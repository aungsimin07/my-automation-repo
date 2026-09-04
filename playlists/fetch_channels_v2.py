import os
import re
import json
from pathlib import Path

import requests

from utils.logger import Logger

DOWNLOAD_DIR = Path("/tmp/playlist_downloads_v2")
CHANNELS_FILE = Path("data/channels_v2.json")
CHANNEL_MAP_FILE = Path("data/channel_tvgid_map.json")

EXTINF_ATTR_PATTERN = re.compile(r'([\w-]+)="([^"]*)"')
EXTINF_DURATION_PATTERN = re.compile(r'^#EXTINF:(-?[\d.]+)')
OPT_LINE_PATTERN = re.compile(r'^#(EXTVLCOPT|EXTYTVOPT|EXTYTVMETA):([\w-]+)=(.*)$')

VLC_KEY_MAP = {
    "http-user-agent": "httpUserAgent",
    "http-referrer": "httpReferrer",
}
QUALITY_KEY_MAP = {
    "quality-resolution": "resolution",
    "quality-width": "width",
    "quality-height": "height",
    "quality-frame-rate": "frameRate",
    "quality-video-bitrate": "videoBitrateKbps",
    "quality-audio-bitrate": "audioBitrateKbps",
    "quality-video-codec": "videoCodec",
    "quality-audio-codec": "audioCodec",
    "quality-label": "label",
}
QUALITY_INT_KEYS = {"width", "height", "videoBitrateKbps", "audioBitrateKbps"}


def parse_number(raw):
    try:
        val = float(raw)
        return int(val) if val.is_integer() else val
    except (TypeError, ValueError):
        return None


def parse_extinf_line(line: str):
    duration_match = EXTINF_DURATION_PATTERN.match(line)
    duration = parse_number(duration_match.group(1)) if duration_match else -1

    raw_attrs = dict(EXTINF_ATTR_PATTERN.findall(line))
    title = line.rsplit(",", 1)[-1].strip() if "," in line else ""

    tvg = {}
    if raw_attrs.get("tvg-id"):
        tvg["id"] = raw_attrs["tvg-id"]
    if raw_attrs.get("tvg-logo"):
        tvg["logo"] = raw_attrs["tvg-logo"]
    if raw_attrs.get("tvg-language"):
        tvg["language"] = raw_attrs["tvg-language"]
    if raw_attrs.get("tvg-country"):
        tvg["country"] = raw_attrs["tvg-country"]
    if raw_attrs.get("tvg-shift"):
        shift = parse_number(raw_attrs["tvg-shift"])
        if shift is not None:
            tvg["shift"] = shift
    if raw_attrs.get("tvg-chno") and raw_attrs["tvg-chno"].isdigit():
        tvg["chno"] = int(raw_attrs["tvg-chno"])

    entry = {"duration": duration, "title": title}
    if tvg:
        entry["tvg"] = tvg
    if raw_attrs.get("radio", "").strip().lower() in ("true", "1"):
        entry["radio"] = True
    if raw_attrs.get("group-title"):
        entry["groupTitle"] = raw_attrs["group-title"]

    if raw_attrs.get("catchup"):
        catchup = {"type": raw_attrs["catchup"]}
        if raw_attrs.get("catchup-days", "").isdigit():
            catchup["days"] = int(raw_attrs["catchup-days"])
        if raw_attrs.get("catchup-source"):
            catchup["source"] = raw_attrs["catchup-source"]
        entry["catchup"] = catchup

    if raw_attrs.get("timeshift"):
        ts = parse_number(raw_attrs["timeshift"])
        if ts is not None:
            entry["timeshift"] = ts

    return entry


