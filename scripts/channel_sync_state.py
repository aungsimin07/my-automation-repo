import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils.logger import Logger

SYNC_STATE_FILE = Path("data/channel_sync_state.json")
DEFAULT_MAX_AGE_HOURS = 1


def load_sync_state() -> dict:
    if not SYNC_STATE_FILE.exists():
        return {"synced": {}}
    with open(SYNC_STATE_FILE, "r", encoding="utf-8") as f:
        try:
            state = json.load(f)
        except json.JSONDecodeError:
            Logger.warning(f"{SYNC_STATE_FILE} is corrupt/empty. Resetting sync state.")
            return {"synced": {}}
    state.setdefault("synced", {})
    return state


def save_sync_state(state: dict) -> None:
    SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SYNC_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def is_synced(state: dict, channel_path: str, max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> bool:
    """True if channel_path has been scraped within the last max_age_hours.
    A channel scraped longer ago than that is treated as due for a
    rescrape again — this is what lets late-added schedule entries get
    picked up without waiting for the once-daily full refresh."""
    last_synced = state["synced"].get(channel_path)
    if not last_synced:
        return False
    try:
        last_dt = datetime.strptime(last_synced, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - last_dt
    return age < timedelta(hours=max_age_hours)


def mark_synced(state: dict, channel_path: str, last_synced_at: str) -> None:
    state["synced"][channel_path] = last_synced_at