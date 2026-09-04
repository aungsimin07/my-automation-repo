import os
import time
from datetime import datetime, timezone

from api_manager import APIManager, APIError
from league_store import load_leagues, save_leagues
from utils.logger import Logger

QUEUE_NAME = "lookupleague"
MAX_RUNTIME_SECONDS = int(os.getenv("MAX_RUNTIME_SECONDS", "240"))


def build_league_object(existing: dict, api_league: dict) -> dict:
    metadata = dict(existing.get("metadata") or {})

    if "leagueLogo" in existing:
        legacy_logo = existing["leagueLogo"]
        if isinstance(legacy_logo, dict):
            Logger.warning(
                f"League {existing.get('idLeague')} has legacy leagueLogo object shape. "
                f"Resetting to null — re-set leagueLogoId manually if needed."
            )
            metadata.setdefault("leagueLogo", None)
        else:
            metadata.setdefault("leagueLogo", legacy_logo)

    metadata["last_sync_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "idLeague": api_league.get("idLeague", existing.get("idLeague")),
        "idAPIfootballv3": api_league.get("idAPIfootballv3", existing.get("idAPIfootballv3")),
        "idCup": api_league.get("idCup", existing.get("idCup")),
        "strLeague": api_league.get("strLeague", existing.get("strLeague")),
        "strCurrentSeason": api_league.get("strCurrentSeason", existing.get("strCurrentSeason")),
        "strComplete": api_league.get("strComplete", existing.get("strComplete")),
        "strBadge": api_league.get("strBadge", existing.get("strBadge")),
        "strWebsite": api_league.get("strWebsite", existing.get("strWebsite")),
        "leagueUrl": existing.get("leagueUrl"),
        "metadata": metadata,
    }


def process_queue(manager: APIManager, leagues: list):
    start = time.monotonic()
    updated_count = 0
    league_by_id = {l["idLeague"]: l for l in leagues}

    while True:
        if time.monotonic() - start > MAX_RUNTIME_SECONDS:
            Logger.warning(f"Runtime budget reached. {manager.queue_length(QUEUE_NAME)} league(s) left queued.")
            break

        batch = manager.dequeue_batch(QUEUE_NAME, 1)
        if not batch:
            Logger.success("Queue drained. All leagues checked this cycle.")
            break

        league_id = batch[0]
        existing = league_by_id.get(league_id)
        if existing is None:
            Logger.warning(f"League {league_id} no longer tracked. Skipping.")
            continue

        try:
            data = manager.request("lookupleague.php", {"id": league_id})
        except APIError as e:
            Logger.error(f"Failed to update league {league_id}: {e}")
            manager.enqueue(QUEUE_NAME, league_id)
            continue

        api_leagues = data.get("leagues") or []
        if not api_leagues or api_leagues[0] is None:
            Logger.warning(f"No API data found for league {league_id}. Keeping existing entry.")
            continue

        updated = build_league_object(existing, api_leagues[0])
        league_by_id[league_id] = updated
        updated_count += 1

    save_leagues(list(league_by_id.values()))
    return updated_count


def main():
    manager = APIManager(script_name="update_league.py")
    leagues = load_leagues()

    if not leagues:
        Logger.warning("No leagues found in leagues.json. Nothing to update.")
        return

    manager.enqueue(QUEUE_NAME, [l["idLeague"] for l in leagues])

    updated_count = process_queue(manager, leagues)
    Logger.success(f"Updated {updated_count} league(s) this run.")
    Logger.info(f"Total API requests this run: {manager.request_count}")


if __name__ == "__main__":
    main()
