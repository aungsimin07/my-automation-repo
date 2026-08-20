import json
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import Logger

EVENTS_FILE = Path("data/events.json")

EVENT_FIELDS = [
    "idEvent", "idAPIfootball", "idLeague", "strLeague", "strLeagueBadge",
    "strSeason", "strGroup", "intRound", "dateEvent", "strTime", "strTimestamp",
    "idHomeTeam", "strHomeTeam", "strHomeTeamBadge", "intHomeScore",
    "idAwayTeam", "strAwayTeam", "strAwayTeamBadge", "intAwayScore",
    "strVenue", "strPoster", "strSquare", "strFanart", "strThumb", "strBanner",
    "strStatus",
]
REQUIRED_EVENT_FIELDS = ["idEvent", "dateEvent", "strHomeTeam", "strAwayTeam", "strStatus"]
LEAGUE_FIELDS = [
    "idLeague", "idAPIfootballv3", "idCup", "strLeague", "strCurrentSeason",
    "strComplete", "strBadge", "strLeagueBadge", "strWebsite", "leagueUrl",
]

LIVE_STATUSES = {"1H", "HT", "2H", "ET", "P", "BT", "INT", "SUSP"}
FINISHED_STATUSES = {"FT", "AET", "PEN", "AWD", "WO", "PST", "CANC", "ABD"}
LIVE_RECHECK_MINUTES = 115

# optional fields carried from a per-url entry into the flattened top-level object
CHANNEL_ENTRY_OPTIONAL_FIELDS = ["provider", "priority", "quality", "format", "httpUserAgent"]


def load_events() -> dict:
    if not EVENTS_FILE.exists():
        return {"dates": [], "leagues": [], "channels": []}
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            Logger.warning(f"{EVENTS_FILE} is corrupt/empty. Starting fresh.")
            return {"dates": [], "leagues": [], "channels": []}
    data.setdefault("dates", [])
    data.setdefault("leagues", [])
    data.setdefault("channels", [])
    return data


def save_events(data: dict) -> None:
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    total_events = sum(len(l.get("events", [])) for l in data.get("leagues", []))
    Logger.success(
        f"Saved {len(data.get('leagues', []))} league(s), {total_events} event(s), "
        f"{len(data.get('channels', []))} channel entr(y/ies) to {EVENTS_FILE}"
    )


def build_event_object(raw_event: dict, source: str):
    if raw_event.get("strStatus") != "NS":
        return None
    return build_event_object_any_status(raw_event, source)


