import json
import re
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import Logger

CHANNELS_DIR = Path("data/channels")
QUALITY_PATTERN = re.compile(r'\b(4K|UHD|FHD|HD|SD|\d{3,4}p)\b', re.IGNORECASE)
MANUAL_SOURCE = "manual"

_UNSAFE_FILENAME_CHARS = re.compile(r'[\/\\:*?"<>|]')


def _filename_for(tvg_id: str) -> str:
    safe = _UNSAFE_FILENAME_CHARS.sub("_", tvg_id.strip())
    return f"{safe}.json"


def _path_for(tvg_id: str) -> Path:
    return CHANNELS_DIR / _filename_for(tvg_id)


def channel_exists(tvg_id: str) -> bool:
    return _path_for(tvg_id).exists()


def load_channel(tvg_id: str):
    path = _path_for(tvg_id)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            Logger.warning(f"{path} is corrupt/empty. Treating as missing.")
            return None


def save_channel(channel: dict) -> None:
    tvg_id = channel.get("attributes", {}).get("tvg-id")
    if not tvg_id:
        Logger.error("Cannot save a channel without attributes.tvg-id.", fatal=True)
    CHANNELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_path_for(tvg_id), "w", encoding="utf-8") as f:
        json.dump(channel, f, indent=2)


def delete_channel(tvg_id: str) -> bool:
    path = _path_for(tvg_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def list_all_tvg_ids() -> list:
    """Scan /channels/ and return the tvg-id recorded INSIDE each file
    (not the filename) — a file's own attributes.tvg-id is the source
    of truth; the filename is just a sanitized on-disk lookup key."""
    if not CHANNELS_DIR.exists():
        return []
    ids = []
    for path in sorted(CHANNELS_DIR.glob("*.json")):
        with open(path, "r", encoding="utf-8") as f:
            try:
                channel = json.load(f)
            except json.JSONDecodeError:
                Logger.warning(f"{path} is corrupt/empty. Skipping.")
                continue
        tvg_id = channel.get("attributes", {}).get("tvg-id")
        if tvg_id:
            ids.append(tvg_id)
    return ids


def build_channel_object(duration: int, display_name: str, attributes: dict) -> dict:
    return {
        "duration": duration,
        "displayName": display_name,
        "urls": [],
        "attributes": attributes,
        "metadata": {},
    }


def upsert_channel_fields(channel: dict, duration: int, display_name: str, attributes: dict) -> None:
    channel["duration"] = duration
    channel["displayName"] = display_name
    channel["attributes"] = attributes


def upsert_url(channel: dict, provider: str, url: str, url_fields: dict) -> None:
    urls = channel.setdefault("urls", [])
    for existing in urls:
        if existing.get("url") == url and existing.get("metadata", {}).get("source") == provider:
            existing.update(url_fields)
            return
    urls.append(url_fields)


def remove_stale_playlist_urls(channel: dict, provider: str, keep_urls: set) -> int:
    """tvg-id IS still tracked by `provider` — drop only that provider's
    urls no longer present in its current m3u."""
    before = len(channel.get("urls", []))
    channel["urls"] = [
        u for u in channel.get("urls", [])
        if u.get("metadata", {}).get("source") != provider or u.get("url") in keep_urls
    ]
    return before - len(channel["urls"])


def strip_provider_urls(channel: dict, provider: str) -> int:
    """tvg-id is NO LONGER tracked by `provider` at all — drop every url
    that provider ever contributed to this channel. Manual and other
    providers' urls are untouched."""
    before = len(channel.get("urls", []))
    channel["urls"] = [u for u in channel.get("urls", []) if u.get("metadata", {}).get("source") != provider]
    return before - len(channel["urls"])


def touch_channel_sync(channel: dict) -> None:
    channel.setdefault("metadata", {})["last_sync_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def guess_quality(display_name: str):
    match = QUALITY_PATTERN.search(display_name)
    return match.group(1).upper() if match else None
