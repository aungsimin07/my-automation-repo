import json
import re
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import Logger

CHANNELS_FILE = Path("data/channels.json")

QUALITY_PATTERN = re.compile(r'\b(4K|UHD|FHD|HD|SD|\d{3,4}p)\b', re.IGNORECASE)


def load_channels() -> list:
    if not CHANNELS_FILE.exists():
        return []
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            Logger.warning(f"{CHANNELS_FILE} is corrupt/empty. Treating as empty list.")
            return []


def save_channels(channels: list) -> None:
    CHANNELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(channels, f, indent=2)
    total_urls = sum(len(c.get("urls", [])) for c in channels)
    Logger.success(f"Saved {len(channels)} channel(s), {total_urls} url(s) to {CHANNELS_FILE}")


def find_by_tvg_id(channels: list, tvg_id: str):
    return next((c for c in channels if c.get("attributes", {}).get("tvg-id") == tvg_id), None)


def build_channel_object(duration: int, display_name: str, attributes: dict) -> dict:
    return {
        "duration": duration,
        "displayName": display_name,
        "urls": [],
        "attributes": attributes,
        "metadata": {},
    }


def upsert_channel(channels: list, tvg_id: str, duration: int, display_name: str, attributes: dict) -> dict:
    entry = find_by_tvg_id(channels, tvg_id)
    if entry is None:
        entry = build_channel_object(duration, display_name, attributes)
        channels.append(entry)
    else:
        # refresh channel-level fields from the latest parse (source of truth is the m3u)
        entry["duration"] = duration
        entry["displayName"] = display_name
        entry["attributes"] = attributes
    return entry


def upsert_url(channel: dict, provider: str, url: str, url_fields: dict) -> None:
    urls = channel.setdefault("urls", [])
    for existing in urls:
        if existing.get("url") == url and existing.get("metadata", {}).get("source") == provider:
            existing.update(url_fields)
            return
    urls.append(url_fields)


def remove_stale_playlist_urls(channel: dict, provider: str, keep_urls: set) -> int:
    """Remove URL entries sourced from `provider` whose url isn't in
    keep_urls (i.e. no longer present in that playlist's current m3u).
    URL entries from other sources are always left untouched."""
    before = len(channel.get("urls", []))
    channel["urls"] = [
        u for u in channel.get("urls", [])
        if u.get("metadata", {}).get("source") != provider or u.get("url") in keep_urls
    ]
    return before - len(channel["urls"])


def touch_channel_sync(channel: dict) -> None:
    channel.setdefault("metadata", {})["last_sync_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _channel_sort_key(channel: dict):
    tvg_id = channel.get("attributes", {}).get("tvg-id") or ""
    return (tvg_id.lower(), (channel.get("displayName") or "").lower())


def sort_channels(channels: list) -> None:
    channels.sort(key=_channel_sort_key)


def prune_empty_channels(channels: list) -> int:
    """Drop channels that ended up with zero url sources at all."""
    before = len(channels)
    channels[:] = [c for c in channels if c.get("urls")]
    dropped = before - len(channels)
    if dropped:
        Logger.info(f"Dropped {dropped} channel(s) with no remaining url sources.")
    return dropped


def guess_quality(display_name: str):
    match = QUALITY_PATTERN.search(display_name)
    return match.group(1).upper() if match else None
