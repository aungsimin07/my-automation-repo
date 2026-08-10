import argparse
import json
import os
import sys
import re
import time
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup
from logger import Log

SUPPORTED_LEAGUES_FILE = "data/supported_leagues.json"
FIXTURES_FILE = "data/fixtures.json"

EVENT_KEYS = [
    "idEvent", "idAPIfootball", "idLeague", "strLeague", "strLeagueBadge",
    "strSeason", "strGroup", "intRound", "dateEvent", "strTime",
    "strTimestamp", "idHomeTeam", "strHomeTeam", "strHomeTeamBadge",
    "intHomeScore", "idAwayTeam", "strAwayTeam", "strAwayTeamBadge",
    "intAwayScore", "strStatus"
]

# Statuses that are considered "Upcoming" or "Live"
ACTIVE_STATUSES = {"TBD", "NS", "1H", "HT", "2H", "ET", "P", "BT", ""}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36'
}

def load_supported_leagues():
    """Loads league data from supported_leagues.json."""
    if not os.path.exists(SUPPORTED_LEAGUES_FILE):
        Log.error(f"{SUPPORTED_LEAGUES_FILE} not found.")
        sys.exit(1)
        
    with open(SUPPORTED_LEAGUES_FILE, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            Log.error(f"Could not parse {SUPPORTED_LEAGUES_FILE}.")
            sys.exit(1)

def extract_date(date_text):
    """Converts scraped text like '22 Jul 26' into 'YYYY-MM-DD'."""
    clean_text = " ".join(date_text.split())
    match = re.search(r'(\d{1,2})\s*([A-Za-z]{3})\s*(\d{2,4})', clean_text)
    
    if match:
        day, month, year = match.groups()
        if len(year) == 2:
            year = f"20{year}"
            
        date_str = f"{day} {month} {year}"
        try:
            return datetime.strptime(date_str, "%d %b %Y").strftime("%Y-%m-%d")
        except ValueError as e:
            Log.warn(f"Date parsing failed for string '{date_str}': {e}")
            return None
    return None

def filter_and_process_event(raw_event, source="api"):
    """Filters by strStatus and formats the final event dictionary."""
    status = str(raw_event.get("strStatus", "")).strip().upper()
    home = raw_event.get("strHomeTeam", "Unknown")
    away = raw_event.get("strAwayTeam", "Unknown")

    # Reject finished, postponed, cancelled, etc.
    if status not in ACTIVE_STATUSES:
        Log.skip(f"[{status}] {home} vs {away} - Event ended or not played.")
        return None

    parsed = {key: str(raw_event.get(key, "") or "") for key in EVENT_KEYS}
    parsed["source"] = source
    return parsed

def fetch_api_events(league_id, date_str):
    """Requests events for a specific league and date from API."""
    api_url = f"https://www.thesportsdb.com/api/v1/json/123/eventsday.php?d={date_str}&l={league_id}"
    Log.api(f"Fetching API Events for League ID {league_id} on {date_str}...")
    
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("events") or []
    except requests.exceptions.RequestException as e:
        Log.error(f"Failed API request for league ID {league_id} on {date_str}: {e}")
        return []

def scrape_fallback_events(league_url, target_date, existing_event_ids):
    """Scrapes the league page for event IDs matching target_date."""
    Log.http(f"Scraper Fallback: {league_url} for date {target_date}")
    try:
        response = requests.get(league_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        Log.error(f"Failed to fetch URL: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    tables = soup.find_all('table', class_=re.compile('league-events-table'))
    
    if not tables:
        Log.warn("Could not find <table> with class 'league-events-table'.")
        return []
    
    scraped_event_ids = []

    for t_idx, table in enumerate(tables):
        rows = table.find_all('tr')
        is_upcoming = False

        for idx, tr in enumerate(rows):
            row_text = tr.get_text(separator=' ', strip=True)
            
            if 'upcoming' in row_text.lower():
                is_upcoming = True
                continue
                
            if 'results' in row_text.lower() and is_upcoming:
                break # Reached the end of the upcoming section

            if is_upcoming:
                tds = tr.find_all('td')
                if len(tds) < 4:
                    continue

                raw_date_text = tds[0].get_text(separator=' ', strip=True)
                formatted_date = extract_date(raw_date_text)
                
                a_tag = tds[1].find('a') or tds[3].find('a')
                event_id = None
                if a_tag and 'href' in a_tag.attrs:
                    match = re.search(r'/event/(\d+)', a_tag['href'])
                    if match:
                        event_id = match.group(1)

                if formatted_date == target_date and event_id:
                    if event_id not in existing_event_ids:
                        scraped_event_ids.append(event_id)

    return scraped_event_ids

def fetch_event_lookup(event_id):
    """Fetches single event JSON from API via scraper lookup."""
    api_url = f"https://www.thesportsdb.com/api/v1/json/123/lookupevent.php?id={event_id}"
    Log.api(f"Requesting lookup for Event ID: {event_id}")
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("events") and len(data["events"]) > 0:
            return data["events"][0]
    except requests.exceptions.RequestException as e:
        Log.error(f"Failed lookup for event {event_id}: {e}")
    return None

def main():
    parser = argparse.ArgumentParser(description="Unified Fixtures Sync (API + Scraper Fallback)")
    parser.add_argument(
        '--date', 
        type=str, 
        default=datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        help="Target Start Date (YYYY-MM-DD)."
    )
    args = parser.parse_args()
    
    # Calculate 2-day window
    base_date = datetime.strptime(args.date, '%Y-%m-%d')
    target_dates = [
        base_date.strftime('%Y-%m-%d'),
        (base_date + timedelta(days=1)).strftime('%Y-%m-%d')
    ]

    Log.start(f"Unified Fixture Sync spanning: {target_dates[0]} to {target_dates[1]}")

    leagues_config = load_supported_leagues()
    final_fixtures_data = {
        "dates": target_dates,
        "leagues": []
    }

    total_added = 0

    for league in leagues_config:
        league_id = str(league.get("idLeague", ""))
        league_url = league.get("leagueUrl", "")
        str_league = league.get("strLeague", "Unknown League")

        if not league_id:
            continue

        Log.section_in(f"Processing {str_league} (ID: {league_id})")
        
        valid_events = {}

        # Loop through both dates for the current league
        for current_date in target_dates:
            Log.info(f"--- Fetching for {current_date} ---")
            
            # 1. API Primary Fetch
            raw_api_events = fetch_api_events(league_id, current_date)
            for raw_event in raw_api_events:
                processed = filter_and_process_event(raw_event, source="api_scheduled")
                if processed:
                    ev_id = processed["idEvent"]
                    valid_events[ev_id] = processed
                    Log.match(f"Added via API: {processed['strHomeTeam']} vs {processed['strAwayTeam']}")

            # 2. Scraper Fallback Fetch
            if league_url:
                existing_ids = set(valid_events.keys())
                scraped_ids = scrape_fallback_events(league_url, current_date, existing_ids)
                
                if scraped_ids:
                    Log.fetch(f"Found {len(scraped_ids)} missing events via Scraper. Looking up...")
                    for ev_id in scraped_ids:
                        time.sleep(0.5) # Rate limiting respect
                        raw_lookup = fetch_event_lookup(ev_id)
                        if raw_lookup:
                            processed = filter_and_process_event(raw_lookup, source="scraped_lookup")
                            if processed:
                                valid_events[ev_id] = processed
                                Log.match(f"Added via Scraper: {processed['strHomeTeam']} vs {processed['strAwayTeam']}")
                else:
                    Log.info("No missing events found via Scraper for this date.")
        
        # 3. Assemble League Object
        if valid_events:
            league_obj = {
                "idLeague": league_id,
                "strLeague": str_league,
                "strLeagueBadge": league.get("strBadge", ""),
                "leagueUrl": league_url,
                "events": list(valid_events.values()) # Dict values maintain insertion order
            }
            final_fixtures_data["leagues"].append(league_obj)
            total_added += len(valid_events)
            Log.ok(f"Saved {len(valid_events)} active matches across 2 days for {str_league}.")
        else:
            Log.skip(f"No active events for {str_league}. Omitting league from JSON.")
            
        Log.section_out(f"Completed {str_league}")

    # 4. Save Final Output
    os.makedirs(os.path.dirname(FIXTURES_FILE), exist_ok=True)
    with open(FIXTURES_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_fixtures_data, f, indent=2, ensure_ascii=False)
    
    Log.success(f"Sync Complete! {total_added} total active matches saved to {FIXTURES_FILE}.")

if __name__ == "__main__":
    main()
