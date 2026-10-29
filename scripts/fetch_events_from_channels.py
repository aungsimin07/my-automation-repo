import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone

from api_manager import APIManager, APIError
from channel_scraper import scrape_channel_schedule
from channel_sync_state import load_sync_state, save_sync_state, is_synced, mark_synced
from event_scraper import scrape_event
from event_store_v2 import (
    load_events, save_events, build_event_object, build_league_entry_from_tracked,
    build_league_entry_from_scraped, get_or_create_league_entry, upsert_event,
    sort_leagues, prune_empty_leagues, link_channel_to_event, resync_channel_links,
    get_target_dates, prune_to_dates, relink_from_stored_paths, prune_channelless_events,
)
from league_store import load_leagues
from utils.logger import Logger

CHANNELS_FILE = Path("data/channels_v2.json")
SUPPORTED_SPORT = "Soccer"
CHANNEL_SCHEDULE_QUEUE = "channel_schedule"
CHANNEL_LOOKUPEVENT_QUEUE = "channel_lookupevent"
MAX_RUNTIME_SECONDS = int(os.getenv("MAX_RUNTIME_SECONDS", "240"))
DELAY_BETWEEN_REQUESTS = 1.5

def load_channel_entries() -> list:
    if not CHANNELS_FILE.exists():
        return []
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            Logger.warning(f"{CHANNELS_FILE} is corrupt/empty.")
            return []


def _force_refresh_enabled() -> bool:
    raw = os.getenv("FORCE_REFRESH", "false").strip().lower()
    return raw in ("1", "true", "yes")


def process_channel_schedule_queue(manager: APIManager, sync_state: dict, start: float) -> int:
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
        events = scrape_channel_schedule(channel_path)
        if events:
            manager.enqueue(CHANNEL_LOOKUPEVENT_QUEUE, [
                {"eventId": eid, "slug": slug, "tvgId": tvg_id} for eid, slug in events
            ])
        
        mark_synced(sync_state, channel_path, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        processed += 1

    return processed


def process_channel_lookupevent_queue(manager, data: dict, leagues_by_id: dict, start: float) -> int:
    processed, linked, skipped_sport = 0, 0, 0
    cache = {}      # event_id -> (scraped_event, status): one page fetch per event per run
    deferred = []   # tasks that hit a transient failure, re-queued after the loop

    while True:
        if time.monotonic() - start > MAX_RUNTIME_SECONDS:
            Logger.warning(f"Runtime budget reached. {manager.queue_length(CHANNEL_LOOKUPEVENT_QUEUE)} event lookup(s) left queued.")
            break
        batch = manager.dequeue_batch(CHANNEL_LOOKUPEVENT_QUEUE, 1)
        if not batch:
            break

        task = batch[0]
        event_id, tvg_id, slug = task["eventId"], task["tvgId"], task.get("slug")

        if event_id in cache:
            scraped, status = cache[event_id]
        else:
            scraped, status = scrape_event(event_id, slug)
            time.sleep(DELAY_BETWEEN_REQUESTS)
            if scraped is None and (status is None or status >= 500 or status == 429):
                deferred.append(task)
                continue
            cache[event_id] = (scraped, status)

        processed += 1
        if scraped is None:
            Logger.warning(f"Event {event_id}: no usable data (HTTP {status}), dropping.")
            continue
        if scraped.get("strSport") != SUPPORTED_SPORT:
            skipped_sport += 1
            continue

        event_obj = build_event_object(scraped, source="channel_schedule")
        if event_obj is None:
            continue  # not NS

        id_league = scraped["idLeague"]
        league = leagues_by_id.get(id_league)
        if league:
            league_entry = get_or_create_league_entry(data, id_league, lambda: build_league_entry_from_tracked(league))
        else:
            league_entry = get_or_create_league_entry(data, id_league, lambda: build_league_entry_from_scraped(scraped))

        existing = next((e for e in league_entry.get("events", []) if e.get("idEvent") == event_id), None)
        if existing:
            event_obj["metadata"] = existing.get("metadata", event_obj["metadata"])

        # same page already gave us the channel paths, so record the scrape here
        event_obj["metadata"]["channel_path_scrape"] = {
            "checked_at": event_obj["metadata"]["last_sync_at"],
            "event_url": scraped["eventUrl"],
            "http_status": 200,
            "channel_paths_found": scraped["channelPaths"],
        }

        if link_channel_to_event(event_obj, tvg_id):
            linked += 1
        upsert_event(league_entry, event_obj)

    if deferred:
        manager.enqueue(CHANNEL_LOOKUPEVENT_QUEUE, deferred)
        Logger.warning(f"{len(deferred)} event lookup(s) hit a transient failure and were re-queued.")
    if skipped_sport:
        Logger.info(f"Skipped {skipped_sport} non-Soccer event(s).")
    if linked:
        Logger.info(f"Made {linked} channel link(s).")
    return processed