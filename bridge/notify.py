import logging
import time

import requests

from .db import Database

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, db: Database, bot_token: str | None, chat_id: str | None):
        self._db = db
        self._bot_token = bot_token
        self._chat_id = chat_id

    def send(self, text: str, key: str | None = None, every_seconds: float = 24 * 3600) -> None:
        """Send a Telegram message; with a key, at most once per every_seconds for that key."""
        if key:
            last = self._db.get(f"notified:{key}")
            if last and time.time() - float(last) < every_seconds:
                return
        log.warning("Notification: %s", text)
        if not (self._bot_token and self._chat_id):
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                json={"chat_id": self._chat_id, "text": f"mywhoosh-garmin-bridge: {text}", "disable_web_page_preview": True},
                timeout=10,
            ).raise_for_status()
        except requests.RequestException as e:
            log.error("Telegram notification failed: %s", e)
            return
        if key:
            self._db.put(f"notified:{key}", str(time.time()))
