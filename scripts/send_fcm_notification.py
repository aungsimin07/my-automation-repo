import json
import os

import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request

from utils.logger import Logger

SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]
TOPIC = "all"


def get_access_token(service_account_json: str) -> str:
    info = json.loads(service_account_json)
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    credentials.refresh(Request())
    return credentials.token


def send_notification(project_id: str, access_token: str, title: str, body: str) -> None:
    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; UTF-8",
    }
    payload = {
        "message": {
            "topic": TOPIC,
            "notification": {
                "title": title,
                "body": body,
            },
        }
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    if resp.status_code != 200:
        Logger.error(f"FCM send failed: {resp.status_code} {resp.text}", fatal=True)

    Logger.success(f"Notification sent to topic '{TOPIC}': \"{title}\" — \"{body}\"")


def main():
    title = os.getenv("NOTIFICATION_TITLE", "").strip()
    body = os.getenv("NOTIFICATION_MESSAGE", "").strip()
    project_id = os.getenv("FCM_PROJECT_ID", "").strip()
    service_account_json = os.getenv("FCM_SERVICE_ACCOUNT_JSON", "").strip()

    if not title:
        Logger.error("NOTIFICATION_TITLE is required.", fatal=True)
    if not body:
        Logger.error("NOTIFICATION_MESSAGE is required.", fatal=True)
    if not project_id:
        Logger.error("FCM_PROJECT_ID is required.", fatal=True)
    if not service_account_json:
        Logger.error("FCM_SERVICE_ACCOUNT_JSON is required.", fatal=True)

    access_token = get_access_token(service_account_json)
    send_notification(project_id, access_token, title, body)


if __name__ == "__main__":
    main()
