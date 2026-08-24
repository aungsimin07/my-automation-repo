import os
from datetime import datetime, timezone

from utils.logger import Logger


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    trigger = os.getenv("GITHUB_EVENT_NAME", "unknown")
    Logger.info(f"Heartbeat OK — triggered by '{trigger}' at {now}")


if __name__ == "__main__":
    main()