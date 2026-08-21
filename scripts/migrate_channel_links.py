"""ONE-TIME migration: converts event.metadata.channels from raw stream
URLs (the old scheme) to tvg-id strings (the new scheme), using the
CURRENT top-level `channels` array (still url-keyed at the time this
runs) to resolve each url back to its tvg-id.

Run this exactly once, right before deploying the tvg-id-keyed linking
scripts. Safe to delete this file and its workflow after running."""

from event_store import load_events, save_events, resync_channel_links
from utils.logger import Logger


def main():
    data = load_events()

    url_to_tvg_id = {c["url"]: c["tvg-id"] for c in data.get("channels", []) if c.get("url") and c.get("tvg-id")}
    if not url_to_tvg_id:
        Logger.warning("No entries in the current top-level channels array. Nothing to resolve against — aborting.")
        return

    migrated_events = 0
    unresolved = set()

    for league in data.get("leagues", []):
        for event in league.get("events", []):
            ev_channels = event.get("metadata", {}).get("channels", [])
            if not ev_channels:
                continue

            # Skip events that already look tvg-id-based (no '://' present)
            if not any("://" in c for c in ev_channels):
                continue

            tvg_ids = set()
            for entry in ev_channels:
                tvg_id = url_to_tvg_id.get(entry)
                if tvg_id:
                    tvg_ids.add(tvg_id)
                else:
                    unresolved.add(entry)

            event["metadata"]["channels"] = sorted(tvg_ids)
            migrated_events += 1

    if unresolved:
        Logger.warning(f"{len(unresolved)} url(s) could not be resolved to a tvg-id: {', '.join(sorted(unresolved))}")

    Logger.success(f"Migrated {migrated_events} event(s) from url-based to tvg-id-based channel links.")

    # Rebuild the top-level array fresh from the newly tvg-id-based links
    summary = resync_channel_links(data)
    Logger.info(f"Post-migration sync: {summary['alive']}/{summary['referenced']} tvg-id(s) alive.")

    save_events(data)


if __name__ == "__main__":
    main()