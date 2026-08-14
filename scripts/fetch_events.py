import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from api_manager import APIManager, APIError
from event_scraper import scrape_fallback_event_ids
from event_store import (
    load_events, save_events, build_event_object, build_league_entry_from_tracked,
    build_league_entry_from_event, get_or_create_league_entry, upsert_event,
    sort_league_events, prune_to_dates,
)
from league_store import load_leagues
from logger import Logger

TIMEZONE = ZoneInfo("Asia/Yangon")
EVENTSDAY_QUEUE = "eventsday"
LOOKUPEVENT_QUEUE = "lookupevent"
MAX_RUNTIME_SECONDS = int(os.getenv("MAX_RUNTIME_SECONDS", "240"))


def get_target_dates() -> list:
    today = datetime.now(TIMEZONE).date()
    tomorrow = today + timedelta(days=1)
    return [today.isoformat(), tomorrow.isoformat()]


def process_eventsday_queue(manager: APIManager, data: dict, leagues_by_id: dict, start: float) -> int:
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
            continue

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
            league_entry = get_or_create_league_entry(data, id_league, lambda: build_league_entry_from_event(raw_event))

        upsert_event(league_entry, event_obj)

    return processed


def main():
    manager = APIManager(script_name="fetch_events.py")
    leagues = load_leagues()
    if not leagues:
        Logger.warning("No leagues found in leagues.json. Nothing to fetch.")
        return

    leagues_by_id = {l["idLeague"]: l for l in leagues}
    dates = get_target_dates()

    data = load_events()
    prune_to_dates(data, dates)

    tasks = [{"idLeague": l["idLeague"], "date": d} for l in leagues for d in dates]
    manager.enqueue(EVENTSDAY_QUEUE, tasks)

    start = time.monotonic()
    eventsday_processed = process_eventsday_queue(manager, data, leagues_by_id, start)
    lookupevent_processed = process_lookupevent_queue(manager, data, leagues_by_id, start)

    for league_entry in data["leagues"]:
        sort_league_events(league_entry)

    save_events(data)
    Logger.success(f"Processed {eventsday_processed} eventsday task(s), {lookupevent_processed} lookup(s) this run.")
    Logger.info(f"Total API requests this run: {manager.request_count}")


if __name__ == "__main__":
    main()
