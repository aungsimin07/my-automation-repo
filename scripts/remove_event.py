import os

from event_store import load_events, save_events, sort_leagues, prune_empty_leagues
from logger import Logger


def main():
    event_id = os.getenv("EVENT_ID", "").strip()
    if not event_id:
        Logger.error("EVENT_ID is required.", fatal=True)

    data = load_events()

    league_entry = None
    for entry in data["leagues"]:
        if any(e.get("idEvent") == event_id for e in entry.get("events", [])):
            league_entry = entry
            break

    if league_entry is None:
        Logger.error(f"Event {event_id} not found in events.json.", fatal=True)

    league_entry["events"] = [e for e in league_entry["events"] if e.get("idEvent") != event_id]

    sort_leagues(data)
    prune_empty_leagues(data)  # drops the league bucket if that was its last event
    save_events(data)

    Logger.success(f"Removed event {event_id} from league '{league_entry.get('strLeague')}'.")


if __name__ == "__main__":
    main()