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
from playlist_store import load_playlists
from utils.logger import Logger

DOWNLOAD_DIR = Path("/tmp/playlist_downloads")

EXTINF_ATTR_PATTERN = re.compile(r'([\w-]+)="([^"]*)"')
EXTINF_DURATION_PATTERN = re.compile(r'^#EXTINF:(-?\d+)')
VLCOPT_UA_PATTERN = re.compile(r'^#EXTVLCOPT:http-user-agent=(.+)$', re.IGNORECASE)

KNOWN_ATTR_KEYS = {
    "tvg-id", "tvg-name", "tvg-logo", "group-title", "tvg-chno",
    "tvg-language", "tvg-country", "tvg-shift", "radio", "catchup",
    "catchup-source", "http-user-agent",
}


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


def parse_extinf_line(line: str):
    duration_match = EXTINF_DURATION_PATTERN.match(line)
    duration = int(duration_match.group(1)) if duration_match else -1
    attributes = {}
    for key, value in EXTINF_ATTR_PATTERN.findall(line):
        attributes[key] = _cast_attr(key, value) if key in KNOWN_ATTR_KEYS else value
    display_name = line.rsplit(",", 1)[-1].strip() if "," in line else ""
    return duration, attributes, display_name


def download_playlist(playlist_id: str, url: str):
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = DOWNLOAD_DIR / f"{playlist_id}.m3u"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        Logger.error(f"Failed to download playlist '{playlist_id}' from {url}: {e}")
        return None
    dest.write_text(resp.text, encoding="utf-8")
    return dest


def parse_playlist_file(file_path: Path) -> list:
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    entries = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith("#EXTINF"):
            i += 1
            continue
        duration, attributes, display_name = parse_extinf_line(line)
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
                "duration": duration, "attributes": attributes,
                "display_name": display_name, "url": stream_url, "user_agent": user_agent,
            })
        i = (j + 1) if stream_url else (i + 1)
    return entries


def sync_playlist(playlist: dict, default_user_agent: str) -> bool:
    playlist_id = playlist.get("id")
    url = playlist.get("url")
    tracked_ids = set(playlist.get("tvgIds", []))

    if not tracked_ids:
        Logger.info(f"Playlist '{playlist_id}' has no tvgIds configured. Skipping.")
        return False

    Logger.info(f"Downloading playlist '{playlist_id}' from {url}")
    local_file = download_playlist(playlist_id, url)
    if local_file is None:
        return False

    parsed_entries = parse_playlist_file(local_file)
    matched_by_tvg_id = {}
    for entry in parsed_entries:
        tvg_id = entry["attributes"].get("tvg-id")
        if tvg_id in tracked_ids:
            matched_by_tvg_id.setdefault(tvg_id, []).append(entry)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    found_count = 0

    # 1) tvg-ids still tracked by this playlist — upsert/refresh, prune stale urls
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
                url_fields = {
                    "url": entry["url"], "provider": playlist_id, "priority": 0, "format": "hls",
                    "metadata": {"source": playlist_id, "last_sync_at": now_iso},
                }
                quality = guess_quality(entry["display_name"])
                if quality:
                    url_fields["quality"] = quality
                ua = entry["user_agent"] or entry["attributes"].get("http-user-agent") or default_user_agent
                if ua:
                    url_fields["httpUserAgent"] = ua
                upsert_url(channel, playlist_id, entry["url"], url_fields)
            touch_channel_sync(channel)
            found_count += 1

        if channel is not None:
            remove_stale_playlist_urls(channel, playlist_id, keep_urls)
            if channel.get("urls"):
                save_channel(channel)
            else:
                delete_channel(tvg_id)

    # 2) tvg-ids NO LONGER tracked by this playlist — strip this provider's
    #    urls wherever they still sit on disk; delete the file if nothing's left
    for tvg_id in list_all_tvg_ids():
        if tvg_id in tracked_ids:
            continue
        channel = load_channel(tvg_id)
        if channel is None:
            continue
        if strip_provider_urls(channel, playlist_id):
            if channel.get("urls"):
                save_channel(channel)
            else:
                delete_channel(tvg_id)
                Logger.info(f"Removed orphaned channel '{tvg_id}' (no urls left, tvg-id dropped from '{playlist_id}').")

    missing = tracked_ids - set(matched_by_tvg_id.keys())
    if missing:
        Logger.warning(f"Playlist '{playlist_id}': {len(missing)} tvg-id(s) not found in source: {', '.join(sorted(missing))}")

    Logger.success(f"Playlist '{playlist_id}': matched {found_count}/{len(tracked_ids)} tvg-id(s).")
    return True


def main():
    playlists = load_playlists()
    if not playlists:
        Logger.warning("No playlists found in playlists.json. Nothing to sync.")
        return

    default_user_agent = os.getenv("DEFAULT_HTTP_USER_AGENT", "").strip() or None

    any_synced = False
    for playlist in playlists:
        if sync_playlist(playlist, default_user_agent):
            any_synced = True

    if not any_synced:
        Logger.warning("No playlist downloads succeeded this run.")


if __name__ == "__main__":
    main()
