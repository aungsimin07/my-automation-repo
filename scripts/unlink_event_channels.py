import os

from event_store import load_events, save_events, find_event_by_id
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


def main():
    event_id = os.getenv("EVENT_ID", "").strip()
    channel_ids = parse_csv_list(os.getenv("CHANNEL_IDS", ""))

    if not event_id:
        Logger.error("EVENT_ID is required.", fatal=True)
    if not channel_ids:
        Logger.error("CHANNEL_IDS is required (comma-separated tvg-id(s)).", fatal=True)

    data = load_events()
    league_entry, event = find_event_by_id(data, event_id)
    if event is None:
        Logger.error(f"Event {event_id} not found in events.json.", fatal=True)

    existing = event.get("metadata", {}).get("channels", [])
    remove_set = set(channel_ids)
    remaining = [c for c in existing if c not in remove_set]
    removed = [c for c in existing if c in remove_set]

    if not removed:
        Logger.warning("None of the provided channel id(s) were linked to this event. Nothing removed.")
        return

    event["metadata"]["channels"] = remaining
    save_events(data)
    Logger.success(f"Unlinked {len(removed)} channel(s) from event {event_id}: {', '.join(removed)}")


if __name__ == "__main__":
    main()
