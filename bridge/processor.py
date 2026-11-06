import logging
import time
from dataclasses import replace

import requests

from . import fit
from .classify import MATCH_TOLERANCE_SECONDS, Kind, StravaActivity, classify
from .db import Database, Ride
from .garmin import GarminClient
from .notify import Notifier
from .strava_web import RememberToken, StravaWeb, StravaWebError

log = logging.getLogger(__name__)

TOKEN_WARNING_SECONDS = 5 * 24 * 3600


def strava_link(activity_id: int) -> str:
    return f"https://www.strava.com/activities/{activity_id}"


def describe(start: int) -> str:
    return f"ride from {time.strftime('%a %b %-d %H:%M', time.localtime(start))}"


class Processor:
    def __init__(
        self,
        db: Database,
        garmin: GarminClient,
        strava_web: StravaWeb,
        remember_token: RememberToken,
        notifier: Notifier,
        dry_run: bool,
    ):
        self._db = db
        self._garmin = garmin
        self._strava_web = strava_web
        self._remember_token = remember_token
        self._notifier = notifier
        self._dry_run = dry_run

    def handle_activity(self, payload: dict) -> None:
        activity = StravaActivity(**payload)
        nearby = self._db.rides_near(activity.start_time, MATCH_TOLERANCE_SECONDS)
        verdict = classify(activity, nearby, time.time())
        log.info(
            "Strava activity %s (device %r, sport %s, photos %d): %s, %s",
            activity.id, activity.device_name, activity.sport_type, activity.total_photo_count,
            verdict.kind.value, verdict.reason,
        )
        if verdict.kind is Kind.MYWHOOSH:
            self._sync_from_strava(activity, nearby)
        elif verdict.kind is Kind.DUPLICATE:
            self._delete_duplicate(activity, verdict.ride)
        elif verdict.kind is Kind.DUPLICATE_UNVERIFIED:
            self._db.save_ride(replace(verdict.ride, strava_duplicate_id=activity.id, status="duplicate_needs_review"))
            self._notifier.send(
                f"Probable Garmin duplicate on Strava, not deleted because the MyWhoosh copy wasn't seen: "
                f"{strava_link(activity.id)}"
            )

    def _sync_from_strava(self, activity: StravaActivity, nearby: list[Ride]) -> None:
        known = next((r for r in nearby if r.status != "uploading"), None)
        if known:
            if known.strava_mywhoosh_id is None:
                self._db.save_ride(replace(known, strava_mywhoosh_id=activity.id))
            return
        self._push_to_garmin(activity.id, self._strava_web.download_original(activity.id))

    def _push_to_garmin(self, activity_id: int, data: bytes) -> None:
        start = fit.start_time(data)
        ride_id = str(activity_id)
        prior = self._db.ride(ride_id)
        ride = Ride(ride_id, start, activity_id, None, "uploading", prior.created if prior else time.time())
        if self._garmin.has_activity_near(start):
            # A crash between upload and bookkeeping leaves "uploading"; that upload was ours.
            status = "uploaded" if prior and prior.status == "uploading" else "already_on_garmin"
            self._db.save_ride(replace(ride, status=status))
            log.info("Garmin already has MyWhoosh ride %s; marked %s", ride_id, status)
            if status == "uploaded":
                self._notifier.send(f"Uploaded {describe(start)} to Garmin Connect.")
            else:
                self._notifier.send(f"Garmin already had {describe(start)}; nothing uploaded.")
            return
        self._db.save_ride(ride)
        self._garmin.upload(fit.as_garmin_edge(data), f"mywhoosh_{ride_id}")
        self._db.save_ride(replace(ride, status="uploaded"))
        self._notifier.send(f"Uploaded {describe(start)} to Garmin Connect.")

    def _delete_duplicate(self, activity: StravaActivity, ride: Ride) -> None:
        if self._dry_run:
            self._db.save_ride(replace(ride, strava_duplicate_id=activity.id, status="duplicate_dry_run"))
            self._notifier.send(f"DRY_RUN: would delete Garmin duplicate {strava_link(activity.id)}")
            return
        try:
            self._strava_web.delete_activity(activity.id)
        except (StravaWebError, requests.RequestException) as e:
            self._db.save_ride(replace(ride, strava_duplicate_id=activity.id, status="duplicate_delete_failed"))
            self._notifier.send(f"Couldn't delete Garmin duplicate {strava_link(activity.id)} ({e}). Delete it by hand.")
            return
        self._db.save_ride(replace(ride, strava_duplicate_id=activity.id, status="duplicate_deleted"))
        self._notifier.send(f"Deleted the Garmin duplicate {strava_link(activity.id)} from Strava.")

    def check_remember_token(self) -> None:
        """Warn before the Strava cookie expires."""
        try:
            expires_at = self._remember_token.expires_at()
        except StravaWebError as e:
            self._notifier.send(f"{e}. Rides can't sync until it's replaced.", key="strava_token")
            return
        if expires_at is None:
            self._notifier.send("STRAVA_REMEMBER_TOKEN is not set; rides can't sync.", key="strava_token")
        elif expires_at - time.time() < TOKEN_WARNING_SECONDS:
            when = time.strftime("%Y-%m-%d", time.localtime(expires_at))
            self._notifier.send(f"Strava remember token expires {when}; paste a fresh one into the container.", key="strava_token")
