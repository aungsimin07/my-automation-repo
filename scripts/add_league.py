import os
import re
from datetime import datetime, timezone

from api_manager import APIManager, APIError
from league_store import load_leagues, save_leagues, find_by_id, parse_logo_id
from logger import Logger

LEAGUE_URL_PATTERN = re.compile(r"/league/(\d+)-")


def parse_league_id(league_url: str) -> str:
    match = LEAGUE_URL_PATTERN.search(league_url)
    if not match:
        Logger.error(f"Could not parse idLeague from URL: {league_url}", fatal=True)
    return match.group(1)


def write_github_output(key: str, value: str) -> None:
    output_file = os.getenv("GITHUB_OUTPUT")
    if not output_file:
        return
    with open(output_file, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def main():
    league_url = os.getenv("LEAGUE_URL", "").strip()
    logo_id = parse_logo_id(os.getenv("LEAGUE_LOGO_ID", ""))

    if not league_url:
        Logger.error("LEAGUE_URL is required.", fatal=True)

    id_league = parse_league_id(league_url)
    write_github_output("idLeague", id_league)

    leagues = load_leagues()
    if find_by_id(leagues, id_league):
        Logger.warning(f"League {id_league} already exists in leagues.json. Skipping add.")
        return

    manager = APIManager(script_name="add_league.py")
    try:
        data = manager.request("lookupleague.php", {"id": id_league})
    except APIError as e:
        Logger.error(f"Failed to fetch league {id_league}: {e}", fatal=True)

    api_leagues = data.get("leagues") or []
    if not api_leagues or api_leagues[0] is None:
        Logger.error(f"TheSportsDB returned no league for id {id_league}.", fatal=True)

    api_league = api_leagues[0]

    league_object = {
        "idLeague": api_league.get("idLeague", id_league),
        "idAPIfootballv3": api_league.get("idAPIfootballv3"),
        "idCup": api_league.get("idCup"),
        "strLeague": api_league.get("strLeague"),
        "strCurrentSeason": api_league.get("strCurrentSeason"),
        "strComplete": api_league.get("strComplete"),
        "strBadge": api_league.get("strBadge"),
        "strWebsite": api_league.get("strWebsite"),
        "leagueUrl": league_url,
        "metadata": {
            "leagueLogo": logo_id,
            "last_sync_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }

    leagues.append(league_object)
    save_leagues(leagues)
    Logger.success(f"Added league '{league_object['strLeague']}' ({id_league}).")
    Logger.info(f"Total API requests this run: {manager.request_count}")


if __name__ == "__main__":
    main()