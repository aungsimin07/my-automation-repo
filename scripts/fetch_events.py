import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from api_manager import APIManager, APIError
from event_scraper import scrape_fallback_event_ids
from event_store import (
    load_events, save_events, build_event_object, build_league_entry_from_tracked,
    fetch_league_entry_via_api, get_or_create_league_entry, upsert_event,
    sort_leagues, prune_empty_leagues, prune_to_dates, prune_unreferenced_channels,
)
from league_store import load_leagues
from sync_state import load_sync_state, save_sync_state, is_synced, mark_synced
from utils.logger import Logger

TIMEZONE = ZoneInfo("Asia/Yangon")
EVENTSDAY_QUEUE = "eventsday"
LOOKUPEVENT_QUEUE = "lookupevent"
MAX_RUNTIME_SECONDS = int(os.getenv("MAX_RUNTIME_SECONDS", "240"))


def get_target_dates() -> list:
    today = datetime.now(TIMEZONE).date()
    tomorrow = today + timedelta(days=1)
    return [today.isoformat(), tomorrow.isoformat()]


def _is_complete(league: dict) -> bool:
    return (league.get("strComplete") or "").strip().lower() == "yes"


def _skip_complete_enabled() -> bool:
    raw = os.getenv("SKIP_COMPLETE_LEAGUES", "true").strip().lower()
    return raw in ("1", "true", "yes")


def process_eventsday_queue(manager: APIManager, data: dict, leagues_by_id: dict, sync_state: dict, start: float) -> int:
    processed = 0
    while True:
        if time.monotonic() - start > MAX_RUNTIME_SECONDS:
            Logger.warning(f"Runtime budget reached. {manager.queue_length(EVENTSDAY_QUEUE)} eventsday task(s) left queued.")
            break

        batch = manager.dequeue_batch(EVENTSDAY_QUEUE, 1)
        if not batch:
            break

        task = batch[0]
        id_league, date = task["idLeague"], task["date"]
        league = leagues_by_id.get(id_league)
        if league is None:
            Logger.warning(f"League {id_league} no longer tracked. Skipping.")
            continue

        league_entry = get_or_create_league_entry(data, id_league, lambda: build_league_entry_from_tracked(league))

        try:
            resp = manager.request("eventsday.php", {"d": date, "l": id_league})
        except APIError as e:
            Logger.error(f"eventsday.php failed for league {id_league} on {date}: {e}")
            manager.enqueue(EVENTSDAY_QUEUE, task)
            continue  # not marked synced — will retry next run

        raw_events = resp.get("events") or []
        known_ids = set()
        for raw_event in raw_events:
            if raw_event.get("idEvent"):
                known_ids.add(raw_event["idEvent"])
            event_obj = build_event_object(raw_event, source="api_scheduled")
            if event_obj:
                upsert_event(league_entry, event_obj)

        # eventsday.php caps at 3 events/league/day — scrape the league page
        # for anything beyond that; only ids are scraped, not data.
        league_url = league.get("leagueUrl")
        if league_url:
            extra_ids = scrape_fallback_event_ids(league_url, date, known_ids)
            if extra_ids:
                manager.enqueue(LOOKUPEVENT_QUEUE, extra_ids)

        mark_synced(sync_state, id_league, date)
        processed += 1

    return processed


def process_lookupevent_queue(manager: APIManager, data: dict, leagues_by_id: dict, start: float) -> int:
    processed = 0
    while True:
        if time.monotonic() - start > MAX_RUNTIME_SECONDS:
            Logger.warning(f"Runtime budget reached. {manager.queue_length(LOOKUPEVENT_QUEUE)} event lookup(s) left queued.")
            break

        batch = manager.dequeue_batch(LOOKUPEVENT_QUEUE, 1)
        if not batch:
            break

        event_id = batch[0]
        try:
            resp = manager.request("lookupevent.php", {"id": event_id})
        except APIError as e:
            Logger.error(f"lookupevent.php failed for event {event_id}: {e}")
            manager.enqueue(LOOKUPEVENT_QUEUE, event_id)
            continue

        raw_events = resp.get("events") or []
        processed += 1
        if not raw_events or raw_events[0] is None:
            Logger.warning(f"No event data found for id {event_id}.")
            continue

        raw_event = raw_events[0]
        event_obj = build_event_object(raw_event, source="scraped_lookup")
        if not event_obj:
            continue  # not NS status (or missing fields) — intentionally dropped

        id_league = raw_event.get("idLeague")
        league = leagues_by_id.get(id_league)
        if league:
            league_entry = get_or_create_league_entry(data, id_league, lambda: build_league_entry_from_tracked(league))
        else:
            league_entry = next((l for l in data["leagues"] if l.get("idLeague") == id_league), None)
            if league_entry is None:
                league_entry = fetch_league_entry_via_api(manager, id_league)
                if league_entry is None:
                    Logger.warning(f"Could not fetch league data for untracked league {id_league}. Dropping event {event_id}.")
                    continue
                data["leagues"].append(league_entry)

        upsert_event(league_entry, event_obj)

    return processed


def main():
    manager = APIManager(script_name="fetch_events.py")
    leagues = load_leagues()
    if not leagues:
        Logger.warning("No leagues found in leagues.json. Nothing to fetch.")
        return

    leagues_by_id = {l["idLeague"]: l for l in leagues}  # full map, unfiltered — needed for lookups
    dates = get_target_dates()

    active_leagues = leagues
    if _skip_complete_enabled():
        active_leagues = [l for l in leagues if not _is_complete(l)]
        skipped = len(leagues) - len(active_leagues)
        if skipped:
            Logger.info(f"Skipping {skipped} league(s) with strComplete=yes (SKIP_COMPLETE_LEAGUES=true).")

    only_league_id = os.getenv("ONLY_LEAGUE_ID", "").strip() or None
    if only_league_id:
        active_leagues = [l for l in active_leagues if l["idLeague"] == only_league_id]
        Logger.info(f"ONLY_LEAGUE_ID set — restricting fetch to league {only_league_id}.")

    data = load_events()
    prune_to_dates(data, dates)

    sync_state = load_sync_state(dates)

    tasks = []
    skipped_synced = 0
    for l in active_leagues:
        for d in dates:
            if is_synced(sync_state, l["idLeague"], d):
                skipped_synced += 1
                continue
            tasks.append({"idLeague": l["idLeague"], "date": d})
    if skipped_synced:
        Logger.info(f"Skipping {skipped_synced} league/date pair(s) already synced for this window.")

    manager.enqueue(EVENTSDAY_QUEUE, tasks)

    start = time.monotonic()
    eventsday_processed = process_eventsday_queue(manager, data, leagues_by_id, sync_state, start)
    lookupevent_processed = process_lookupevent_queue(manager, data, leagues_by_id, start)

    sort_leagues(data)
    prune_empty_leagues(data)
    prune_unreferenced_channels(data)

    save_events(data)
    save_sync_state(sync_state)
    Logger.success(f"Processed {eventsday_processed} eventsday task(s), {lookupevent_processed} lookup(s) this run.")
    Logger.info(f"Total API requests this run: {manager.request_count}")


if __name__ == "__main__":
    main()