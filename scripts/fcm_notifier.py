import json
import os
import re

import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request

from utils.logger import Logger

SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]
SLUG_INVALID_CHARS = re.compile(r'[^a-z0-9-]')


def slugify_topic(text: str) -> str:
    """FCM topic names only allow [a-zA-Z0-9-_.~%]. Lowercase, spaces to
    hyphens, strip anything else (accents, punctuation, apostrophes)."""
    slug = text.strip().lower().replace(" ", "-")
    slug = SLUG_INVALID_CHARS.sub("", slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug


def get_access_token(service_account_json: str) -> str:
    info = json.loads(service_account_json)
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    credentials.refresh(Request())
    return credentials.token


def send_to_topic(project_id: str, access_token: str, topic: str, title: str, body: str) -> bool:
    """Notification+data message — OS auto-displays when backgrounded, but
    onMessageReceived is skipped in that case. Used only by the manual
    test workflow, not by match notifications."""
    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; UTF-8",
    }
    payload = {
        "message": {
            "topic": topic,
            "notification": {"title": title, "body": body},
        }
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    if resp.status_code != 200:
        Logger.error(f"FCM send to topic '{topic}' failed: {resp.status_code} {resp.text}")
        return False
    Logger.success(f"Sent to '{topic}': \"{title}\"")
    return True


def send_data_message_to_topic(project_id: str, access_token: str, topic: str, data: dict) -> bool:
    """Data-only message (NO top-level 'notification' key). This forces
    Android's FirebaseMessagingService.onMessageReceived to fire regardless
    of foreground/background state, giving the app full control to build
    the notification and its tap action. All values must be strings —
    FCM's data payload rejects non-string values."""
    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; UTF-8",
    }
    string_data = {k: str(v) for k, v in data.items()}
    payload = {"message": {"topic": topic, "data": string_data}}

    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    if resp.status_code != 200:
        Logger.error(f"FCM data send to topic '{topic}' failed: {resp.status_code} {resp.text}")
        return False
    Logger.success(f"Sent data message to '{topic}' (type={data.get('type', '?')})")
    return True
