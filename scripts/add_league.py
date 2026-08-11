import argparse
import json
import os
import re
import sys
import requests

# Define the path to the JSON file (relative to the repository root)
DATA_FILE = "data/supported_leagues.json"

# The specific fields we expect every league object to have (intDivision removed)
REQUIRED_KEYS = [
    "idLeague", "idAPIfootball", "idAPIfootballv3", 
    "strLeague", "strBadge", "leagueUrl"
]

def extract_league_id(url):
    """Extracts the numeric league ID from the provided URL."""
    match = re.search(r'/league/(\d+)', url)
    if match:
        return match.group(1)
    return None

def get_sort_key(league):
    """Helper function to convert idAPIfootballv3 string to integer for numerical sorting."""
    try:
        return int(league.get("idAPIfootballv3"))
    except (ValueError, TypeError):
        return 999999  # Safe fallback for missing or non-numeric values

def fetch_league_data(league_id):
    """Fetches league data from the API by ID."""
    api_url = f"https://www.thesportsdb.com/api/v1/json/123/lookupleague.php?id={league_id}"
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        data = response.json()
        if data.get('leagues') and len(data['leagues']) > 0:
            return data['leagues'][0]
    except requests.exceptions.RequestException as e:
        print(f"Error: Failed to fetch data from API for ID {league_id}. Details: {e}")
    return None

def main():
    # 1. Accept leagueUrl as argument
    parser = argparse.ArgumentParser(description="Add a supported league and validate existing entries.")
    parser.add_argument('--leagueUrl', required=True, help="The URL of the league on thesportsdb.com")
    args = parser.parse_args()

    # 2. Extract league id from leagueUrl
    new_league_url = args.leagueUrl
    new_league_id = extract_league_id(new_league_url)

    if not new_league_id:
        print(f"Error: Could not extract league ID from URL: {new_league_url}")
        sys.exit(1)

    # 3. Load existing data safely
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as file:
            try:
                supported_leagues = json.load(file)
            except json.JSONDecodeError:
                supported_leagues = []
    else:
        supported_leagues = []

    # 4. Validate and Backfill Existing Leagues
    for i, league in enumerate(supported_leagues):
        missing_keys = [key for key in REQUIRED_KEYS if key not in league or league.get(key) is None]
        
        if missing_keys:
            league_id = league.get('idLeague')
            print(f"Notice: League ID {league_id} is missing fields: {missing_keys}. Fetching update...")
            
            fresh_data = fetch_league_data(league_id)
            if fresh_data:
                # Update the object with fresh data, ensuring we keep the existing leagueUrl
                supported_leagues[i] = {
                    "idLeague": fresh_data.get("idLeague"),
                    "idAPIfootball": fresh_data.get("idAPIfootball"),
                    "idAPIfootballv3": fresh_data.get("idAPIfootballv3"),
                    "strLeague": fresh_data.get("strLeague"),
                    "strBadge": fresh_data.get("strBadge"),
                    "leagueUrl": league.get("leagueUrl") # Preserve local URL
                }

    # 5. Check if the newly requested league already exists
    league_exists = False
    for league in supported_leagues:
        if league.get('idLeague') == str(new_league_id):
            print(f"Notice: League '{league.get('strLeague')}' (ID: {new_league_id}) already exists. Skipping addition.")
            league_exists = True
            break
            
    # 6. Fetch and add the new league if it does not exist
    if not league_exists:
        print(f"Fetching data for new League ID: {new_league_id}")
        new_league_data = fetch_league_data(new_league_id)
        
        if not new_league_data:
            print(f"Error: No league found in the API response for ID: {new_league_id}")
            sys.exit(1)

        new_league = {
            "idLeague": new_league_data.get("idLeague"),
            "idAPIfootball": new_league_data.get("idAPIfootball"),
            "idAPIfootballv3": new_league_data.get("idAPIfootballv3"),
            "strLeague": new_league_data.get("strLeague"),
            "strBadge": new_league_data.get("strBadge"),
            "leagueUrl": new_league_url
        }
        
        supported_leagues.append(new_league)
        print(f"Success: Added '{new_league['strLeague']}' (API v3 ID: {new_league.get('idAPIfootballv3')}).")

    # 7. Sort leagues by idAPIfootballv3 numerically
    supported_leagues.sort(key=get_sort_key)

    # 8. Save back to the JSON file
    with open(DATA_FILE, 'w', encoding='utf-8') as file:
        json.dump(supported_leagues, file, indent=2)

    print(f"Success: Operations complete. Validated data sorted by API v3 ID and saved to {DATA_FILE}.")

if __name__ == "__main__":
    main()
