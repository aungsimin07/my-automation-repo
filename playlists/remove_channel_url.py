import os

from channel_store import load_channel, save_channel, delete_channel
from utils.logger import Logger


def main():
    tvg_id = os.getenv("TVG_ID", "").strip()
    url = os.getenv("URL", "").strip()

    if not tvg_id:
        Logger.error("TVG_ID is required.", fatal=True)
    if not url:
        Logger.error("URL is required.", fatal=True)

    channel = load_channel(tvg_id)
    if channel is None:
        Logger.error(f"No channel found for tvg-id '{tvg_id}'.", fatal=True)

    before = len(channel.get("urls", []))
    channel["urls"] = [u for u in channel.get("urls", []) if u.get("url") != url]
    removed = before - len(channel["urls"])

    if not removed:
        Logger.warning(f"URL not found on channel '{tvg_id}'. Nothing removed.")
        return

    if channel["urls"]:
        save_channel(channel)
    else:
        delete_channel(tvg_id)
        Logger.info(f"Channel '{tvg_id}' had no remaining urls. Removed channel file.")

    Logger.success(f"Removed url from channel '{tvg_id}': {url}")


if __name__ == "__main__":
    main()
