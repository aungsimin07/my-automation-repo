import json
import os
import sys
import re
import time
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from logger import Log

FIXTURES_FILE = "data/fixtures.json"
TASK_QUEUE_FILE = "data/task_queue.json"

EVENT_KEYS = [
    "idEvent", "idAPIfootball", "idLeague", "strLeague", "strLeagueBadge",
    "strSeason", "strGroup", "intRound", "dateEvent", "strTime",
    "strTimestamp", "idHomeTeam", "strHomeTeam", "strHomeTeamBadge",
    "intHomeScore", "idAwayTeam", "strAwayTeam", "strAwayTeamBadge",
    "intAwayScore", "strStatus"
]

ACTIVE_STATUSES = {"TBD", "NS", "1H", "HT", "2H", "ET", "P", "BT", ""}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36'
}

class RateLimitException(Exception):
    """Custom exception raised when the API returns a 429 status code."""
    pass

def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    with open(filepath, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            Log.error(f"Could not parse {filepath}.")
            return None

def extract_date(date_text):
    clean_text = " ".join(date_text.split())
    match = re.search(r'(\d{1,2})\s*([A-Za-z]{3})\s*(\d{2,4})', clean_text)
    if match:
        day, month, year = match.groups()
        if len(year) == 2:
            year = f"20{year}"
        date_str = f"{day} {month} {year}"
        try:
            return datetime.strptime(date_str, "%d %b %Y").strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None

def filter_and_process_event(raw_event, source="api"):
    status = str(raw_event.get("strStatus", "")).strip().upper()
    home = raw_event.get("strHomeTeam", "Unknown")
    away = raw_event.get("strAwayTeam", "Unknown")

    if status not in ACTIVE_STATUSES:
        Log.skip(f"[{status}] {home} vs {away} - Event ended or not played.")
        return None

    parsed = {key: str(raw_event.get(key, "") or "") for key in EVENT_KEYS}
    parsed["source"] = source
    return parsed

def get_sort_key(event):
    """Extracts timestamp or constructs a fallback datetime string for sorting."""
    ts = event.get("strTimestamp")
    if ts and ts.strip():
        return ts.strip()
    date_val = event.get("dateEvent") or "9999-12-31"
    time_val = event.get("strTime") or "23:59:59"
    return f"{date_val}T{time_val}"

def fetch_api_events(league_id, date_str):
    api_url = f"https://www.thesportsdb.com/api/v1/json/123/eventsday.php?d={date_str}&l={league_id}"
    Log.api(f"Fetching API Events for League ID {league_id} on {date_str}...")
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("events") or []
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            raise RateLimitException("429 Too Many Requests")
        Log.error(f"Failed API request for league ID {league_id} on {date_str}: {e}")
        return []
    except requests.exceptions.RequestException as e:
        Log.error(f"Failed API request for league ID {league_id} on {date_str}: {e}")
        return []

def scrape_fallback_events(league_url, target_date, existing_event_ids):
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
        return []
    
    scraped_event_ids = []
    for table in tables:
        rows = table.find_all('tr')
        is_upcoming = False
        for tr in rows:
            row_text = tr.get_text(separator=' ', strip=True)
            if 'upcoming' in row_text.lower():
                is_upcoming = True
                continue
            if 'results' in row_text.lower() and is_upcoming:
                break 

            if is_upcoming:
                tds = tr.find_all('td')
                if len(tds) < 4:
                    continue
                formatted_date = extract_date(tds[0].get_text(separator=' ', strip=True))
                
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
    api_url = f"https://www.thesportsdb.com/api/v1/json/123/lookupevent.php?id={event_id}"
    Log.api(f"Requesting lookup for Event ID: {event_id}")
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("events") and len(data["events"]) > 0:
            return data["events"][0]
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            raise RateLimitException("429 Too Many Requests")
        Log.error(f"Failed lookup for event {event_id}: {e}")
    except requests.exceptions.RequestException as e:
        Log.error(f"Failed lookup for event {event_id}: {e}")
    return None

def merge_events_into_fixtures(fixtures_data, league_id, str_league, str_badge, league_url, new_events):
    if not new_events:
        return

    leagues_list = fixtures_data.setdefault("leagues", [])
    target_league = next((l for l in leagues_list if str(l.get("idLeague")) == str(league_id)), None)

    if not target_league:
        target_league = {
            "idLeague": str(league_id),
            "strLeague": str_league,
            "strLeagueBadge": str_badge,
            "leagueUrl": league_url,
            "events": []
        }
        leagues_list.append(target_league)

    existing_event_ids = {e["idEvent"] for e in target_league.get("events", [])}
    added_count = 0

    for ev in new_events:
        if ev["idEvent"] not in existing_event_ids:
            target_league["events"].append(ev)
            existing_event_ids.add(ev["idEvent"])
            added_count += 1

    # Sort events so the nearest upcoming match is at the top
    target_league["events"].sort(key=get_sort_key)

    if added_count > 0:
        Log.ok(f"Merged {added_count} new events into {str_league}.")

def main():
    Log.start("Sweeper Execution: Checking Task Queue")
    
    task_queue = load_json(TASK_QUEUE_FILE)
    if not task_queue or task_queue.get("status") == "completed":
        Log.success("Task queue is empty or completed. Nothing to resume.")
        sys.exit(0)

    fixtures_data = load_json(FIXTURES_FILE) or {
        "dates": task_queue.get("target_dates", []),
        "leagues": []
    }

    pending_fetches = task_queue.get("pending_api_fetches", [])
    pending_lookups = task_queue.get("pending_lookups", [])
    completed_leagues = set(task_queue.get("completed_leagues", []))

    Log.info(f"Queue Status: {len(pending_fetches)} pending API fetches, {len(pending_lookups)} pending lookups.")

    # Record which leagues had tasks when we started
    initial_pending_league_ids = set()
    for task in pending_fetches + pending_lookups:
        initial_pending_league_ids.add(str(task["league_id"]))

    remaining_fetches = []
    remaining_lookups = []
    api_exhausted = False

    # 1. Process Pending Base API Fetches
    for index, task in enumerate(pending_fetches):
        if api_exhausted:
            remaining_fetches.append(task)
            continue

        league_id = task["league_id"]
        current_date = task["date"]
        str_league = task.get("strLeague", "Unknown League")
        league_url = task.get("league_url", "")
        str_badge = task.get("strBadge", "")

        Log.section_in(f"Resuming Base Fetch for {str_league} on {current_date}")
        fetched_events = {}

        try:
            # API Fetch
            raw_api_events = fetch_api_events(league_id, current_date)
            for raw_event in raw_api_events:
                processed = filter_and_process_event(raw_event, source="api_resumed")
                if processed:
                    fetched_events[processed["idEvent"]] = processed
                    Log.match(f"Added via API: {processed['strHomeTeam']} vs {processed['strAwayTeam']}")

            # Scraper Fallback
            if league_url:
                existing_ids = set(fetched_events.keys())
                scraped_ids = scrape_fallback_events(league_url, current_date, existing_ids)
                
                if scraped_ids:
                    Log.fetch(f"Found {len(scraped_ids)} missing events via Scraper. Looking up...")
                    for ev_id in scraped_ids:
                        if not api_exhausted:
                            time.sleep(0.5)
                            try:
                                raw_lookup = fetch_event_lookup(ev_id)
                                if raw_lookup:
                                    processed = filter_and_process_event(raw_lookup, source="scraped_lookup_resumed")
                                    if processed:
                                        fetched_events[ev_id] = processed
                                        Log.match(f"Added via Scraper: {processed['strHomeTeam']} vs {processed['strAwayTeam']}")
                            except RateLimitException:
                                Log.warn("API 429 Limit hit during scraper lookup! Queueing remaining lookups.")
                                api_exhausted = True

                        if api_exhausted:
                            remaining_lookups.append({
                                "event_id": ev_id,
                                "league_id": league_id,
                                "strLeague": str_league,
                                "date": current_date,
                                "league_url": league_url,
                                "strBadge": str_badge
                            })
                else:
                    Log.info("No missing events found via Scraper for this date.")

            merge_events_into_fixtures(
                fixtures_data, league_id, str_league, str_badge, league_url, list(fetched_events.values())
            )

        except RateLimitException:
            Log.warn(f"API 429 Limit hit while fetching {str_league}! Re-queueing task.")
            api_exhausted = True
            remaining_fetches.append(task)

        Log.section_out(f"Completed processing task for {str_league}")

    # 2. Process Pending Individual Event Lookups
    for task in pending_lookups:
        if api_exhausted:
            remaining_lookups.append(task)
            continue

        ev_id = task["event_id"]
        league_id = task["league_id"]
        str_league = task.get("strLeague", "Unknown League")
        league_url = task.get("league_url", "")
        str_badge = task.get("strBadge", "")

        try:
            time.sleep(0.5)
            raw_lookup = fetch_event_lookup(ev_id)
            if raw_lookup:
                processed = filter_and_process_event(raw_lookup, source="scraped_lookup_resumed")
                if processed:
                    Log.match(f"Added via Scraper (Pending Queue): {processed['strHomeTeam']} vs {processed['strAwayTeam']}")
                    merge_events_into_fixtures(
                        fixtures_data, league_id, str_league, str_badge, league_url, [processed]
                    )
        except RateLimitException:
            Log.warn("API 429 Limit hit during pending lookups! Preserving remaining lookups.")
            api_exhausted = True
            remaining_lookups.append(task)

    # 3. Assess which leagues have been fully completed
    remaining_league_ids = set()
    for task in remaining_fetches + remaining_lookups:
        remaining_league_ids.add(str(task["league_id"]))

    for lid in initial_pending_league_ids:
        if lid not in remaining_league_ids:
            completed_leagues.add(lid)

    # 4. Update Task Queue State
    task_queue["pending_api_fetches"] = remaining_fetches
    task_queue["pending_lookups"] = remaining_lookups
    task_queue["completed_leagues"] = list(completed_leagues)

    if not remaining_fetches and not remaining_lookups:
        task_queue["status"] = "completed"
        Log.success("All queued tasks successfully processed!")
    else:
        task_queue["status"] = "incomplete"
        Log.warn(f"Tasks remaining: {len(remaining_fetches)} fetches, {len(remaining_lookups)} lookups.")

    # 5. Save Updated Files
    with open(FIXTURES_FILE, 'w', encoding='utf-8') as f:
        json.dump(fixtures_data, f, indent=2, ensure_ascii=False)

    with open(TASK_QUEUE_FILE, 'w', encoding='utf-8') as f:
        json.dump(task_queue, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()
