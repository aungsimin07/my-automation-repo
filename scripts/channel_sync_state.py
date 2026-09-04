import json
from pathlib import Path

from utils.logger import Logger

SYNC_STATE_FILE = Path("data/channel_sync_state.json")


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


def is_synced(state: dict, channel_path: str) -> bool:
    return channel_path in state["synced"]


def mark_synced(state: dict, channel_path: str, last_synced_at: str) -> None:
    state["synced"][channel_path] = last_synced_at
