import json
from pathlib import Path

from utils.logger import Logger

LEAGUES_FILE = Path("data/leagues.json")


def load_leagues() -> list:
    if not LEAGUES_FILE.exists():
        return []
    with open(LEAGUES_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            Logger.warning(f"{LEAGUES_FILE} is corrupt/empty. Treating as empty list.")
            return []


def _sort_key(league: dict):
    raw = league.get("idAPIfootballv3")
    try:
        return (0, int(raw))
    except (TypeError, ValueError):
        return (1, str(raw or ""))


def save_leagues(leagues: list) -> None:
    LEAGUES_FILE.parent.mkdir(parents=True, exist_ok=True)
    leagues_sorted = sorted(leagues, key=_sort_key)
    with open(LEAGUES_FILE, "w", encoding="utf-8") as f:
        json.dump(leagues_sorted, f, indent=2)
    Logger.success(f"Saved {len(leagues_sorted)} league(s) to {LEAGUES_FILE}")


def find_by_id(leagues: list, id_league: str):
    return next((l for l in leagues if l.get("idLeague") == id_league), None)


def find_by_url(leagues: list, league_url: str):
    return next((l for l in leagues if l.get("leagueUrl") == league_url), None)


def parse_logo_id(raw: str):
    """Parse an optional integer logo id. Returns None if blank,
    fatally errors if present but not a valid integer."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        Logger.error(f"leagueLogoId must be an integer, got: '{raw}'", fatal=True)