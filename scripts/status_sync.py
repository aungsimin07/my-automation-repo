import os
import time
from datetime import datetime, timezone, timedelta

from api_manager import APIManager
from event_scraper import scrape_event
from event_store_v2 import (
    load_events, save_events, sort_leagues, prune_empty_leagues,
    build_event_object_any_status, upsert_event, resync_channel_links,
    get_target_dates, prune_to_dates, FINISHED_STATUSES, prune_channelless_events,
)
from fcm_notifier import get_access_token
from fetch_events_from_channels import load_channel_entries
from match_notifications import notify_match_started
from utils.logger import Logger

LIVE_STATUSES = {"1H", "HT", "2H", "ET", "P", "BT", "INT", "SUSP"}
LIVE_RECHECK_MINUTES = 115

# A match this long past kickoff is over, whatever the page still says.
# Backstop for pages stuck on NS / UNK / a live status.
STALE_HOURS = 4

# Match-started notification window: only notify if this is the FIRST
# recheck within this many minutes after kickoff. Deliberately independent
# of strStatus, since the page can lag updating NS -> 1H well past kickoff.
MATCH_STARTED_WINDOW_MINUTES = 15

DELAY_BETWEEN_REQUESTS = 1.5


def _slug_for(event: dict) -> str:
    return (event.get("strEvent") or "").strip().lower().replace(" ", "-")


def _fcm_enabled() -> bool:
    return bool(os.getenv("FCM_PROJECT_ID", "").strip() and os.getenv("FCM_SERVICE_ACCOUNT_JSON", "").strip())


def parse_event_timestamp(event: dict):
    ts = event.get("strTimestamp")
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def needs_status_check(event: dict, now: datetime) -> bool:
    status = event.get("strStatus")
    ts = parse_event_timestamp(event)
    if ts is None:
        return False
    if status in ("NS", "UNK"):
        return now > ts
    if status in LIVE_STATUSES:
        return (now - ts) >= timedelta(minutes=LIVE_RECHECK_MINUTES)
    return False


def should_notify_match_started(event: dict, now: datetime) -> bool:
    """True if this event's strTimestamp has passed, it's still recorded
    as NS, hasn't been notified yet, and 'now' falls within
    MATCH_STARTED_WINDOW_MINUTES of that timestamp, i.e. this is (close
    to) the first time we're catching it after kickoff."""
    event_id = event.get("idEvent")
    already_sent = event.get("metadata", {}).get("notifications_sent", [])

    if "started" in already_sent:
        Logger.info(f"Event {event_id}: 'started' already sent, skipping notify check.")
        return False

    if event.get("strStatus") != "NS":
        Logger.info(f"Event {event_id}: strStatus is '{event.get('strStatus')}' (not NS), skipping timestamp-based notify check.")
        return False

    ts = parse_event_timestamp(event)
    if ts is None:
        Logger.info(f"Event {event_id}: no strTimestamp, cannot evaluate notify window.")
        return False

    gap = now - ts
    if gap < timedelta(0):
        Logger.info(f"Event {event_id}: strTimestamp is in the future (kicks off in {-gap}), not due yet.")
        return False

    if gap >= timedelta(minutes=MATCH_STARTED_WINDOW_MINUTES):
        Logger.warning(
            f"Event {event_id}: gap since strTimestamp is {gap}, past the "
            f"{MATCH_STARTED_WINDOW_MINUTES}min window, treating as missed, will NOT notify."
        )
        return False

    Logger.info(
        f"Event {event_id}: gap since strTimestamp is {gap}, within "
        f"{MATCH_STARTED_WINDOW_MINUTES}min window, will notify as match started."
    )
    return True


def remove_finished_events(data: dict) -> int:
    removed = 0
    for league_entry in data["leagues"]:
        before = len(league_entry.get("events", []))
        league_entry["events"] = [
            e for e in league_entry.get("events", []) if e.get("strStatus") not in FINISHED_STATUSES
        ]
        removed += before - len(league_entry["events"])
    if removed:
        Logger.info(f"Removed {removed} finished event(s).")
    return removed


