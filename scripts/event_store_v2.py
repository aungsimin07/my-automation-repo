import json
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import Logger

EVENTS_FILE = Path("data/events_v2.json")

EVENT_FIELDS = [
    "idEvent", "idAPIfootball", "idLeague", "strLeague", "strLeagueBadge",
    "strSeason", "strGroup", "intRound", "dateEvent", "strTime", "strTimestamp",
    "strEvent",
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

CHANNEL_ENTRY_OPTIONAL_FIELDS = ["provider", "priority", "quality", "format", "httpUserAgent"]


def load_events() -> dict:
    if not EVENTS_FILE.exists():
        return {"leagues": [], "channels": []}
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            Logger.warning(f"{EVENTS_FILE} is corrupt/empty. Starting fresh.")
            return {"leagues": [], "channels": []}
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


def fetch_league_entry_via_api(manager, id_league: str):
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
    entry["metadata"] = {"last_sync_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    entry["events"] = []
    return entry


def get_or_create_league_entry(data: dict, id_league: str, builder) -> dict:
    for entry in data["leagues"]:
        if entry.get("idLeague") == id_league:
            return entry
    entry = builder()
    data["leagues"].append(entry)
    return entry


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
        Logger.info(f"Dropped {dropped} league(s) with no events.")


def link_channel_to_event(event: dict, tvg_id: str) -> bool:
    ev_channels = event.setdefault("metadata", {}).setdefault("channels", [])
    if tvg_id in ev_channels:
        return False
    ev_channels.append(tvg_id)
    ev_channels.sort()
    return True