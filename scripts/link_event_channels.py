import os

from event_store import load_events, save_events, find_event_by_id
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

    missing = [cid for cid in channel_ids if not channel_exists(cid)]
    if missing:
        Logger.warning(
            f"{len(missing)} channel id(s) have no channel file under /channels/: {', '.join(missing)}. "
            f"Linking anyway."
        )

    existing = set(event.setdefault("metadata", {}).setdefault("channels", []))
    added = [c for c in channel_ids if c not in existing]

    if not added:
        Logger.warning("All provided channel id(s) are already linked to this event. Nothing to add.")
        return

    event["metadata"]["channels"] = sorted(existing | set(added))
    save_events(data)
    Logger.success(f"Linked {len(added)} channel(s) to event {event_id}: {', '.join(added)}")


if __name__ == "__main__":
    main()
