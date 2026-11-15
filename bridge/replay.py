"""Queue a MyWhoosh ride the bridge missed: `docker exec mywhoosh-garmin-bridge python -m bridge.replay <activity id or URL>`."""
import re
import sys
from datetime import datetime, timezone

from . import fit
from .config import Settings
from .db import Database
from .strava_web import RememberToken, StravaWeb

ACTIVITY_ID = re.compile(r"^(?:https?://www\.strava\.com/activities/)?(\d+)")


def parse_activity_id(arg: str) -> int:
    match = ACTIVITY_ID.match(arg.strip())
    if not match:
        raise ValueError(f"Not a Strava activity ID or URL: {arg}")
    return int(match.group(1))


def replay(activity_id: int, athlete_id: int, strava_web: StravaWeb, db: Database) -> None:
    start = fit.start_time(strava_web.download_original(activity_id))
    db.enqueue("strava_activity", {
        "id": activity_id,
        "athlete_id": athlete_id,
        "aspect_type": "create",
        "start_date": datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "device_name": "MyWhoosh",
        "sport_type": "VirtualRide",
    })


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m bridge.replay <Strava activity ID or URL>")
    settings = Settings.from_env()
    activity_id = parse_activity_id(sys.argv[1])
    strava_web = StravaWeb(RememberToken(settings.strava_remember_token, settings.strava_token_file), settings.strava_athlete_id)
    replay(activity_id, settings.strava_athlete_id, strava_web, Database(settings.db_path))
    print(f"Queued Strava activity {activity_id}; the running bridge will process it shortly.")


if __name__ == "__main__":
    main()
