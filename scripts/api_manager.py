import json
import os
import time
from pathlib import Path

import requests

from logger import Logger

MAX_REQUESTS_PER_MINUTE = 30
MIN_SECONDS_BETWEEN_CALLS = 60 / MAX_REQUESTS_PER_MINUTE  # 2.0s safety spacing

QUEUE_DIR = Path("data/queues")


class APIError(Exception):
    pass


class APIManager:
    """Single choke point for every TheSportsDB request: builds the URL,
    throttles to the free-tier rate limit, tracks how many requests this
    run has made, and owns per-endpoint queue files so callers can push
    unfinished work forward to the next run instead of blocking on it."""

    def __init__(self, script_name: str):
        self.base_url = os.getenv("THESPORTSDB_BASE_URL")
        self.api_key = os.getenv("THESPORTSDB_API_KEY")
        self.script_name = script_name

        if not self.base_url:
            Logger.error("THESPORTSDB_BASE_URL environment variable is not set.", fatal=True)
        if not self.api_key:
            Logger.error("THESPORTSDB_API_KEY environment variable is not set.", fatal=True)

        self.request_count = 0
        self._last_call_at = 0.0

    # -- rate limiting -----------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        wait = MIN_SECONDS_BETWEEN_CALLS - elapsed
        if wait > 0:
            time.sleep(wait)

    # -- notifications (placeholder, not implemented yet) -------------------

    def _notify_error(self, message: str) -> None:
        # Not implemented yet — this is a stand-in so the call sites and
        # control flow are already wired up. Once a real channel exists,
        # replace the log line below with actual dispatch calls.
        Logger.warning(f"[NOTIFY-DEMO] Would dispatch alert -> Telegram/Email: {message}")
        # TODO: send_telegram_alert(message)
        # TODO: send_email_alert(message)

    # -- requests ------------------------------------------------------------

    def request(self, path: str, params: dict = None, retries: int = 2) -> dict:
        """GET a TheSportsDB endpoint, respecting the free-tier rate limit."""
        endpoint = f"{self.base_url.rstrip('/')}/{self.api_key}/{path.lstrip('/')}"

        attempt = 0
        while True:
            self._throttle()
            try:
                response = requests.get(endpoint, params=params, timeout=15)
                self._last_call_at = time.monotonic()
                self.request_count += 1
            except requests.exceptions.RequestException as e:
                attempt += 1
                Logger.warning(f"Request to {path} failed: {e}")
                if attempt > retries:
                    self._notify_error(f"[{self.script_name}] Request to {path} failed after retries: {e}")
                    raise APIError(str(e))
                continue

            if response.status_code == 429:
                Logger.warning("Hit 429 rate limit, backing off 5s...")
                time.sleep(5)
                attempt += 1
                if attempt > retries:
                    self._notify_error(f"[{self.script_name}] Persistent 429 on {path}")
                    raise APIError("Rate limited repeatedly (429).")
                continue

            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                self._notify_error(f"[{self.script_name}] HTTP error on {path}: {e}")
                raise APIError(str(e))

            try:
                data = response.json()
            except ValueError as e:
                self._notify_error(f"[{self.script_name}] Invalid JSON from {path}: {e}")
                raise APIError(f"Invalid JSON response: {e}")

            Logger.success(f"GET {path} -> request #{self.request_count} this run")
            return data

    # -- queue management ------------------------------------------------------

    @staticmethod
    def _queue_path(queue_name: str) -> Path:
        return QUEUE_DIR / f"queue_{queue_name}.json"

    @classmethod
    def load_queue(cls, queue_name: str) -> list:
        path = cls._queue_path(queue_name)
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                Logger.warning(f"{path} is corrupt/empty. Treating as empty queue.")
                return []

    @classmethod
    def save_queue(cls, queue_name: str, items: list) -> None:
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        with open(cls._queue_path(queue_name), "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)

    @classmethod
    def enqueue(cls, queue_name: str, items) -> None:
        """Append one item or a list of items, de-duplicated. Items may be
        strings or JSON-serializable dicts (e.g. {"idLeague", "date"} tasks)."""
        if not isinstance(items, list):
            items = [items]
        existing = cls.load_queue(queue_name)
        seen = {json.dumps(i, sort_keys=True) for i in existing}
        added = []
        for i in items:
            key = json.dumps(i, sort_keys=True)
            if key not in seen:
                seen.add(key)
                added.append(i)
        if added:
            existing.extend(added)
            cls.save_queue(queue_name, existing)
            Logger.info(f"Queued {len(added)} item(s) into queue_{queue_name}.json")

    @classmethod
    def dequeue_batch(cls, queue_name: str, max_items: int) -> list:
        """Pop up to max_items from the front and persist the remainder."""
        items = cls.load_queue(queue_name)
        batch, remainder = items[:max_items], items[max_items:]
        if batch:
            cls.save_queue(queue_name, remainder)
        return batch

    @classmethod
    def queue_length(cls, queue_name: str) -> int:
        return len(cls.load_queue(queue_name))

