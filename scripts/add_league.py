import argparse
import json
import os
import re
import sys
import requests

# Define the path to the JSON file (relative to the repository root)
DATA_FILE = "data/supported_leagues.json"

def extract_league_id(url):
    """Extracts the numeric league ID from the provided URL."""
    match = re.search(r'/league/(\d+)', url)
    if match:
        return match.group(1)
    return None

def main():
    # 1. Accept leagueUrl as argument
    parser = argparse.ArgumentParser(description="Add a supported league to the JSON data.")
    parser.add_argument('--leagueUrl', required=True, help="The URL of the league on thesportsdb.com")
    args = parser.parse_args()

    # 2. Extract league id from leagueUrl
    league_url = args.leagueUrl
    league_id = extract_league_id(league_url)

    if not league_id:
        print(f"Error: Could not extract league ID from URL: {league_url}")
        sys.exit(1)

    print(f"Extracted League ID: {league_id}")

    # 3. Request the API Endpoint with the extracted id
    api_url = f"https://www.thesportsdb.com/api/v1/json/123/lookupleague.php?id={league_id}"
    
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error: Failed to fetch data from API. Details: {e}")
        sys.exit(1)

    # 4. Parse the response and grab specific values
    if not data.get('leagues') or len(data['leagues']) == 0:
        print(f"Error: No league found in the API response for ID: {league_id}")
        sys.exit(1)

    league_data = data['leagues'][0]
    
    new_league = {
        "idLeague": league_data.get("idLeague"),
        "idAPIfootball": league_data.get("idAPIfootball"),
        "idAPIfootballv3": league_data.get("idAPIfootballv3"),
        "strLeague": league_data.get("strLeague"),
        "strBadge": league_data.get("strBadge"),
        "leagueUrl": league_url
    }

    # 5. Store in supported_leagues.json, ensuring uniqueness
    # Ensure the data directory exists
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)

    # Load existing data safely
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as file:
            try:
                supported_leagues = json.load(file)
            except json.JSONDecodeError:
                supported_leagues = []
    else:
        supported_leagues = []

    # Check for duplicates by idLeague
    for existing_league in supported_leagues:
        if existing_league.get('idLeague') == new_league['idLeague']:
            print(f"Notice: League '{new_league['strLeague']}' (ID: {new_league['idLeague']}) already exists. Skipping addition.")
            sys.exit(0) # Exit cleanly, not an error for the CI/CD pipeline

    # Append and save
    supported_leagues.append(new_league)

    with open(DATA_FILE, 'w', encoding='utf-8') as file:
        json.dump(supported_leagues, file, indent=2)

    print(f"Success: Added '{new_league['strLeague']}' to {DATA_FILE}.")

if __name__ == "__main__":
    main()
