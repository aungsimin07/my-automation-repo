import json
from pathlib import Path

from utils.logger import Logger

SYNC_STATE_FILE = Path("data/sync_state.json")


def load_sync_state(dates: list) -> dict:
    """Load sync state for the current date window. If the window has
    moved on (today/tomorrow changed) since the last run, reset —
    everything needs re-syncing for the new window."""
    if not SYNC_STATE_FILE.exists():
        return {"dates": dates, "synced": []}

    with open(SYNC_STATE_FILE, "r", encoding="utf-8") as f:
        try:
            state = json.load(f)
        except json.JSONDecodeError:
            Logger.warning(f"{SYNC_STATE_FILE} is corrupt/empty. Resetting sync state.")
            return {"dates": dates, "synced": []}

    if state.get("dates") != dates:
        Logger.info("Date window changed. Resetting sync state.")
        return {"dates": dates, "synced": []}

    state.setdefault("synced", [])
    return state


def save_sync_state(state: dict) -> None:
    SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SYNC_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def is_synced(state: dict, id_league: str, date: str) -> bool:
    return {"idLeague": id_league, "date": date} in state["synced"]


def mark_synced(state: dict, id_league: str, date: str) -> None:
    entry = {"idLeague": id_league, "date": date}
    if entry not in state["synced"]:
        state["synced"].append(entry)
