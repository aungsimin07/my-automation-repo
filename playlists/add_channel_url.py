import os

from channel_store import load_channel, save_channel, build_channel_object, upsert_url, touch_channel_sync, MANUAL_SOURCE
from utils.logger import Logger


def parse_int(raw: str, default: int = 0) -> int:
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        Logger.error(f"Expected an integer, got '{raw}'.", fatal=True)


def main():
    tvg_id = os.getenv("TVG_ID", "").strip()
    url = os.getenv("URL", "").strip()
    display_name = os.getenv("DISPLAY_NAME", "").strip()
    provider = os.getenv("PROVIDER", "").strip() or MANUAL_SOURCE
    priority = parse_int(os.getenv("PRIORITY", ""), default=0)
    quality = os.getenv("QUALITY", "").strip() or None
    stream_format = os.getenv("FORMAT", "").strip() or "hls"
    user_agent = os.getenv("HTTP_USER_AGENT", "").strip() or None

    if not tvg_id:
        Logger.error("TVG_ID is required.", fatal=True)
    if not url:
        Logger.error("URL is required.", fatal=True)

    channel = load_channel(tvg_id)
    if channel is None:
        if not display_name:
            Logger.error(
                f"No existing channel found for tvg-id '{tvg_id}'. "
                f"DISPLAY_NAME is required to create a new channel.",
                fatal=True,
            )
        channel = build_channel_object(-1, display_name, {"tvg-id": tvg_id})
        Logger.info(f"Created new channel entry for tvg-id '{tvg_id}'.")

    url_fields = {
        "url": url, "provider": provider, "priority": priority, "format": stream_format,
        "metadata": {"source": provider},
    }
    if quality:
        url_fields["quality"] = quality
    if user_agent:
        url_fields["httpUserAgent"] = user_agent

    upsert_url(channel, provider, url, url_fields)
    touch_channel_sync(channel)
    save_channel(channel)

    Logger.success(f"Added url to channel '{channel.get('displayName')}' ({tvg_id}): {url}")


if __name__ == "__main__":
    main()
