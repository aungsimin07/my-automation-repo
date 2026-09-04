import re

import requests
from bs4 import BeautifulSoup

from utils.logger import Logger

BASE_URL = "https://www.thesportsdb.com/channel"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SportsDataBot/1.0)"}
EVENT_ID_PATTERN = re.compile(r'/event/(\d+)-')


def scrape_channel_schedule(channel_path: str) -> list:
    """Scrape a channel's TV schedule page and return event ids listed
    ONLY under the '(today)' and '(tomorrow)' date headers."""
    url = f"{BASE_URL}{channel_path}"
    Logger.info(f"Scraping channel schedule: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        Logger.warning(f"Failed to fetch {url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    event_ids = []
    in_target_section = False

    for el in soup.find_all(["h2", "a"]):
        if el.name == "h2":
            text = el.get_text(strip=True).lower()
            in_target_section = "(today)" in text or "(tomorrow)" in text
            continue
        if in_target_section:
            href = el.get("href", "")
            match = EVENT_ID_PATTERN.search(href)
            if match:
                event_ids.append(match.group(1))

    Logger.info(f"  -> {len(event_ids)} event id(s) for today/tomorrow: {event_ids}")
    return event_ids
