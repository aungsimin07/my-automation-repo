import json

from fcm_notifier import slugify_topic, send_data_message_to_topic
from utils.logger import Logger

MAX_CHANNELS_IN_PAYLOAD = 5
PAYLOAD_SIZE_WARNING_BYTES = 3800  # stay clear of FCM's ~4KB data payload ceiling

# Not in production yet — no per-league/per-match subscriptions set up on
# the Android side. Broadcasting to "all" for now. To switch back to
# per-league/per-match targeting later, set this to False.
BROADCAST_TO_ALL_ONLY = True
ALL_TOPIC = "all"


def build_started_text(event: dict):
    home, away = event.get("strHomeTeam"), event.get("strAwayTeam")
    if not home or not away:
        Logger.warning(f"Event {event.get('idEvent')}: missing strHomeTeam/strAwayTeam, cannot build notification text.")
        return None
    title = f"Match Started: {home} vs {away}"
    body = "We are underway! Tap here to open the match center for live commentary."
    return title, body


def _trim_channel_for_payload(channel: dict) -> dict:
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


def _resolve_topics(event: dict) -> list:
    """Returns the list of topics this notification should be sent to.
    Currently hardcoded to just ['all'] — see BROADCAST_TO_ALL_ONLY."""
    if BROADCAST_TO_ALL_ONLY:
        Logger.info(f"Event {event.get('idEvent')}: BROADCAST_TO_ALL_ONLY is set, targeting topic '{ALL_TOPIC}' only.")
        return [ALL_TOPIC]

    str_league = event.get("strLeague")
    str_event = event.get("strEvent")
    topics = []
    if str_league:
        topics.append(slugify_topic(str_league))
    if str_event:
        topics.append(slugify_topic(str_event))
    return topics


def notify_match_started(event: dict, channel_entries: list, project_id: str, access_token: str) -> bool:
    event_id = event.get("idEvent")
    Logger.info(f"notify_match_started: evaluating event {event_id} ('{event.get('strEvent')}')")

    already_sent = event.setdefault("metadata", {}).setdefault("notifications_sent", [])
    if "started" in already_sent:
        Logger.info(f"Event {event_id}: 'started' notification already sent previously, skipping (dedup guard).")
        return False

    text = build_started_text(event)
    if text is None:
        return False
    title, body = text
    Logger.info(f"Event {event_id}: notification text — title='{title}', body='{body}'")

    topics = _resolve_topics(event)
    if not topics:
        Logger.warning(f"Event {event_id}: no topics resolved, skipping send.")
        return False
    Logger.info(f"Event {event_id}: resolved topic(s): {topics}")

    matched_channels = get_channels_for_event(channel_entries, event)
    Logger.info(f"Event {event_id}: {len(matched_channels)} linked channel(s) found for payload.")
    if len(matched_channels) > MAX_CHANNELS_IN_PAYLOAD:
        Logger.warning(
            f"Event {event_id}: {len(matched_channels)} linked channel(s), "
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
        Logger.info(f"Event {event_id}: including image (strThumb): {thumb}")
    else:
        Logger.info(f"Event {event_id}: no strThumb present, sending without image.")

    approx_size = sum(len(k) + len(str(v)) for k, v in data.items())
    Logger.info(f"Event {event_id}: approx payload size {approx_size} bytes.")
    if approx_size > PAYLOAD_SIZE_WARNING_BYTES:
        Logger.warning(
            f"Event {event_id}: notification payload ~{approx_size} bytes, "
            f"close to/over FCM's 4KB limit. Send may be rejected."
        )

    any_sent = False
    for topic in topics:
        Logger.info(f"Event {event_id}: sending to topic '{topic}'...")
        sent = send_data_message_to_topic(project_id, access_token, topic, data)
        Logger.info(f"Event {event_id}: send to '{topic}' -> {'success' if sent else 'FAILED'}")
        if sent:
            any_sent = True

    if any_sent:
        already_sent.append("started")
        Logger.success(f"Event {event_id}: marked 'started' as sent in metadata.notifications_sent.")
    else:
        Logger.error(f"Event {event_id}: all topic sends failed, NOT marking as sent (will retry next status_sync cycle).")

    return any_sent