def build_event_object_any_status(raw_event: dict, source: str):
    for field in REQUIRED_EVENT_FIELDS:
        if not raw_event.get(field):
            Logger.warning(f"Event missing required field '{field}'. Skipping.")
            return None

    event = {f: raw_event.get(f) for f in EVENT_FIELDS if raw_event.get(f) is not None}
    event["metadata"] = {
        "source": source,
        "last_sync_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return event


def build_league_entry_from_tracked(league: dict) -> dict:
    entry = {f: league.get(f) for f in LEAGUE_FIELDS if league.get(f) is not None}
    metadata = league.get("metadata")
    if metadata:
        entry["metadata"] = dict(metadata)
    entry["events"] = []
    return entry


def fetch_league_entry_via_api(manager, id_league: str, logo_id=None):
    from api_manager import APIError

    try:
        resp = manager.request("lookupleague.php", {"id": id_league})
    except APIError as e:
        Logger.warning(f"lookupleague.php failed for untracked league {id_league}: {e}")
        return None

    api_leagues = resp.get("leagues") or []
    if not api_leagues or api_leagues[0] is None:
        Logger.warning(f"No league data found for id {id_league}.")
        return None

    api_league = api_leagues[0]
    entry = {f: api_league.get(f) for f in LEAGUE_FIELDS if api_league.get(f) is not None}
    metadata = {"last_sync_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if logo_id is not None:
        metadata["leagueLogo"] = logo_id
    entry["metadata"] = metadata
    entry["events"] = []
    return entry


def get_or_create_league_entry(data: dict, id_league: str, builder) -> dict:
    for entry in data["leagues"]:
        if entry.get("idLeague") == id_league:
            return entry
    entry = builder()
    data["leagues"].append(entry)
    return entry


def refresh_tracked_league_fields(data: dict, league: dict) -> bool:
    id_league = league.get("idLeague")
    entry = next((l for l in data["leagues"] if l.get("idLeague") == id_league), None)
    if entry is None:
        return False
    fresh = build_league_entry_from_tracked(league)
    fresh["events"] = entry.get("events", [])
    entry.clear()
    entry.update(fresh)
    return True


def find_event_by_id(data: dict, event_id: str):
    for league_entry in data.get("leagues", []):
        for event in league_entry.get("events", []):
            if event.get("idEvent") == event_id:
                return league_entry, event
    return None, None


def upsert_event(league_entry: dict, event_obj: dict) -> None:
    events = league_entry.setdefault("events", [])
    for i, existing in enumerate(events):
        if existing.get("idEvent") == event_obj["idEvent"]:
            events[i] = event_obj
            return
    events.append(event_obj)


def _event_sort_key(event: dict):
    ts = event.get("strTimestamp")
    if ts:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(ts, fmt)
            except ValueError:
                continue
    date_part = event.get("dateEvent", "1970-01-01")
    time_part = event.get("strTime", "00:00:00")
    try:
        return datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return datetime.max


def sort_league_events(league_entry: dict) -> None:
    league_entry["events"].sort(key=_event_sort_key)


def _league_sort_key(league: dict):
    raw = league.get("idAPIfootballv3")
    try:
        return (0, int(raw))
    except (TypeError, ValueError):
        return (1, str(raw or ""))


def sort_leagues(data: dict) -> None:
    for league_entry in data["leagues"]:
        sort_league_events(league_entry)
    data["leagues"].sort(key=_league_sort_key)


def prune_empty_leagues(data: dict) -> None:
    before = len(data["leagues"])
    data["leagues"] = [l for l in data["leagues"] if l.get("events")]
    dropped = before - len(data["leagues"])
    if dropped:
        Logger.info(f"Dropped {dropped} league(s) with no events for this window.")


def prune_to_dates(data: dict, dates: list) -> None:
    date_set = set(dates)
    for league_entry in data["leagues"]:
        league_entry["events"] = [
            e for e in league_entry.get("events", [])
            if e.get("metadata", {}).get("source") == "manual" or e.get("dateEvent") in date_set
        ]
    data["dates"] = dates


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
        return (now - ts).total_seconds() > LIVE_RECHECK_MINUTES * 60
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


# ---- channel linking (top-level `channels` cache) --------------------------

def _flatten_channel_urls(tvg_id: str, channel: dict) -> list:
    """Turn a per-file channel object's `urls` array into flattened
    top-level entries, one per url, keyed by url."""
    entries = []
    for u in channel.get("urls", []):
        entry = {
            "tvg-id": tvg_id,
            "displayName": channel.get("displayName"),
            "url": u.get("url"),
        }
        for field in CHANNEL_ENTRY_OPTIONAL_FIELDS:
            if u.get(field) is not None:
                entry[field] = u.get(field)
        entries.append(entry)
    return entries


def link_channel_to_event(data: dict, event: dict, tvg_id: str, channel: dict) -> int:
    """Materialize every current url of `channel` into the top-level
    channels array (only adding ones not already present) and link them
    all to `event`. Returns how many NEW urls got linked to this event."""
    new_entries = _flatten_channel_urls(tvg_id, channel)
    existing_urls = {c["url"] for c in data["channels"]}
    for entry in new_entries:
        if entry["url"] not in existing_urls:
            data["channels"].append(entry)
            existing_urls.add(entry["url"])

    ev_channels = event.setdefault("metadata", {}).setdefault("channels", [])
    ev_set = set(ev_channels)
    added = [e["url"] for e in new_entries if e["url"] not in ev_set]
    if added:
        event["metadata"]["channels"] = sorted(ev_set | set(added))
    return len(added)


def unlink_channel_from_event(data: dict, event: dict, tvg_id: str) -> int:
    """Remove every url belonging to tvg_id from event's linked channels."""
    tvgid_urls = {c["url"] for c in data["channels"] if c.get("tvg-id") == tvg_id}
    ev_channels = event.get("metadata", {}).get("channels", [])
    remaining = [u for u in ev_channels if u not in tvgid_urls]
    removed = len(ev_channels) - len(remaining)
    if removed:
        event["metadata"]["channels"] = remaining
    return removed


def sync_channel_updates(data: dict, channel_updates: dict) -> int:
    """channel_updates: {tvg_id: channel_dict_or_None}. For every tvg-id
    that ALREADY has entries in the top-level channels array (i.e. is
    currently linked to at least one event), refresh those entries to
    the latest file content and swap every event's OLD urls for that
    tvg-id to the NEW ones. A tvg-id with no existing top-level entries
    is skipped entirely — nothing gets added except via link_event_channels.py."""
    if not channel_updates:
        return 0

    channels = data.setdefault("channels", [])
    touched_events = 0

    for tvg_id, channel in channel_updates.items():
        old_urls = {c["url"] for c in channels if c.get("tvg-id") == tvg_id}
        if not old_urls:
            continue  # not currently linked anywhere

        new_entries = _flatten_channel_urls(tvg_id, channel) if channel else []
        new_urls = {e["url"] for e in new_entries}

        channels[:] = [c for c in channels if c.get("tvg-id") != tvg_id]
        channels.extend(new_entries)

        for league in data.get("leagues", []):
            for event in league.get("events", []):
                ev_channels = event.get("metadata", {}).get("channels", [])
                if not ev_channels or not (old_urls & set(ev_channels)):
                    continue
                event["metadata"]["channels"] = sorted((set(ev_channels) - old_urls) | new_urls)
                touched_events += 1

    return touched_events


def prune_unreferenced_channels(data: dict) -> int:
    """Drop any top-level channel entry that no event links anymore.
    Self-healing backstop — call after any operation that could remove
    an event's channel link (unlink, event removal, league removal)."""
    referenced = set()
    for league in data.get("leagues", []):
        for event in league.get("events", []):
            referenced.update(event.get("metadata", {}).get("channels", []))
    before = len(data.get("channels", []))
    data["channels"] = [c for c in data.get("channels", []) if c.get("url") in referenced]
    dropped = before - len(data["channels"])
    if dropped:
        Logger.info(f"Pruned {dropped} unreferenced channel entr(y/ies) from top-level channels.")
    return dropped