def apply_option_line(entry: dict, tag: str, key: str, value: str):
    if tag == "EXTVLCOPT":
        vlc = entry.setdefault("options", {}).setdefault("vlc", {})
        if key in VLC_KEY_MAP:
            vlc[VLC_KEY_MAP[key]] = value
        elif key == "network-caching" and value.isdigit():
            vlc["networkCaching"] = int(value)

    elif tag == "EXTYTVOPT":
        custom = entry.setdefault("options", {}).setdefault("custom", {})
        if key == "http-origin":
            custom["httpOrigin"] = value
        elif key == "http-cookie":
            custom["httpCookie"] = value
        elif key == "http-header" and ":" in value:
            name, _, val = value.partition(":")
            custom.setdefault("httpHeaders", []).append({"name": name.strip(), "value": val.strip()})

    elif tag == "EXTYTVMETA":
        if key == "channel-path":
            entry["channelPath"] = value
        elif key in QUALITY_KEY_MAP:
            quality = entry.setdefault("quality", {})
            mapped_key = QUALITY_KEY_MAP[key]
            if mapped_key in QUALITY_INT_KEYS and value.isdigit():
                quality[mapped_key] = int(value)
            elif mapped_key == "frameRate":
                num = parse_number(value)
                if num is not None:
                    quality[mapped_key] = num
            else:
                quality[mapped_key] = value


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


def parse_playlist_file(file_path: Path, default_user_agent: str) -> list:
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    entries = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith("#EXTINF"):
            i += 1
            continue

        entry = parse_extinf_line(line)
        j = i + 1
        stream_url = None
        while j < len(lines):
            candidate = lines[j].strip()
            if not candidate:
                j += 1
                continue
            if candidate.startswith("#"):
                opt_match = OPT_LINE_PATTERN.match(candidate)
                if opt_match:
                    tag, key, value = opt_match.groups()
                    apply_option_line(entry, tag, key, value)
                j += 1
                continue
            stream_url = candidate
            break

        if stream_url:
            entry["url"] = stream_url
            if default_user_agent:
                vlc = entry.setdefault("options", {}).setdefault("vlc", {})
                vlc.setdefault("httpUserAgent", default_user_agent)
            entries.append(entry)

        i = (j + 1) if stream_url else (i + 1)

    return entries


def save_channels(entries: list) -> None:
    CHANNELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    Logger.success(f"Saved {len(entries)} channel entr(y/ies) to {CHANNELS_FILE}")

def build_tvgid_channelpath_map(entries: list) -> dict:
    """tvg-id -> sorted list of distinct channelPaths seen for it.
    A tvg-id can appear on multiple #EXTINF blocks (different urls,
    same logical channel) — each with a potentially different
    channelPath, so this is 1:many, not 1:1."""
    mapping = {}
    for entry in entries:
        tvg_id = entry.get("tvg", {}).get("id")
        channel_path = entry.get("channelPath")
        if not tvg_id or not channel_path:
            continue
        paths = mapping.setdefault(tvg_id, [])
        if channel_path not in paths:
            paths.append(channel_path)
    for tvg_id in mapping:
        mapping[tvg_id].sort()
    return mapping


def save_channel_map(mapping: dict) -> None:
    CHANNEL_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CHANNEL_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2)
    Logger.success(f"Saved {len(mapping)} tvg-id -> channelPath mapping(s) to {CHANNEL_MAP_FILE}")


def main():
    playlist_url = os.getenv("PLAYLIST_URLV2", "").strip()
    default_user_agent = os.getenv("DEFAULT_HTTP_USER_AGENT", "").strip() or None

    if not playlist_url:
        Logger.error("PLAYLIST_URL is required.", fatal=True)

    Logger.info(f"Downloading playlist from {playlist_url}")
    local_file = download_playlist(playlist_url)
    if local_file is None:
        return

    entries = parse_playlist_file(local_file, default_user_agent)

    missing_tvg_id = sum(1 for e in entries if not e.get("tvg", {}).get("id"))
    if missing_tvg_id:
        Logger.warning(f"{missing_tvg_id} entr(y/ies) have no tvg-id set.")

    save_channels(entries)

    tvgid_map = build_tvgid_channelpath_map(entries)
    save_channel_map(tvgid_map)


if __name__ == "__main__":
    main()
