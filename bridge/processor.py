import logging
import time
from dataclasses import replace

import requests

from . import fit
from .classify import MATCH_TOLERANCE_SECONDS, Kind, StravaActivity, classify
from .db import Database, Ride
from .garmin import GarminClient
from .mywhoosh import Activity, MyWhooshClient
from .notify import Notifier
from .strava_web import RememberToken, StravaWeb, StravaWebError

log = logging.getLogger(__name__)

RECENT_LIMIT = 5
# Leave rides this young to the Strava event path, so the duplicate check knows the MyWhoosh copy's ID.
RECONCILE_MIN_AGE_SECONDS = 4 * 3600
TOKEN_WARNING_SECONDS = 5 * 24 * 3600


class RetryLater(Exception):
    pass


def strava_link(activity_id: int) -> str:
    return f"https://www.strava.com/activities/{activity_id}"


def describe(activity: Activity, start: int) -> str:
    when = time.strftime("%a %b %-d %H:%M", time.localtime(start))
    return f"\"{activity.name}\" ({when})" if activity.name else f"ride from {when}"


class Processor:
    def __init__(
        self,
        db: Database,
        mywhoosh: MyWhooshClient,
        garmin: GarminClient,
        strava_web: StravaWeb,
        remember_token: RememberToken,
        notifier: Notifier,
        dry_run: bool,
    ):
        self._db = db
        self._mywhoosh = mywhoosh
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
        for candidate in self._unsynced_recent():
            data = self._mywhoosh.download(candidate)
            start = fit.start_time(data)
            if abs(start - activity.start_time) <= MATCH_TOLERANCE_SECONDS:
                self._push_to_garmin(candidate, start, data, strava_mywhoosh_id=activity.id)
                return
        raise RetryLater(f"No MyWhoosh ride matches Strava activity {activity.id} yet")

    def _unsynced_recent(self) -> list[Activity]:
        return [
            a for a in self._mywhoosh.recent_activities(RECENT_LIMIT)
            if (ride := self._db.ride(a.id)) is None or ride.status == "uploading"
        ]

    def _push_to_garmin(self, activity: Activity, start: int, data: bytes, strava_mywhoosh_id: int | None) -> None:
        prior = self._db.ride(activity.id)
        ride = Ride(activity.id, start, strava_mywhoosh_id, None, "uploading", prior.created if prior else time.time())
        if self._garmin.has_activity_near(start):
            # A crash between upload and bookkeeping leaves "uploading"; that upload was ours.
            status = "uploaded" if prior and prior.status == "uploading" else "already_on_garmin"
            self._db.save_ride(replace(ride, status=status))
            log.info("Garmin already has MyWhoosh ride %s; marked %s", activity.id, status)
            if status == "uploaded":
                self._notifier.send(f"Uploaded {describe(activity, start)} to Garmin Connect.")
            else:
                self._notifier.send(f"Garmin already had {describe(activity, start)}; nothing uploaded.")
            return
        self._db.save_ride(ride)
        self._garmin.upload(fit.as_garmin_edge(data), f"mywhoosh_{activity.id}")
        self._db.save_ride(replace(ride, status="uploaded"))
        self._notifier.send(f"Uploaded {describe(activity, start)} to Garmin Connect.")

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

    def reconcile(self) -> None:
        """Catch rides whose Strava event never arrived, and warn before the Strava cookie expires."""
        self._check_remember_token()
        installed_at = float(self._db.get("installed_at") or 0)
        now = time.time()
        for candidate in self._unsynced_recent():
            data = self._mywhoosh.download(candidate)
            start = fit.start_time(data)
            if start < installed_at:
                self._db.save_ride(Ride(candidate.id, start, None, None, "before_install", now))
            elif now - start >= RECONCILE_MIN_AGE_SECONDS:
                log.info("Reconcile: MyWhoosh ride %s was never synced", candidate.id)
                self._push_to_garmin(candidate, start, data, strava_mywhoosh_id=None)

    def _check_remember_token(self) -> None:
        try:
            expires_at = self._remember_token.expires_at()
        except StravaWebError as e:
            self._notifier.send(f"{e}. Duplicates can't be deleted until it's replaced.", key="strava_token")
            return
        if expires_at is None:
            self._notifier.send("STRAVA_REMEMBER_TOKEN is not set; duplicates can't be deleted.", key="strava_token")
        elif expires_at - time.time() < TOKEN_WARNING_SECONDS:
            when = time.strftime("%Y-%m-%d", time.localtime(expires_at))
            self._notifier.send(f"Strava remember token expires {when}; paste a fresh one into the container.", key="strava_token")
