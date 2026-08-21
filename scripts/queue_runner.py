import os
import time

from api_manager import APIManager, APIError
from event_store import (
    load_events, save_events, sort_leagues, prune_empty_leagues,
    needs_status_check, remove_finished_events, build_event_object_any_status, upsert_event,
    resync_channel_links, refresh_tracked_league_fields,
)
from fetch_events import (
    EVENTSDAY_QUEUE, LOOKUPEVENT_QUEUE, get_target_dates,
    process_eventsday_queue, process_lookupevent_queue,
)
from league_store import load_leagues, save_leagues
from status_sync import run_status_sync
from sync_state import load_sync_state, save_sync_state
from update_league import build_league_object, QUEUE_NAME as LOOKUPLEAGUE_QUEUE
from utils.logger import Logger

MAX_RUNTIME_SECONDS = int(os.getenv("MAX_RUNTIME_SECONDS", "240"))


def run_lookupleague_queue(manager: APIManager, start: float) -> int:
    leagues = load_leagues()
    league_by_id = {l["idLeague"]: l for l in leagues}
    processed = 0
    updated_leagues = []

    while True:
        if time.monotonic() - start > MAX_RUNTIME_SECONDS:
            break

        batch = manager.dequeue_batch(LOOKUPLEAGUE_QUEUE, 1)
        if not batch:
            break

        league_id = batch[0]
        existing = league_by_id.get(league_id)
        if existing is None:
            continue

        try:
            data = manager.request("lookupleague.php", {"id": league_id})
        except APIError as e:
            Logger.error(f"Failed to update league {league_id}: {e}")
            manager.enqueue(LOOKUPLEAGUE_QUEUE, league_id)
            continue

        api_leagues = data.get("leagues") or []
        if not api_leagues or api_leagues[0] is None:
            continue

        updated = build_league_object(existing, api_leagues[0])
        league_by_id[league_id] = updated
        updated_leagues.append(updated)
        processed += 1

    if processed:
        save_leagues(list(league_by_id.values()))

    if updated_leagues:
        events_data = load_events()
        touched = 0
        for league in updated_leagues:
            if refresh_tracked_league_fields(events_data, league):
                touched += 1
        if touched:
            save_events(events_data)
            Logger.info(f"Refreshed league metadata for {touched} league(s) in events.json.")

    return processed


def run_eventsday_queue(manager: APIManager, start: float) -> int:
    dates = get_target_dates()
    sync_state = load_sync_state(dates)
    leagues_by_id = {l["idLeague"]: l for l in load_leagues()}
    data = load_events()
    processed = process_eventsday_queue(manager, data, leagues_by_id, sync_state, start)
    if processed:
        sort_leagues(data)
        prune_empty_leagues(data)
        resync_channel_links(data)
        save_events(data)
        save_sync_state(sync_state)
    return processed


def run_lookupevent_queue(manager: APIManager, start: float) -> int:
    leagues_by_id = {l["idLeague"]: l for l in load_leagues()}
    data = load_events()
    processed = process_lookupevent_queue(manager, data, leagues_by_id, start)
    if processed:
        sort_leagues(data)
        prune_empty_leagues(data)
        resync_channel_links(data)
        save_events(data)
    return processed


QUEUE_HANDLERS = {
    LOOKUPLEAGUE_QUEUE: run_lookupleague_queue,
    EVENTSDAY_QUEUE: run_eventsday_queue,
    LOOKUPEVENT_QUEUE: run_lookupevent_queue,
}


def main():
    manager = APIManager(script_name="queue_runner.py")
    start = time.monotonic()
    any_work = False

    for queue_name, handler in QUEUE_HANDLERS.items():
        if manager.queue_length(queue_name) == 0:
            continue
        any_work = True
        pending = manager.queue_length(queue_name)
        Logger.info(f"Processing queue_{queue_name}.json ({pending} pending)...")
        processed = handler(manager, start)
        Logger.success(f"Processed {processed} item(s) from queue_{queue_name}.json.")

        if time.monotonic() - start > MAX_RUNTIME_SECONDS:
            Logger.warning("Runtime budget reached. Stopping queue runner for this cycle.")
            break

    if not any_work:
        Logger.info("All queues empty. Using this cycle for event status sync instead.")
        run_status_sync(manager, start, MAX_RUNTIME_SECONDS)

    Logger.info(f"Total API requests this run: {manager.request_count}")


if __name__ == "__main__":
    main()