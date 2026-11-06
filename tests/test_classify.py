import time

import pytest

from bridge.classify import Kind, StravaActivity, classify
from bridge.db import Ride

from .conftest import RIDE_START, garmin_duplicate_payload, strava_payload

NOW = time.time()


def uploaded_ride(**overrides) -> Ride:
    fields = dict(ride_id="w1", start_time=RIDE_START, strava_mywhoosh_id=1001,
                  strava_duplicate_id=None, status="uploaded", created=NOW - 600)
    fields.update(overrides)
    return Ride(**fields)


def test_start_time_parses_strava_utc_date():
    assert StravaActivity(**strava_payload()).start_time == RIDE_START


def test_mywhoosh_virtual_ride_is_synced():
    assert classify(StravaActivity(**strava_payload()), [], NOW).kind is Kind.MYWHOOSH


def test_mywhoosh_device_but_not_virtual_ride_is_ignored():
    assert classify(StravaActivity(**strava_payload(sport_type="Ride")), [], NOW).kind is Kind.IGNORE


def test_garmin_copy_of_uploaded_ride_is_duplicate():
    verdict = classify(StravaActivity(**garmin_duplicate_payload()), [uploaded_ride()], NOW)
    assert verdict.kind is Kind.DUPLICATE
    assert verdict.ride.ride_id == "w1"


def test_ride_still_uploading_counts_as_awaiting_duplicate():
    verdict = classify(StravaActivity(**garmin_duplicate_payload()), [uploaded_ride(status="uploading")], NOW)
    assert verdict.kind is Kind.DUPLICATE


@pytest.mark.parametrize(
    ("payload", "rides"),
    [
        pytest.param(garmin_duplicate_payload(aspect_type="update"), [uploaded_ride()], id="update event"),
        pytest.param(garmin_duplicate_payload(), [], id="no ride uploaded by bridge"),
        pytest.param(garmin_duplicate_payload(start_date="2025-11-02T16:01:01Z"), [uploaded_ride()], id="start 61s off"),
        pytest.param(garmin_duplicate_payload(), [uploaded_ride(status="already_on_garmin")], id="bridge didn't upload it"),
        pytest.param(garmin_duplicate_payload(), [uploaded_ride(status="duplicate_deleted")], id="duplicate already handled"),
        pytest.param(garmin_duplicate_payload(), [uploaded_ride(created=NOW - 49 * 3600)], id="older than 48h"),
        pytest.param(garmin_duplicate_payload(device_name="Wahoo ELEMNT"), [uploaded_ride()], id="not a Garmin Edge"),
        pytest.param(garmin_duplicate_payload(device_name=None), [uploaded_ride()], id="no device"),
        pytest.param(garmin_duplicate_payload(total_photo_count=1), [uploaded_ride()], id="has photos"),
        pytest.param(garmin_duplicate_payload(id=1000), [uploaded_ride()], id="older than MyWhoosh copy"),
        pytest.param(garmin_duplicate_payload(id=1001), [uploaded_ride()], id="is the MyWhoosh copy"),
    ],
)
def test_each_rule_alone_prevents_deletion(payload, rides):
    assert classify(StravaActivity(**payload), rides, NOW).kind is Kind.IGNORE


def test_duplicate_of_ride_without_a_strava_id_needs_review():
    verdict = classify(StravaActivity(**garmin_duplicate_payload()), [uploaded_ride(strava_mywhoosh_id=None)], NOW)
    assert verdict.kind is Kind.DUPLICATE_UNVERIFIED


def test_start_within_60s_matches():
    payload = garmin_duplicate_payload(start_date="2025-11-02T15:59:00Z")
    assert classify(StravaActivity(**payload), [uploaded_ride()], NOW).kind is Kind.DUPLICATE
