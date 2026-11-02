import logging
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from garminconnect import Garmin

log = logging.getLogger(__name__)


class GarminNeedsLogin(RuntimeError):
    pass


def _no_mfa_prompt() -> str:
    raise GarminNeedsLogin("Garmin asked for an MFA code; run the garmin_login command in the container")


class GarminClient:
    def __init__(self, username: str, password: str, token_dir: Path, prompt_mfa: Callable[[], str] = _no_mfa_prompt):
        self._username = username
        self._password = password
        self._token_dir = token_dir
        self._prompt_mfa = prompt_mfa
        self._api: Garmin | None = None

    def login(self) -> None:
        self._token_dir.mkdir(parents=True, exist_ok=True)
        api = Garmin(self._username, self._password, prompt_mfa=self._prompt_mfa)
        # Loads saved tokens when present, otherwise logs in and saves them to token_dir.
        api.login(tokenstore=str(self._token_dir))
        self._api = api

    def _client(self) -> Garmin:
        if self._api is None:
            self.login()
        return self._api

    def has_activity_near(self, start_time: int, tolerance_seconds: int = 60) -> bool:
        day = datetime.fromtimestamp(start_time, timezone.utc).date()
        activities = self._client().get_activities_by_date(
            (day - timedelta(days=1)).isoformat(), (day + timedelta(days=1)).isoformat()
        )
        for activity in activities:
            gmt = activity.get("startTimeGMT")
            if not gmt:
                continue
            started = datetime.strptime(gmt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
            if abs(started - start_time) <= tolerance_seconds:
                return True
        return False

    def upload(self, fit_data: bytes, name: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{name}.fit"
            path.write_bytes(fit_data)
            try:
                self._client().upload_activity(str(path))
            except Exception as e:
                # Garmin answers 409 when it already has this exact file.
                if "409" in str(e):
                    log.info("Garmin already has %s", name)
                    return
                raise
        log.info("Uploaded %s to Garmin Connect", name)