def _finalize(data: dict) -> int:
    """Shared cleanup + resync. Returns number of finished events removed."""
    removed = remove_finished_events(data)
    prune_to_dates(data, get_target_dates())
    sort_leagues(data)
    prune_empty_leagues(data)
    channels = load_channel_entries()
    resync_channel_links(data, channels)
    if prune_channelless_events(data):
        prune_empty_leagues(data)
    return removed


def run_status_sync(manager: APIManager, start: float, max_runtime_seconds: int) -> int:
    """Idle-cycle task for queue_runner.py: refresh strStatus for events
    due a recheck by scraping the event page, fire a Match Started
    notification based on strTimestamp proximity (independent of the
    page's status, which can lag), then remove any event that has finished."""
    data = load_events()
    now = datetime.now(timezone.utc)
    fcm_enabled = _fcm_enabled()
    Logger.info(f"Status sync: FCM notifications {'enabled' if fcm_enabled else 'disabled (missing FCM_PROJECT_ID/FCM_SERVICE_ACCOUNT_JSON)'}.")
    channel_entries = load_channel_entries() if fcm_enabled else []

    candidates = []
    for league_entry in data["leagues"]:
        for event in league_entry.get("events", []):
            if needs_status_check(event, now):
                candidates.append(event)

    if not candidates:
        Logger.info("Status sync: no events due for a recheck.")
        before_state = (
            sum(len(l.get("events", [])) for l in data["leagues"]),
            len(data["leagues"]),
        )
        removed = _finalize(data)
        after_state = (
            sum(len(l.get("events", [])) for l in data["leagues"]),
            len(data["leagues"]),
        )
        if removed or before_state != after_state:
            save_events(data)
        return 0

    Logger.info(f"Status sync: {len(candidates)} event(s) due for a recheck.")
    processed = 0
    access_token = get_access_token(os.getenv("FCM_SERVICE_ACCOUNT_JSON")) if fcm_enabled else None
    project_id = os.getenv("FCM_PROJECT_ID", "").strip()

    for event in candidates:
        if time.monotonic() - start > max_runtime_seconds:
            Logger.warning("Runtime budget reached during status sync. Remaining events will be checked next cycle.")
            break

        event_id = event.get("idEvent")

        # Decide BEFORE the scrape, using the event's current (pre-refresh)
        # data, deliberately independent of what the page says the status is.
        should_notify = should_notify_match_started(event, now) if fcm_enabled else False

        raw_event, http_status = scrape_event(event_id, _slug_for(event))
        time.sleep(DELAY_BETWEEN_REQUESTS)
        if raw_event is None:
            Logger.error(
                f"Event page scrape failed for {event_id} (HTTP {http_status}; "
                f"None/5xx = fetch problem, 200 = parse problem), will retry next cycle."
            )
            continue

        processed += 1
        source = event.get("metadata", {}).get("source", "channel_schedule")
        updated = build_event_object_any_status(raw_event, source=source)
        if updated is None:
            continue

        # Backstop: a match this far past kickoff is over even if the page
        # is stuck on NS / UNK / a live status.
        ts = parse_event_timestamp(event)
        stuck = updated.get("strStatus") in ({"UNK", "NS"} | LIVE_STATUSES)
        if stuck and ts and (now - ts) > timedelta(hours=STALE_HOURS):
            Logger.info(f"Event {event_id}: status '{updated.get('strStatus')}' but {now - ts} past kickoff, treating as finished.")
            updated["strStatus"] = "FT"

        old_status, new_status = event.get("strStatus"), updated.get("strStatus")
        if old_status != new_status:
            Logger.info(f"Event {event_id} status changed: {old_status} -> {new_status}")

        # build_event_object_any_status builds a fresh, empty metadata
        # dict, so preserve everything already recorded on this event
        # (channels, channel_path_scrape, notifications_sent, etc.).
        updated["metadata"] = event.get("metadata", {})
        updated["metadata"]["last_sync_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if should_notify:
            notify_match_started(updated, channel_entries, project_id, access_token)

        id_league = raw_event.get("idLeague") or event.get("idLeague")
        league_entry = next((l for l in data["leagues"] if l.get("idLeague") == id_league), None)
        if league_entry:
            upsert_event(league_entry, updated)

    removed = _finalize(data)
    save_events(data)

    Logger.success(f"Status sync processed {processed} event(s), removed {removed} finished event(s).")
    return processed