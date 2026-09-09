import json

from fcm_notifier import slugify_topic, send_data_message_to_topic
from utils.logger import Logger

MAX_CHANNELS_IN_PAYLOAD = 5
PAYLOAD_SIZE_WARNING_BYTES = 3800  # stay clear of FCM's ~4KB data payload ceiling


def build_started_text(event: dict):
    home, away = event.get("strHomeTeam"), event.get("strAwayTeam")
    if not home or not away:
        return None
    title = f"Match Started: {home} vs {away}"
    body = "We are underway! Tap here to open the match center for live commentary."
    return title, body


def _trim_channel_for_payload(channel: dict) -> dict:
    """Keep only what's needed to start playback immediately, even from a
    cold app start with no local cache. Drops verbose optional fields
    (cookies, custom headers, catchup, health-check status) that add
    payload size without adding value for this purpose."""
    trimmed = {
        "duration": channel.get("duration", -1),
        "title": channel.get("title"),
        "url": channel.get("url"),
    }
    if channel.get("tvg"):
        trimmed["tvg"] = channel["tvg"]
    if channel.get("groupTitle"):
        trimmed["groupTitle"] = channel["groupTitle"]
    if channel.get("quality"):
        trimmed["quality"] = channel["quality"]
    vlc = (channel.get("options") or {}).get("vlc")
    if vlc:
        trimmed["options"] = {"vlc": vlc}
    return trimmed


def get_channels_for_event(channel_entries: list, event: dict) -> list:
    tvg_ids = set(event.get("metadata", {}).get("channels", []))
    if not tvg_ids:
        return []
    return [c for c in channel_entries if c.get("tvg", {}).get("id") in tvg_ids]


def notify_match_started(event: dict, channel_entries: list, project_id: str, access_token: str) -> bool:
    already_sent = event.setdefault("metadata", {}).setdefault("notifications_sent", [])
    if "started" in already_sent:
        return False  # dedup guard — never double-fire

    text = build_started_text(event)
    if text is None:
        return False
    title, body = text

    str_league = event.get("strLeague")
    str_event = event.get("strEvent")
    topics = []
    if str_league:
        topics.append(slugify_topic(str_league))
    if str_event:
        topics.append(slugify_topic(str_event))
    if not topics:
        Logger.warning(f"Event {event.get('idEvent')}: no strLeague/strEvent to build topics from, skipping.")
        return False

    matched_channels = get_channels_for_event(channel_entries, event)
    if len(matched_channels) > MAX_CHANNELS_IN_PAYLOAD:
        Logger.warning(
            f"Event {event.get('idEvent')}: {len(matched_channels)} linked channel(s), "
            f"truncating to {MAX_CHANNELS_IN_PAYLOAD} for notification payload size."
        )
        matched_channels = matched_channels[:MAX_CHANNELS_IN_PAYLOAD]
    trimmed_channels = [_trim_channel_for_payload(c) for c in matched_channels]

    data = {
        "type": "match_started",
        "title": title,
        "body": body,
        "eventJson": json.dumps(event),
        "channelsJson": json.dumps(trimmed_channels),
    }
    thumb = event.get("strThumb")
    if thumb:
        data["image"] = thumb

    approx_size = sum(len(k) + len(str(v)) for k, v in data.items())
    if approx_size > PAYLOAD_SIZE_WARNING_BYTES:
        Logger.warning(
            f"Event {event.get('idEvent')}: notification payload ~{approx_size} bytes, "
            f"close to/over FCM's 4KB limit. Send may be rejected."
        )

    any_sent = False
    for topic in topics:
        if send_data_message_to_topic(project_id, access_token, topic, data):
            any_sent = True

    if any_sent:
        already_sent.append("started")

    return any_sent
