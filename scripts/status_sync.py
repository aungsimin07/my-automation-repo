import time
from datetime import datetime, timezone, timedelta

from api_manager import APIManager, APIError
from event_store_v2 import (
    load_events, save_events, sort_leagues, prune_empty_leagues,
    build_event_object_any_status, upsert_event, resync_channel_links,
    get_target_dates, prune_to_dates,
)
from fetch_events_from_channels import load_channel_entries
from utils.logger import Logger

LIVE_STATUSES = {"1H", "HT", "2H", "ET", "P", "BT", "INT", "SUSP"}
FINISHED_STATUSES = {"FT", "AET", "PEN", "AWD", "WO", "PST", "CANC", "ABD"}
LIVE_RECHECK_MINUTES = 115


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
    if status == "NS":
        return now > ts
    if status in LIVE_STATUSES:
        return (now - ts) >= timedelta(minutes=LIVE_RECHECK_MINUTES)
    return False


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


def run_status_sync(manager: APIManager, start: float, max_runtime_seconds: int) -> int:
    """Idle-cycle task for queue_runner.py: when no queue has pending work,
    use the budget to refresh strStatus for events that are due a recheck
    (NS events past kickoff, or Live events stuck live past the threshold),
    then remove any event that has finished."""
    data = load_events()
    now = datetime.now(timezone.utc)

    candidates = []
    for league_entry in data["leagues"]:
        for event in league_entry.get("events", []):
            if needs_status_check(event, now):
                candidates.append(event)

    if not candidates:
        Logger.info("Status sync: no events due for a recheck.")
        removed = remove_finished_events(data)
        pruned = prune_to_dates(data, get_target_dates())
        if removed or pruned:
            sort_leagues(data)
            prune_empty_leagues(data)
            channels = load_channel_entries()
            resync_channel_links(data, channels)
            save_events(data)
        return 0

    Logger.info(f"Status sync: {len(candidates)} event(s) due for a recheck.")
    processed = 0

    for event in candidates:
        if time.monotonic() - start > max_runtime_seconds:
            Logger.warning("Runtime budget reached during status sync. Remaining events will be checked next cycle.")
            break

        event_id = event.get("idEvent")
        try:
            resp = manager.request("lookupevent.php", {"id": event_id})
        except APIError as e:
            Logger.error(f"lookupevent.php failed for event {event_id}: {e}")
            continue

        processed += 1
        raw_events = resp.get("events") or []
        if not raw_events or raw_events[0] is None:
            Logger.warning(f"No event data found for id {event_id} during status sync.")
            continue

        raw_event = raw_events[0]
        source = event.get("metadata", {}).get("source", "channel_schedule")
        updated = build_event_object_any_status(raw_event, source=source)
        if updated is None:
            continue

        old_status, new_status = event.get("strStatus"), updated.get("strStatus")
        if old_status != new_status:
            Logger.info(f"Event {event_id} status changed: {old_status} -> {new_status}")

        # lookupevent.php has no notion of our channel links — carry
        # them forward from the existing event object, or they'd be lost
        updated.setdefault("metadata", {})["channels"] = event.get("metadata", {}).get("channels", [])

        id_league = raw_event.get("idLeague") or event.get("idLeague")
        league_entry = next((l for l in data["leagues"] if l.get("idLeague") == id_league), None)
        if league_entry:
            upsert_event(league_entry, updated)

    removed = remove_finished_events(data)
    prune_to_dates(data, get_target_dates())
    sort_leagues(data)
    prune_empty_leagues(data)
    channels = load_channel_entries()
    resync_channel_links(data, channels)
    save_events(data)

    Logger.success(f"Status sync processed {processed} event(s), removed {removed} finished event(s).")
    return processed
