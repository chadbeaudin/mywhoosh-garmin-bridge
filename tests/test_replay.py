import pytest
import responses

from bridge.classify import Kind, StravaActivity, classify
from bridge.replay import parse_activity_id, replay
from bridge.strava_web import BASE_URL, RememberToken, StravaWeb, StravaWebError

from .conftest import ATHLETE_ID, RIDE_START, make_fit, make_jwt


def strava_web(tmp_path) -> StravaWeb:
    return StravaWeb(RememberToken(make_jwt(), tmp_path / "token"), ATHLETE_ID)


def mock_download(activity_id: int, body: bytes, status: int = 200):
    responses.get(f"{BASE_URL}/me", status=302, headers={"Location": f"{BASE_URL}/athletes/{ATHLETE_ID}"})
    responses.get(f"{BASE_URL}/activities/{activity_id}/export_original", body=body, status=status)


@pytest.mark.parametrize("arg", ["21002864534", "https://www.strava.com/activities/21002864534", " 21002864534\n"])
def test_activity_id_is_parsed_from_id_or_url(arg):
    assert parse_activity_id(arg) == 21002864534


def test_activity_id_rejects_garbage():
    with pytest.raises(ValueError, match="Not a Strava activity"):
        parse_activity_id("my-ride")


@responses.activate
def test_replay_queues_a_ride_the_bridge_classifies_as_mywhoosh(tmp_path, db):
    mock_download(42, make_fit())
    replay(42, ATHLETE_ID, strava_web(tmp_path), db)
    job = db.next_job()
    assert job.kind == "strava_activity"
    assert job.payload["start_date"] == "2025-11-02T16:00:00Z"
    activity = StravaActivity(**job.payload)
    assert activity.start_time == RIDE_START
    assert classify(activity, [], now=RIDE_START).kind is Kind.MYWHOOSH


@responses.activate
def test_replay_queues_nothing_when_the_download_fails(tmp_path, db):
    mock_download(42, b"", status=404)
    with pytest.raises(StravaWebError, match="HTTP 404"):
        replay(42, ATHLETE_ID, strava_web(tmp_path), db)
    assert db.next_job() is None
