import re

import requests
from bs4 import BeautifulSoup

from utils.logger import Logger

BASE_URL = "https://www.thesportsdb.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SportsDataBot/1.0)"}
EVENT_HREF_RE = re.compile(r'/event/(\d+)-([^/?#]+)')


def scrape_channel_schedule(channel_path: str) -> list:
    """Returns [(event_id, slug), ...] listed under '(today)'/'(tomorrow)'."""
    url = f"{BASE_URL}{channel_path}"
    Logger.info(f"Scraping channel schedule: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        Logger.warning(f"Failed to fetch {url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    found, in_target = [], False
    for el in soup.find_all(["h2", "a"]):
        if el.name == "h2":
            t = el.get_text(strip=True).lower()
            in_target = "(today)" in t or "(tomorrow)" in t
            continue
        if in_target:
            m = EVENT_HREF_RE.search(el.get("href", ""))
            if m and (m.group(1), m.group(2)) not in found:
                found.append((m.group(1), m.group(2)))

    Logger.info(f"  -> {len(found)} event(s) for today/tomorrow: {[f[0] for f in found]}")
    return found