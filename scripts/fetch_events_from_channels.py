import json
import os
import time
from pathlib import Path

from api_manager import APIManager, APIError
from channel_scraper import scrape_channel_schedule
from event_store_v2 import (
    load_events, save_events, build_event_object, build_league_entry_from_tracked,
    fetch_league_entry_via_api, get_or_create_league_entry, upsert_event,
    sort_leagues, prune_empty_leagues, link_channel_to_event,
)
from league_store import load_leagues
from utils.logger import Logger

CHANNELS_FILE = Path("data/channels_v2.json")
SUPPORTED_SPORT = "Soccer"
CHANNEL_SCHEDULE_QUEUE = "channel_schedule"
CHANNEL_LOOKUPEVENT_QUEUE = "channel_lookupevent"
MAX_RUNTIME_SECONDS = int(os.getenv("MAX_RUNTIME_SECONDS", "240"))


def load_channel_entries() -> list:
    if not CHANNELS_FILE.exists():
        return []
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            Logger.warning(f"{CHANNELS_FILE} is corrupt/empty.")
            return []


def process_channel_schedule_queue(manager: APIManager, start: float) -> int:
    """Drains queue_channel_schedule.json: one task per channel entry.
    Scrapes its schedule page, enqueues discovered event ids (tagged
    with which tvg-id found them) into queue_channel_lookupevent.json."""
    processed = 0
    while True:
        if time.monotonic() - start > MAX_RUNTIME_SECONDS:
            Logger.warning(f"Runtime budget reached. {manager.queue_length(CHANNEL_SCHEDULE_QUEUE)} channel task(s) left queued.")
            break

        batch = manager.dequeue_batch(CHANNEL_SCHEDULE_QUEUE, 1)
        if not batch:
            break

        task = batch[0]
        tvg_id, channel_path = task["tvgId"], task["channelPath"]
        event_ids = scrape_channel_schedule(channel_path)

        if event_ids:
            manager.enqueue(CHANNEL_LOOKUPEVENT_QUEUE, [{"eventId": eid, "tvgId": tvg_id} for eid in event_ids])

        processed += 1

    return processed


def process_channel_lookupevent_queue(manager: APIManager, data: dict, leagues_by_id: dict, start: float) -> int:
    """Drains queue_channel_lookupevent.json: one task per (event id,
    tvg-id that discovered it). Calls lookupevent.php, filters to Soccer
    + NS, upserts into events.json, links the discovering channel."""
    processed = 0
    linked = 0
    skipped_sport = 0

    while True:
        if time.monotonic() - start > MAX_RUNTIME_SECONDS:
            Logger.warning(f"Runtime budget reached. {manager.queue_length(CHANNEL_LOOKUPEVENT_QUEUE)} event lookup(s) left queued.")
            break

        batch = manager.dequeue_batch(CHANNEL_LOOKUPEVENT_QUEUE, 1)
        if not batch:
            break

        task = batch[0]
        event_id, tvg_id = task["eventId"], task["tvgId"]

        try:
            resp = manager.request("lookupevent.php", {"id": event_id})
        except APIError as e:
            Logger.error(f"lookupevent.php failed for event {event_id}: {e}")
            manager.enqueue(CHANNEL_LOOKUPEVENT_QUEUE, task)
            continue

        processed += 1
        raw_events = resp.get("events") or []
        if not raw_events or raw_events[0] is None:
            Logger.warning(f"No event data found for id {event_id}.")
            continue

        raw_event = raw_events[0]
        if raw_event.get("strSport") != SUPPORTED_SPORT:
            skipped_sport += 1
            continue

        event_obj = build_event_object(raw_event, source="channel_schedule")
        if event_obj is None:
            continue  # not NS, or missing required fields

        id_league = raw_event.get("idLeague")
        league = leagues_by_id.get(id_league)
        if league:
            league_entry = get_or_create_league_entry(data, id_league, lambda: build_league_entry_from_tracked(league))
        else:
            league_entry = next((l for l in data["leagues"] if l.get("idLeague") == id_league), None)
            if league_entry is None:
                league_entry = fetch_league_entry_via_api(manager, id_league)
                if league_entry is None:
                    Logger.warning(f"Could not fetch league for untracked league {id_league}. Dropping event {event_id}.")
                    continue
                data["leagues"].append(league_entry)

        upsert_event(league_entry, event_obj)
        if link_channel_to_event(event_obj, tvg_id):
            linked += 1

    if skipped_sport:
        Logger.info(f"Skipped {skipped_sport} non-Soccer event(s).")
    if linked:
        Logger.info(f"Made {linked} channel link(s).")

    return processed


def main():
    manager = APIManager(script_name="fetch_events_from_channels.py")
    channels = load_channel_entries()
    if not channels:
        Logger.warning("No channels found in channels_v2.json. Nothing to do.")
        return

    eligible = [c for c in channels if c.get("channelPath") and c.get("tvg", {}).get("id")]
    skipped = len(channels) - len(eligible)
    if skipped:
        Logger.info(f"Skipping {skipped} channel entr(y/ies) with no channelPath or tvg.id.")
    if not eligible:
        Logger.warning("No channel entries have channelPath set. Nothing to do.")
        return

    tasks = [{"tvgId": c["tvg"]["id"], "channelPath": c["channelPath"]} for c in eligible]
    manager.enqueue(CHANNEL_SCHEDULE_QUEUE, tasks)

    data = load_events()
    leagues_by_id = {l["idLeague"]: l for l in load_leagues()}

    start = time.monotonic()
    schedule_processed = process_channel_schedule_queue(manager, start)
    lookupevent_processed = process_channel_lookupevent_queue(manager, data, leagues_by_id, start)

    sort_leagues(data)
    prune_empty_leagues(data)
    save_events(data)

    Logger.success(
        f"Processed {schedule_processed} channel schedule task(s), "
        f"{lookupevent_processed} event lookup(s) this run."
    )
    Logger.info(f"Total API requests this run: {manager.request_count}")


if __name__ == "__main__":
    main()
