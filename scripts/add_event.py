import os

from api_manager import APIManager, APIError
from event_store import (
    load_events, save_events, build_event_object, build_league_entry_from_tracked,
    fetch_league_entry_via_api, get_or_create_league_entry, upsert_event,
    sort_leagues, prune_empty_leagues,
)
from league_store import load_leagues, parse_logo_id
from logger import Logger


def main():
    event_id = os.getenv("EVENT_ID", "").strip()
    logo_id = parse_logo_id(os.getenv("LEAGUE_LOGO_ID", ""))

    if not event_id:
        Logger.error("EVENT_ID is required.", fatal=True)

    manager = APIManager(script_name="add_event.py")
    try:
        resp = manager.request("lookupevent.php", {"id": event_id})
    except APIError as e:
        Logger.error(f"Failed to fetch event {event_id}: {e}", fatal=True)

    raw_events = resp.get("events") or []
    if not raw_events or raw_events[0] is None:
        Logger.error(f"TheSportsDB returned no event for id {event_id}.", fatal=True)

    raw_event = raw_events[0]
    event_obj = build_event_object(raw_event, source="manual")
    if event_obj is None:
        Logger.error(
            f"Event {event_id} has status '{raw_event.get('strStatus')}', not NS. "
            f"Only scheduled (NS) events can be added.",
            fatal=True,
        )

    id_league = raw_event.get("idLeague")
    data = load_events()
    leagues_by_id = {l["idLeague"]: l for l in load_leagues()}
    tracked_league = leagues_by_id.get(id_league)

    if tracked_league:
        if logo_id is not None:
            Logger.warning(
                f"League {id_league} is already tracked in leagues.json — ignoring "
                f"provided leagueLogoId. Use the Update League Logo workflow instead."
            )
        league_entry = get_or_create_league_entry(
            data, id_league, lambda: build_league_entry_from_tracked(tracked_league)
        )
    else:
        league_entry = next((l for l in data["leagues"] if l.get("idLeague") == id_league), None)
        if league_entry is None:
            league_entry = fetch_league_entry_via_api(manager, id_league, logo_id)
            if league_entry is None:
                Logger.error(f"Could not fetch league data for id {id_league}. Cannot add event.", fatal=True)
            data["leagues"].append(league_entry)
        elif logo_id is not None:
            league_entry.setdefault("metadata", {})["leagueLogo"] = logo_id

    upsert_event(league_entry, event_obj)
    sort_leagues(data)
    prune_empty_leagues(data)  # safety net; harmless here since we just added an event
    save_events(data)

    Logger.success(f"Added event '{raw_event.get('strEvent')}' ({event_id}) to league '{league_entry.get('strLeague')}'.")
    Logger.info(f"Total API requests this run: {manager.request_count}")


if __name__ == "__main__":
    main()