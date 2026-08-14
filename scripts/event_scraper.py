import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from logger import Logger

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SportsDataBot/1.0)"}


def _extract_date(date_text: str):
    clean_text = " ".join(date_text.split())
    match = re.search(r"(\d{1,2})\s*([A-Za-z]{3})\s*(\d{2,4})", clean_text)
    if not match:
        return None
    day, month, year = match.groups()
    if len(year) == 2:
        year = f"20{year}"
    date_str = f"{day} {month} {year}"
    try:
        return datetime.strptime(date_str, "%d %b %Y").strftime("%Y-%m-%d")
    except ValueError as e:
        Logger.warning(f"Date parsing failed for string '{date_str}': {e}")
        return None


def scrape_fallback_event_ids(league_url: str, target_date: str, existing_event_ids: set) -> list:
    """Scrape a league's public page 'Upcoming' table for event ids on
    target_date that eventsday.php's 3-event truncation missed. Only
    discovers ids — full data is still fetched via lookupevent.php."""
    Logger.info(f"Scraper fallback: {league_url} for date {target_date}")
    try:
        response = requests.get(league_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        Logger.warning(f"Failed to fetch league page: {e}")
        return []

    soup = BeautifulSoup(response.text, "lxml")
    tables = soup.find_all("table", class_=re.compile("league-events-table"))
    if not tables:
        Logger.warning("Could not find <table class='league-events-table'> on league page.")
        return []

    found_ids = []
    for table in tables:
        rows = table.find_all("tr")
        is_upcoming = False

        for tr in rows:
            row_text = tr.get_text(separator=" ", strip=True)

            if "upcoming" in row_text.lower():
                is_upcoming = True
                continue
            if "results" in row_text.lower() and is_upcoming:
                break
            if not is_upcoming:
                continue

            tds = tr.find_all("td")
            if len(tds) < 4:
                continue

            raw_date_text = tds[0].get_text(separator=" ", strip=True)
            formatted_date = _extract_date(raw_date_text)

            a_tag = tds[1].find("a") or tds[3].find("a")
            event_id = None
            if a_tag and "href" in a_tag.attrs:
                match = re.search(r"/event/(\d+)", a_tag["href"])
                if match:
                    event_id = match.group(1)

            if formatted_date == target_date and event_id and event_id not in existing_event_ids:
                found_ids.append(event_id)

    return found_ids
