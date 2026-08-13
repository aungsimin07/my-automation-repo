import os

from league_store import load_leagues, save_leagues, find_by_id, find_by_url
from logger import Logger


def main():
    league_url = os.getenv("LEAGUE_URL", "").strip()
    id_league = os.getenv("LEAGUE_ID", "").strip()

    if not league_url and not id_league:
        Logger.error("Either LEAGUE_URL or LEAGUE_ID must be provided.", fatal=True)

    leagues = load_leagues()

    target = find_by_id(leagues, id_league) if id_league else None
    if target is None and league_url:
        target = find_by_url(leagues, league_url)

    if target is None:
        Logger.error("No matching league found in leagues.json.", fatal=True)

    leagues = [l for l in leagues if l is not target]
    save_leagues(leagues)
    Logger.success(f"Removed league '{target.get('strLeague')}' ({target.get('idLeague')}).")


if __name__ == "__main__":
    main()