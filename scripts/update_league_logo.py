import os

from league_store import load_leagues, save_leagues, find_by_id, find_by_url, parse_logo_id
from logger import Logger


def main():
    league_url = os.getenv("LEAGUE_URL", "").strip()
    id_league = os.getenv("LEAGUE_ID", "").strip()

    logo_id_raw = os.getenv("LEAGUE_LOGO_ID", "").strip()
    if not logo_id_raw:
        Logger.error("LEAGUE_LOGO_ID is required.", fatal=True)
    logo_id = parse_logo_id(logo_id_raw)

    if not league_url and not id_league:
        Logger.error("Either LEAGUE_URL or LEAGUE_ID must be provided.", fatal=True)

    leagues = load_leagues()

    target = find_by_id(leagues, id_league) if id_league else None
    if target is None and league_url:
        target = find_by_url(leagues, league_url)

    if target is None:
        Logger.error("No matching league found in leagues.json.", fatal=True)

    target.setdefault("metadata", {})
    old_logo = target["metadata"].get("leagueLogo")
    target["metadata"]["leagueLogo"] = logo_id

    save_leagues(leagues)
    Logger.success(
        f"Updated leagueLogo for '{target.get('strLeague')}' ({target.get('idLeague')}): "
        f"{old_logo} -> {logo_id}"
    )


if __name__ == "__main__":
    main()