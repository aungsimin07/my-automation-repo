import os
import time

from api_manager import APIManager, APIError
from event_store import load_events, save_events, sort_league_events, prune_empty_leagues
from fetch_events import EVENTSDAY_QUEUE, LOOKUPEVENT_QUEUE, process_eventsday_queue, process_lookupevent_queue
from league_store import load_leagues, save_leagues
from logger import Logger
from update_league import build_league_object, QUEUE_NAME as LOOKUPLEAGUE_QUEUE

MAX_RUNTIME_SECONDS = int(os.getenv("MAX_RUNTIME_SECONDS", "240"))


def run_lookupleague_queue(manager: APIManager, start: float) -> int:
    leagues = load_leagues()
    league_by_id = {l["idLeague"]: l for l in leagues}
    processed = 0

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

        league_by_id[league_id] = build_league_object(existing, api_leagues[0])
        processed += 1

    if processed:
        save_leagues(list(league_by_id.values()))
    return processed

def run_eventsday_queue(manager: APIManager, start: float) -> int:
    leagues_by_id = {l["idLeague"]: l for l in load_leagues()}
    data = load_events()
    processed = process_eventsday_queue(manager, data, leagues_by_id, start)
    if processed:
        for league_entry in data["leagues"]:
            sort_league_events(league_entry)
        prune_empty_leagues(data)
        save_events(data)
    return processed


def run_lookupevent_queue(manager: APIManager, start: float) -> int:
    leagues_by_id = {l["idLeague"]: l for l in load_leagues()}
    data = load_events()
    processed = process_lookupevent_queue(manager, data, leagues_by_id, start)
    if processed:
        for league_entry in data["leagues"]:
            sort_league_events(league_entry)
        prune_empty_leagues(data)
        save_events(data)
    return processed

# Add future queues here as they're introduced, e.g.:
# "lookupevent": run_lookupevent_queue,
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
        Logger.info("All queues empty. Nothing to do.")

    Logger.info(f"Total API requests this run: {manager.request_count}")


if __name__ == "__main__":
    main()