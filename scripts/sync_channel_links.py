from event_store import load_events, save_events, resync_channel_links
from utils.logger import Logger


def main():
    data = load_events()
    summary = resync_channel_links(data)

    if summary["dead"]:
        Logger.warning(
            f"{len(summary['dead'])} referenced tvg-id(s) had no channel file/urls, "
            f"unlinked from {summary['unlinked_events']} event(s): {', '.join(summary['dead'])}"
        )

    Logger.success(
        f"Sync complete: {summary['alive']}/{summary['referenced']} referenced tvg-id(s) alive, "
        f"{summary['top_level_entries']} top-level channel entr(y/ies) written."
    )
    save_events(data)


if __name__ == "__main__":
    main()