from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .db import Ride

MATCH_TOLERANCE_SECONDS = 60
DUPLICATE_WINDOW_SECONDS = 48 * 3600
AWAITING_DUPLICATE = ("uploading", "uploaded")


@dataclass(frozen=True)
class StravaActivity:
    id: int
    athlete_id: int
    aspect_type: str
    start_date: str
    device_name: str | None
    sport_type: str | None
    total_photo_count: int

    @property
    def start_time(self) -> int:
        return int(datetime.fromisoformat(self.start_date.replace("Z", "+00:00")).timestamp())


class Kind(Enum):
    MYWHOOSH = "mywhoosh"
    DUPLICATE = "duplicate"
    DUPLICATE_UNVERIFIED = "duplicate_unverified"
    IGNORE = "ignore"


@dataclass(frozen=True)
class Verdict:
    kind: Kind
    reason: str
    ride: Ride | None = None


def is_mywhoosh(activity: StravaActivity) -> bool:
    return activity.sport_type == "VirtualRide" and "mywhoosh" in (activity.device_name or "").lower()


def classify(activity: StravaActivity, nearby_rides: list[Ride], now: float) -> Verdict:
    """Decide what a Strava activity is. Only a ride this bridge pushed to Garmin can ever be a duplicate."""
    if activity.aspect_type != "create":
        return Verdict(Kind.IGNORE, f"aspect {activity.aspect_type}")
    if is_mywhoosh(activity):
        return Verdict(Kind.MYWHOOSH, "MyWhoosh virtual ride")

    ride = next(
        (
            r for r in nearby_rides
            if abs(r.start_time - activity.start_time) <= MATCH_TOLERANCE_SECONDS
            and r.status in AWAITING_DUPLICATE
            and now - r.created <= DUPLICATE_WINDOW_SECONDS
        ),
        None,
    )
    if ride is None:
        return Verdict(Kind.IGNORE, "no ride this bridge uploaded starts at the same time")
    if not (activity.device_name or "").lower().startswith("garmin edge"):
        return Verdict(Kind.IGNORE, f"device {activity.device_name!r} is not a Garmin Edge", ride)
    if activity.total_photo_count != 0:
        return Verdict(Kind.IGNORE, f"has {activity.total_photo_count} photo(s)", ride)
    if ride.strava_mywhoosh_id is None:
        return Verdict(Kind.DUPLICATE_UNVERIFIED, "the MyWhoosh copy's Strava ID is unknown", ride)
    if activity.id <= ride.strava_mywhoosh_id:
        return Verdict(Kind.IGNORE, "not uploaded after the MyWhoosh copy", ride)
    return Verdict(Kind.DUPLICATE, "Garmin copy of a ride this bridge uploaded", ride)
