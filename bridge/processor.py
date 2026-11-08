import logging
import time
from dataclasses import replace
from pathlib import Path

import requests

from . import fit
from .classify import MATCH_TOLERANCE_SECONDS, Kind, StravaActivity, classify
from .db import Database, Ride
from .garmin import GarminClient
from .notify import Notifier
from .strava_web import RememberToken, StravaWeb, StravaWebError, write_private

log = logging.getLogger(__name__)

TOKEN_WARNING_SECONDS = 5 * 24 * 3600


def strava_link(activity_id: int) -> str:
    return f"https://www.strava.com/activities/{activity_id}"


def describe(start: int) -> str:
    return f"ride from {time.strftime('%a %b %-d %H:%M', time.localtime(start))}"


def label(activity: StravaActivity) -> str:
    name = f' "{activity.name}"' if activity.name else ""
    return f"Strava activity {activity.id}{name} ({strava_link(activity.id)})"


class Processor:
    def __init__(
        self,
        db: Database,
        garmin: GarminClient,
        strava_web: StravaWeb,
        remember_token: RememberToken,
        notifier: Notifier,
        dry_run: bool,
        backup_dir: Path,
    ):
        self._db = db
        self._garmin = garmin
        self._strava_web = strava_web
        self._remember_token = remember_token
        self._notifier = notifier
        self._dry_run = dry_run
        self._backup_dir = backup_dir

    def log_connections(self) -> None:
        checks = (("Strava", self._strava_status), ("Garmin", self._garmin_status), ("Telegram", self._notifier.status))
        for service, status in checks:
            try:
                log.info("%s: %s", service, status())
            except Exception as e:
                log.error("%s: connection failed: %s", service, e)

    def _strava_status(self) -> str:
        self._strava_web.check_login()
        expires = time.strftime("%Y-%m-%d", time.localtime(self._remember_token.expires_at()))
        return f"connected, token expires {expires}"

    def _garmin_status(self) -> str:
        self._garmin.login()
        return "connected"

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
                f"Probable Garmin duplicate {label(activity)} not deleted because the MyWhoosh copy wasn't seen."
            )

    def _sync_from_strava(self, activity: StravaActivity, nearby: list[Ride]) -> None:
        known = next((r for r in nearby if r.status != "uploading"), None)
        if known:
            if known.strava_mywhoosh_id is None:
                self._db.save_ride(replace(known, strava_mywhoosh_id=activity.id))
            return
        data = self._strava_web.download_original(activity.id)
        self._notifier.send(f"Downloaded MyWhoosh ride {label(activity)} from Strava.")
        self._push_to_garmin(activity, data)

    def _push_to_garmin(self, activity: StravaActivity, data: bytes) -> None:
        start = fit.start_time(data)
        ride_id = str(activity.id)
        prior = self._db.ride(ride_id)
        ride = Ride(ride_id, start, activity.id, None, "uploading", prior.created if prior else time.time())
        if self._garmin.has_activity_near(start):
            # A crash between upload and bookkeeping leaves "uploading"; that upload was ours.
            status = "uploaded" if prior and prior.status == "uploading" else "already_on_garmin"
            self._db.save_ride(replace(ride, status=status))
            log.info("Garmin already has MyWhoosh ride %s; marked %s", ride_id, status)
            if status == "uploaded":
                self._notifier.send(f"Uploaded {label(activity)} to Garmin Connect ({describe(start)}).")
            else:
                self._notifier.send(f"Garmin already had {label(activity)} ({describe(start)}); nothing uploaded.")
            return
        self._db.save_ride(ride)
        self._garmin.upload(fit.as_garmin_edge(data), f"mywhoosh_{ride_id}")
        self._db.save_ride(replace(ride, status="uploaded"))
        self._notifier.send(f"Uploaded {label(activity)} to Garmin Connect ({describe(start)}).")

    def _delete_duplicate(self, activity: StravaActivity, ride: Ride) -> None:
        # The event's own fields can't be trusted to authorize a delete, so check Strava's copy of the file.
        original = self._strava_web.download_original(activity.id)
        self._notifier.send(f"Downloaded probable Garmin duplicate {label(activity)} from Strava.")
        if not fit.is_garmin_edge_copy(original, ride.start_time, MATCH_TOLERANCE_SECONDS):
            self._db.save_ride(replace(ride, strava_duplicate_id=activity.id, status="duplicate_needs_review"))
            self._notifier.send(f"{label(activity)} doesn't match the ride this bridge uploaded; not deleted.")
            return
        if self._dry_run:
            self._db.save_ride(replace(ride, strava_duplicate_id=activity.id, status="duplicate_dry_run"))
            self._notifier.send(f"DRY_RUN: verified, would delete Garmin duplicate {label(activity)}")
            return
        backup = self._backup_dir / f"{activity.id}.fit"
        write_private(backup, original)
        try:
            self._strava_web.delete_activity(activity.id)
        except (StravaWebError, requests.RequestException) as e:
            self._db.save_ride(replace(ride, strava_duplicate_id=activity.id, status="duplicate_delete_failed"))
            self._notifier.send(
                f"Couldn't delete Garmin duplicate {label(activity)} ({e}). Delete it by hand; its original is saved as {backup}."
            )
            return
        self._db.save_ride(replace(ride, strava_duplicate_id=activity.id, status="duplicate_deleted"))
        self._notifier.send(f"Deleted Garmin duplicate {label(activity)} from Strava; its original is saved as {backup}.")

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
