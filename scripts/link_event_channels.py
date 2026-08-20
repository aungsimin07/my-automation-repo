import os

from event_store import load_events, save_events, find_event_by_id, link_channel_to_event
from playlists.channel_store import load_channel
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
    tvg_ids = parse_csv_list(os.getenv("CHANNEL_IDS", ""))

    if not event_id:
        Logger.error("EVENT_ID is required.", fatal=True)
    if not tvg_ids:
        Logger.error("CHANNEL_IDS is required (comma-separated tvg-id(s)).", fatal=True)

    data = load_events()
    league_entry, event = find_event_by_id(data, event_id)
    if event is None:
        Logger.error(f"Event {event_id} not found in events.json.", fatal=True)

    linked_total = 0
    missing = []
    for tvg_id in tvg_ids:
        channel = load_channel(tvg_id)
        if channel is None or not channel.get("urls"):
            missing.append(tvg_id)
            continue
        linked_total += link_channel_to_event(data, event, tvg_id, channel)

    if missing:
        Logger.warning(f"{len(missing)} channel id(s) have no file/urls under data/channels/, skipped: {', '.join(missing)}")

    if linked_total == 0:
        Logger.warning("No new channel url(s) linked.")
        return

    save_events(data)
    Logger.success(f"Linked {linked_total} url(s) to event {event_id}.")


if __name__ == "__main__":
    main()