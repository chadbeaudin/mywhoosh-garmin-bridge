import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REQUIRED = (
    "MYWHOOSH_EMAIL",
    "MYWHOOSH_PASSWORD",
    "GARMIN_USERNAME",
    "GARMIN_PASSWORD",
    "BRIDGE_SECRET",
    "STRAVA_ATHLETE_ID",
)


@dataclass(frozen=True)
class Settings:
    mywhoosh_email: str
    mywhoosh_password: str
    garmin_username: str
    garmin_password: str
    bridge_secret: str
    strava_athlete_id: int
    strava_remember_token: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    data_dir: Path
    dry_run: bool
    reconcile_interval_hours: float
    port: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.db"

    @property
    def garmin_token_dir(self) -> Path:
        return self.data_dir / "garmin_tokens"

    @property
    def strava_token_file(self) -> Path:
        return self.data_dir / "strava_remember_token"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        missing = [k for k in REQUIRED if not env.get(k)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        return cls(
            mywhoosh_email=env["MYWHOOSH_EMAIL"],
            mywhoosh_password=env["MYWHOOSH_PASSWORD"],
            garmin_username=env["GARMIN_USERNAME"],
            garmin_password=env["GARMIN_PASSWORD"],
            bridge_secret=env["BRIDGE_SECRET"],
            strava_athlete_id=int(env["STRAVA_ATHLETE_ID"]),
            strava_remember_token=env.get("STRAVA_REMEMBER_TOKEN") or None,
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=env.get("TELEGRAM_CHAT_ID") or None,
            data_dir=Path(env.get("DATA_DIR", "/data")),
            dry_run=env.get("DRY_RUN", "true").strip().lower() != "false",
            reconcile_interval_hours=float(env.get("RECONCILE_INTERVAL_HOURS", "6")),
            port=int(env.get("PORT", "8080")),
        )
