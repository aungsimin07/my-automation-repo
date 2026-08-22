import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from channel_store import (
    load_channel, save_channel, delete_channel, list_all_tvg_ids,
    build_channel_object, upsert_channel_fields, upsert_url,
    remove_stale_playlist_urls, strip_provider_urls, touch_channel_sync, guess_quality,
)
from utils.logger import Logger

DOWNLOAD_DIR = Path("/tmp/playlist_downloads")
PROVIDER_ID = "playlist-sync"

EXTINF_ATTR_PATTERN = re.compile(r'([\w-]+)="([^"]*)"')
EXTINF_DURATION_PATTERN = re.compile(r'^#EXTINF:(-?\d+)')
VLCOPT_UA_PATTERN = re.compile(r'^#EXTVLCOPT:http-user-agent=(.+)$', re.IGNORECASE)

KNOWN_ATTR_KEYS = {
    "tvg-id", "tvg-name", "tvg-logo", "group-title", "tvg-chno",
    "tvg-language", "tvg-country", "tvg-shift", "radio", "catchup",
    "catchup-source", "http-user-agent",
}

# Reserved, non-standard EXTINF attributes we define ourselves, describing
# the URL that follows THIS #EXTINF block specifically (not the channel as
# a whole) — so a channel with 2 urls can tag each with its own quality.
RESERVED_URL_KEYS = {"quality", "priority", "format"}


def parse_csv_list(raw: str) -> list:
    if not raw:
        return []
    seen = set()
    result = []
    for part in raw.split(","):
        val = part.strip()
        if val and val not in seen:
            seen.add(val)
            result.append(val)
    return result


def _cast_attr(key: str, value: str):
    if key == "tvg-chno":
        return int(value) if value.isdigit() else None
    if key == "tvg-shift":
        try:
            return int(value)
        except ValueError:
            return None
    if key == "radio":
        return value.strip().lower() in ("true", "1")
    return value if value != "" else None


def parse_int(raw, default=0):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def parse_extinf_line(line: str):
    duration_match = EXTINF_DURATION_PATTERN.match(line)
    duration = int(duration_match.group(1)) if duration_match else -1

    attributes = {}
    url_overrides = {}
    for key, value in EXTINF_ATTR_PATTERN.findall(line):
        if key in RESERVED_URL_KEYS:
            url_overrides[key] = value
        elif key in KNOWN_ATTR_KEYS:
            attributes[key] = _cast_attr(key, value)
        else:
            attributes[key] = value  # unrecognized key, still passed through as channel-level

    display_name = line.rsplit(",", 1)[-1].strip() if "," in line else ""
    return duration, attributes, display_name, url_overrides


def download_playlist(url: str):
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = DOWNLOAD_DIR / "playlist.m3u"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        Logger.error(f"Failed to download playlist from {url}: {e}")
        return None
    dest.write_text(resp.text, encoding="utf-8")
    return dest


def parse_playlist_file(file_path: Path) -> list:
    """Each #EXTINF opens one entry and consumes exactly the next
    non-comment line as its url. A channel with multiple urls MUST
    repeat the full #EXTINF (+ optional #EXTVLCOPT) block once per url."""
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    entries = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith("#EXTINF"):
            i += 1
            continue
        duration, attributes, display_name, url_overrides = parse_extinf_line(line)
        j = i + 1
        stream_url = None
        user_agent = None
        while j < len(lines):
            candidate = lines[j].strip()
            if not candidate:
                j += 1
                continue
            if candidate.startswith("#"):
                ua_match = VLCOPT_UA_PATTERN.match(candidate)
                if ua_match:
                    user_agent = ua_match.group(1).strip()
                j += 1
                continue
            stream_url = candidate
            break
        if stream_url:
            entries.append({
                "duration": duration, "attributes": attributes, "display_name": display_name,
                "url": stream_url, "user_agent": user_agent, "url_overrides": url_overrides,
            })
        i = (j + 1) if stream_url else (i + 1)
    return entries


def main():
    playlist_url = os.getenv("PLAYLIST_URL", "").strip()
    tracked_ids = set(parse_csv_list(os.getenv("TVG_IDS", "")))
    default_user_agent = os.getenv("DEFAULT_HTTP_USER_AGENT", "").strip() or None

    if not playlist_url:
        Logger.error("PLAYLIST_URL is required.", fatal=True)
    if not tracked_ids:
        Logger.warning("TVG_IDS is empty. Nothing to sync.")
        return

    Logger.info(f"Downloading playlist from {playlist_url}")
    local_file = download_playlist(playlist_url)
    if local_file is None:
        return

    parsed_entries = parse_playlist_file(local_file)
    matched_by_tvg_id = {}
    for entry in parsed_entries:
        tvg_id = entry["attributes"].get("tvg-id")
        if tvg_id in tracked_ids:
            matched_by_tvg_id.setdefault(tvg_id, []).append(entry)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    found_count = 0

    for tvg_id in tracked_ids:
        entries = matched_by_tvg_id.get(tvg_id, [])
        keep_urls = {e["url"] for e in entries}
        channel = load_channel(tvg_id)

        if entries:
            first = entries[0]
            if channel is None:
                channel = build_channel_object(first["duration"], first["display_name"], first["attributes"])
            else:
                upsert_channel_fields(channel, first["duration"], first["display_name"], first["attributes"])

            for entry in entries:
                overrides = entry["url_overrides"]
                url_fields = {
                    "url": entry["url"],
                    "provider": PROVIDER_ID,
                    "priority": parse_int(overrides.get("priority"), default=0),
                    "format": overrides.get("format") or "hls",
                    "metadata": {"source": PROVIDER_ID, "last_sync_at": now_iso},
                }
                quality = overrides.get("quality") or guess_quality(entry["display_name"])
                if quality:
                    url_fields["quality"] = quality
                ua = entry["user_agent"] or entry["attributes"].get("http-user-agent") or default_user_agent
                if ua:
                    url_fields["httpUserAgent"] = ua
                upsert_url(channel, PROVIDER_ID, entry["url"], url_fields)
            touch_channel_sync(channel)
            found_count += 1

        if channel is not None:
            remove_stale_playlist_urls(channel, PROVIDER_ID, keep_urls)
            if channel.get("urls"):
                save_channel(channel)
            else:
                delete_channel(tvg_id)

    for tvg_id in list_all_tvg_ids():
        if tvg_id in tracked_ids:
            continue
        channel = load_channel(tvg_id)
        if channel is None:
            continue
        if strip_provider_urls(channel, PROVIDER_ID):
            if channel.get("urls"):
                save_channel(channel)
            else:
                delete_channel(tvg_id)
                Logger.info(f"Removed orphaned channel '{tvg_id}' (no urls left, tvg-id dropped from TVG_IDS).")

    missing = tracked_ids - set(matched_by_tvg_id.keys())
    if missing:
        Logger.warning(f"{len(missing)} tvg-id(s) not found in playlist source: {', '.join(sorted(missing))}")

    Logger.success(f"Matched {found_count}/{len(tracked_ids)} tvg-id(s) from playlist.")


if __name__ == "__main__":
    main()