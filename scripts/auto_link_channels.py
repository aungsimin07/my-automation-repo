import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from event_store import load_events, save_events, link_channel_to_event, resync_channel_links
from playlists.channel_store import build_channel_id_index
from utils.logger import Logger

EVENT_URL_PREFIX = "https://www.thesportsdb.com/event/"
CHANNEL_HREF_PATTERN = re.compile(r'''href=['"]/channel/(\d+)-''')
REQUEST_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 1.5

MAX_RUNTIME_SECONDS = int(os.getenv("MAX_RUNTIME_SECONDS", "240"))
RECHECK_HOURS = int(os.getenv("AUTO_LINK_RECHECK_HOURS", "6"))


def slugify_event(str_event: str) -> str:
    return str_event.strip().lower().replace(" ", "-")


def build_event_url(id_event: str, str_event: str) -> str:
    return f"{EVENT_URL_PREFIX}{id_event}-{slugify_event(str_event)}"


def needs_recheck(event: dict, now: datetime) -> bool:
    if event.get("strStatus") != "NS":
        return False
    if not event.get("strEvent"):
        return False

    last_checked = event.get("metadata", {}).get("channel_auto_link", {}).get("last_checked_at")
    if not last_checked:
        return True
    try:
        last_dt = datetime.strptime(last_checked, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (now - last_dt) >= timedelta(hours=RECHECK_HOURS)


def scrape_channel_ids(event_url: str, headers: dict):
    """Returns (channel_ids_found, http_status) — status is None on a
    network-level failure (no response at all)."""
    try:
        resp = requests.get(event_url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as e:
        Logger.warning(f"  -> request failed: {e}")
        return None, None

    Logger.info(f"  -> HTTP {resp.status_code}, {len(resp.text)} bytes")

    if resp.status_code != 200:
        Logger.warning(f"  -> non-200 status, skipping page parse")
        return None, resp.status_code

    matches = CHANNEL_HREF_PATTERN.findall(resp.text)
    Logger.info(f"  -> found {len(matches)} /channel/ href(s) on page: {matches if matches else '(none)'}")
    return matches, resp.status_code


def main():
    default_user_agent = os.getenv("DEFAULT_HTTP_USER_AGENT", "").strip() or "Mozilla/5.0"
    headers = {"User-Agent": default_user_agent}
    Logger.info(f"Using User-Agent: {default_user_agent}")

    data = load_events()
    channel_id_index = build_channel_id_index()
    Logger.info(f"Channel-id index built from data/channels/: {len(channel_id_index)} entr(y/ies) -> {channel_id_index}")

    if not channel_id_index:
        Logger.warning("No channel files have a channel-id set. Nothing to match against.")
        return

    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    candidates = []
    for league in data.get("leagues", []):
        for event in league.get("events", []):
            if needs_recheck(event, now):
                candidates.append(event)

    if not candidates:
        Logger.info("Auto-link: no NS event(s) due for a channel-link check.")
        return

    Logger.info(f"Auto-link: {len(candidates)} NS event(s) due for a channel-link check.")

    start = time.monotonic()
    checked = 0
    linked_events = 0

    for event in candidates:
        if time.monotonic() - start > MAX_RUNTIME_SECONDS:
            Logger.warning("Runtime budget reached. Remaining event(s) will be checked next cycle.")
            break

        event_id = event.get("idEvent")
        str_event = event["strEvent"]
        event_url = build_event_url(event_id, str_event)

        Logger.info(f"Event {event_id} ('{str_event}') -> built url: {event_url}")

        channel_ids_found, http_status = scrape_channel_ids(event_url, headers)
        checked += 1
        time.sleep(DELAY_BETWEEN_REQUESTS)

        matched_tvg_ids = []
        unresolved_channel_ids = []
        if channel_ids_found:
            for cid in channel_ids_found:
                tvg_id = channel_id_index.get(cid)
                if tvg_id:
                    if link_channel_to_event(event, tvg_id):
                        matched_tvg_ids.append(tvg_id)
                        Logger.info(f"  -> channel-id {cid} resolved to tvg-id '{tvg_id}', linked")
                    else:
                        Logger.info(f"  -> channel-id {cid} resolved to tvg-id '{tvg_id}', already linked, skipping")
                else:
                    unresolved_channel_ids.append(cid)

            if unresolved_channel_ids:
                Logger.info(f"  -> channel-id(s) not in our tracked channels, ignored: {unresolved_channel_ids}")

        event.setdefault("metadata", {})["channel_auto_link"] = {
            "last_checked_at": now_iso,
            "event_url": event_url,
            "http_status": http_status,
            "channel_ids_found": channel_ids_found or [],
            "matched": matched_tvg_ids,
        }

        if matched_tvg_ids:
            linked_events += 1
            Logger.success(f"Event {event_id}: auto-linked {', '.join(matched_tvg_ids)}")
        else:
            Logger.info(f"Event {event_id}: no new channel(s) matched this check.")

    resync_channel_links(data)
    save_events(data)
    Logger.success(f"Auto-link complete: checked {checked} event(s), {linked_events} newly matched channel(s) this run.")


if __name__ == "__main__":
    main()