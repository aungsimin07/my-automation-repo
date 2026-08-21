import os

from event_store import load_events, save_events, find_event_by_id, unlink_channel_from_event, prune_unreferenced_channels
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

    removed_total = sum(unlink_channel_from_event(data, event, tvg_id) for tvg_id in tvg_ids)

    if removed_total == 0:
        Logger.warning("None of the provided channel id(s) were linked to this event. Nothing removed.")
        return

    prune_unreferenced_channels(data)
    save_events(data)
    Logger.success(f"Unlinked {removed_total} url(s) from event {event_id}.")


if __name__ == "__main__":
    main()