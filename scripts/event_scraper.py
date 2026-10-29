import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from utils.logger import Logger

BASE_URL = "https://www.thesportsdb.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SportsDataBot/1.0)"}
REQUEST_TIMEOUT = 15

UTC_RE = re.compile(r'\w{3} (\d{1,2})(?:st|nd|rd|th) (\w+) (\d{4}) (\d{1,2}:\d{2}(?:am|pm)) UTC')
STATUS_RE = re.compile(r'\bStatus\s+([A-Z]{1,5})\b')
ID_RE = re.compile(r'/(?:league|team)/(\d+)-')


def _badge(img):
    if not img or not img.get("src"):
        return None
    return re.sub(r'/(?:tiny|small|medium)$', '', img["src"])


def event_url(event_id: str, slug: str = None) -> str:
    return f"{BASE_URL}/event/{event_id}-{slug}" if slug else f"{BASE_URL}/event/{event_id}"


def scrape_event(event_id: str, slug: str = None):
    """Returns (event_dict_or_None, http_status). The dict mirrors the keys of
    lookupevent.php's events[0], plus strSport, channelPaths, leagueUrl, eventUrl.
    http_status is None on network failure (caller should treat as transient)."""
    url = event_url(event_id, slug)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as e:
        Logger.warning(f"Event page request failed for {url}: {e}")
        return None, None
    if resp.status_code != 200:
        Logger.warning(f"Event page {url} returned HTTP {resp.status_code}")
        return None, resp.status_code

    soup = BeautifulSoup(resp.text, "lxml")
    text = re.sub(r'\s+', ' ', soup.get_text(" ", strip=True))
    event = {"idEvent": str(event_id), "eventUrl": url}

    sport_a = soup.find("a", href=re.compile(r'^/sport/(?!leagues)'))
    event["strSport"] = sport_a.get_text(strip=True) if sport_a else None

    league_a = soup.find("a", href=re.compile(r'^/league/\d+-'))
    if league_a:
        event["idLeague"] = ID_RE.search(league_a["href"]).group(1)
        event["strLeague"] = league_a.get_text(strip=True)
        event["leagueUrl"] = BASE_URL + league_a["href"]

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else None
    event["strEvent"] = title
    if title and " vs " in title:
        event["strHomeTeam"], event["strAwayTeam"] = [t.strip() for t in title.split(" vs ", 1)]

    team_links = soup.find_all("a", href=re.compile(r'^/team/\d+-'))[:2]
    if len(team_links) == 2:
        event["idHomeTeam"] = ID_RE.search(team_links[0]["href"]).group(1)
        event["idAwayTeam"] = ID_RE.search(team_links[1]["href"]).group(1)
        event["strHomeTeamBadge"] = _badge(team_links[0].find("img"))
        event["strAwayTeamBadge"] = _badge(team_links[1].find("img"))

    m = UTC_RE.search(text)
    if m:
        day, month, year, clock = m.groups()
        dt = datetime.strptime(f"{day} {month} {year} {clock.upper()}", "%d %B %Y %I:%M%p")
        event["dateEvent"] = dt.strftime("%Y-%m-%d")
        event["strTime"] = dt.strftime("%H:%M:%S")
        event["strTimestamp"] = dt.strftime("%Y-%m-%dT%H:%M:%S")

    sm = STATUS_RE.search(text)
    event["strStatus"] = sm.group(1) if sm else None

    round_a = soup.find("a", href=re.compile(r'season\.php\?.*[&?]r=\d+'))
    if round_a:
        event["intRound"] = re.search(r'r=(\d+)', round_a["href"]).group(1)

    scores = [s.get_text(strip=True) for s in soup.select("span.spoiler-main-score")]
    if len(scores) == 2:
        event["intHomeScore"] = scores[0] or None
        event["intAwayScore"] = scores[1] or None

    for b in soup.find_all("b"):
        if b.get_text(strip=True) == "Thumb":
            a = b.find_next("a", href=True)
            if a and "/images/media/" in a["href"]:
                event["strThumb"] = a["href"]
            break

    event["channelPaths"] = list(dict.fromkeys(
        a["href"] for a in soup.select("div.event-tv-channel a[href^='/channel/']")
    ))

    missing = [k for k in ("idLeague", "strHomeTeam", "strAwayTeam", "strTimestamp", "strStatus") if not event.get(k)]
    if missing:
        Logger.warning(f"Event {event_id}: could not parse {missing} from {url}")
        return None, 200
    return event, 200