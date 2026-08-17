import json
from pathlib import Path

from utils.logger import Logger

PLAYLISTS_FILE = Path("data/playlists.json")


def load_playlists() -> list:
    if not PLAYLISTS_FILE.exists():
        return []
    with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            Logger.warning(f"{PLAYLISTS_FILE} is corrupt/empty. Treating as empty list.")
            return []


def save_playlists(playlists: list) -> None:
    PLAYLISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    playlists_sorted = sorted(playlists, key=lambda p: (p.get("id") or "").lower())
    with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
        json.dump(playlists_sorted, f, indent=2)
    Logger.success(f"Saved {len(playlists_sorted)} playlist(s) to {PLAYLISTS_FILE}")


def find_by_id(playlists: list, playlist_id: str):
    return next((p for p in playlists if p.get("id") == playlist_id), None)


def parse_csv_list(raw: str) -> list:
    """Parse a comma-separated string into a deduped, order-preserving
    list of trimmed, non-empty values."""
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
