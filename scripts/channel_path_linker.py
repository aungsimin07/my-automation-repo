import os
import re
import time
from datetime import datetime, timezone

import requests

from event_store_v2 import (
    load_events, save_events, sort_leagues, prune_empty_leagues,
    resync_channel_links, relink_from_stored_paths,
)
from fetch_events_from_channels import load_channel_entries
from utils.logger import Logger

EVENT_URL_PREFIX = "https://www.thesportsdb.com/event/"
CHANNEL_HREF_PATTERN = re.compile(r'''href=['"](/channel/[^'"]+)['"]''')
REQUEST_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 1.5


def slugify_event(str_event: str) -> str:
    return str_event.strip().lower().replace(" ", "-")


def build_event_url(id_event: str, str_event: str) -> str:
    return f"{EVENT_URL_PREFIX}{id_event}-{slugify_event(str_event)}"


def needs_scrape(event: dict) -> bool:
    """Scrape once per event — UNLESS the previous attempt failed with a
    transient error (network failure, or an HTTP 5xx from the server),
    in which case it's retried. A clean 200 (even with zero channels
    found) or a non-5xx error status (e.g. 404) is treated as a
    permanent result and never retried."""
    if event.get("strStatus") != "NS":
        return False
    if not event.get("strEvent"):
        return False

    scrape = event.get("metadata", {}).get("channel_path_scrape")
    if not scrape:
        return True  # never scraped

    http_status = scrape.get("http_status")
    if http_status is None or http_status >= 500:
        return True  # transient failure (network error or 5xx) — retry

    return False  # permanent result (200 or non-5xx error) — never retry


def scrape_event_channel_paths(event_url: str, headers: dict):
    """Returns (channel_paths_found, http_status). Paths are full hrefs
    like '/channel/6518-tnt-sports-1-tv-schedule', not just the numeric id."""
    try:
        resp = requests.get(event_url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as e:
        Logger.warning(f"  -> request failed: {e}")
        return None, None

    Logger.info(f"  -> HTTP {resp.status_code}, {len(resp.text)} bytes")
    if resp.status_code != 200:
        Logger.warning("  -> non-200 status, skipping page parse")
        return None, resp.status_code

    seen = set()
    unique_paths = []
    for match in CHANNEL_HREF_PATTERN.findall(resp.text):
        if match not in seen:
            seen.add(match)
            unique_paths.append(match)

    Logger.info(f"  -> found {len(unique_paths)} channel path(s): {unique_paths if unique_paths else '(none)'}")
    return unique_paths, resp.status_code


def run_channel_path_discovery(manager, start: float, max_runtime_seconds: int) -> int:
    """Idle-cycle task: scrape NS events never (successfully) scraped
    before — or whose last attempt failed transiently — then re-match
    every event's stored raw paths against the current channel list."""
    default_user_agent = os.getenv("DEFAULT_HTTP_USER_AGENT", "").strip() or "Mozilla/5.0"
    headers = {"User-Agent": default_user_agent}

    data = load_events()
    channel_entries = load_channel_entries()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    candidates = []
    for league in data.get("leagues", []):
        for event in league.get("events", []):
            if needs_scrape(event):
                candidates.append(event)

    scraped = 0
    if not candidates:
        Logger.info("Channel path discovery: no event(s) due for a scrape.")
    else:
        Logger.info(f"Channel path discovery: {len(candidates)} event(s) due for a scrape.")
        for event in candidates:
            if time.monotonic() - start > max_runtime_seconds:
                Logger.warning("Runtime budget reached during channel path discovery. Remaining event(s) next cycle.")
                break

            event_id = event.get("idEvent")
            str_event = event["strEvent"]
            event_url = build_event_url(event_id, str_event)
            Logger.info(f"Event {event_id} ('{str_event}') -> {event_url}")

            channel_paths_found, http_status = scrape_event_channel_paths(event_url, headers)
            scraped += 1
            time.sleep(DELAY_BETWEEN_REQUESTS)

            event.setdefault("metadata", {})["channel_path_scrape"] = {
                "checked_at": now_iso,
                "event_url": event_url,
                "http_status": http_status,
                "channel_paths_found": channel_paths_found or [],
            }

    linked = relink_from_stored_paths(data, channel_entries)

    if scraped or linked:
        sort_leagues(data)
        prune_empty_leagues(data)
        resync_channel_links(data, channel_entries)
        save_events(data)

    Logger.success(f"Channel path discovery complete: {scraped} event(s) scraped, {linked} new link(s) made.")
    return scraped
