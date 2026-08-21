import os

from event_store import load_events, save_events, find_event_by_id, link_channel_to_event
from playlists.channel_store import channel_exists
from utils.logger import Logger


def parse_csv_list(raw: str) -> list:
    if not raw:
        return []
    seen = set()
    result = []
    for part in raw.split(","):
        val = part.strip()
        if val and val not in seen:
            seen.add(val)
            result.append(val)
    return result


def normalize_tvg_id(raw: str) -> str:
    """Defensive: strip an accidental trailing '.json' if the user pasted
    a filename instead of the raw tvg-id."""
    if raw.lower().endswith(".json"):
        stripped = raw[: -len(".json")]
        Logger.warning(f"'{raw}' looked like a filename — using '{stripped}' as the tvg-id instead.")
        return stripped
    return raw


def main():
    event_id = os.getenv("EVENT_ID", "").strip()
    tvg_ids = [normalize_tvg_id(t) for t in parse_csv_list(os.getenv("CHANNEL_IDS", ""))]

    if not event_id:
        Logger.error("EVENT_ID is required.", fatal=True)
    if not tvg_ids:
        Logger.error("CHANNEL_IDS is required (comma-separated tvg-id(s)).", fatal=True)

    data = load_events()
    league_entry, event = find_event_by_id(data, event_id)
    if event is None:
        Logger.error(f"Event {event_id} not found in events.json.", fatal=True)

    added = []
    missing = []
    for tvg_id in tvg_ids:
        if not channel_exists(tvg_id):
            missing.append(tvg_id)
            continue
        if link_channel_to_event(event, tvg_id):
            added.append(tvg_id)

    if missing:
        Logger.warning(f"{len(missing)} channel id(s) have no file under data/channels/, skipped: {', '.join(missing)}")

    if not added:
        Logger.warning("No new channel(s) linked.")
        return

    save_events(data)
    Logger.success(f"Linked {len(added)} channel(s) to event {event_id}: {', '.join(added)}")
    Logger.info("Run sync_channel_links.py next to refresh the top-level channels array.")


if __name__ == "__main__":
    main()