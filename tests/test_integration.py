"""End to end: StreetSweep events in, real clients out, with HTTP mocked at the boundary and a fake garminconnect."""
import json

import pytest
import responses
from fastapi.testclient import TestClient
from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.file_id_message import FileIdMessage

from bridge import garmin, mywhoosh, strava_web
from bridge.app import build, create_app
from bridge.config import Settings

from .conftest import ATHLETE_ID, garmin_duplicate_payload, make_fit, make_jwt, strava_payload
from .test_garmin import FakeGarminApi
from .test_strava_web import ABOUT_HTML

TELEGRAM = "https://api.telegram.org/botBOT/sendMessage"


def settings(tmp_path, dry_run: bool) -> Settings:
    return Settings.from_env({
        "MYWHOOSH_EMAIL": "m@example.com", "MYWHOOSH_PASSWORD": "mp",
        "GARMIN_USERNAME": "g@example.com", "GARMIN_PASSWORD": "gp",
        "BRIDGE_SECRET": "s3cret", "STRAVA_ATHLETE_ID": str(ATHLETE_ID),
        "STRAVA_REMEMBER_TOKEN": make_jwt(), "TELEGRAM_BOT_TOKEN": "BOT", "TELEGRAM_CHAT_ID": "42",
        "DATA_DIR": str(tmp_path), "DRY_RUN": str(dry_run).lower(),
    })


@pytest.fixture
def garmin_api(monkeypatch):
    FakeGarminApi.instances = []
    monkeypatch.setattr(garmin, "Garmin", FakeGarminApi)
    return FakeGarminApi


def mock_mywhoosh():
    responses.post(mywhoosh.LOGIN_URL, json={"Success": True, "AccessToken": "tok", "WhooshId": "wid"})
    responses.post(mywhoosh.ACTIVITIES_URL, json={"data": {"results": [{"id": 77, "activityFileId": "f77"}]}})
    responses.post(mywhoosh.DOWNLOAD_URL, json={"data": "https://s3.example/77.fit"})
    responses.get("https://s3.example/77.fit", body=make_fit())


def mock_strava_delete():
    base = strava_web.BASE_URL
    responses.get(f"{base}/me", status=302, headers={"Location": f"{base}/athletes/{ATHLETE_ID}"})
    responses.get(f"{base}/about", body=ABOUT_HTML)
    return responses.post(f"{base}/activities/1002", status=302, headers={"Location": f"{base}/athlete/training"})


def post_and_process(client: TestClient, bridge, payload: dict) -> None:
    assert client.post("/events", json=payload, headers={"X-Bridge-Secret": "s3cret"}).status_code == 202
    while bridge.worker.tick():
        if not bridge.db.has_pending("strava_activity"):
            break


@responses.activate
def test_ride_reaches_garmin_and_its_strava_duplicate_is_deleted(tmp_path, garmin_api):
    mock_mywhoosh()
    delete = mock_strava_delete()
    bridge = build(settings(tmp_path, dry_run=False))
    client = TestClient(create_app(bridge))

    post_and_process(client, bridge, strava_payload())
    uploaded = garmin_api.instances[0].uploaded[0]
    file_id = next(r.message for r in FitFile.from_bytes(uploaded).records if isinstance(r.message, FileIdMessage))
    assert (file_id.manufacturer, file_id.product) == (1, 4024)
    assert garmin_api.instances[0].tokenstore == str(tmp_path / "garmin_tokens")

    post_and_process(client, bridge, garmin_duplicate_payload())
    assert delete.call_count == 1
    assert bridge.db.ride("77").status == "duplicate_deleted"

    # A later edit event for the same ride changes nothing.
    post_and_process(client, bridge, garmin_duplicate_payload(aspect_type="update"))
    assert delete.call_count == 1


@responses.activate
def test_dry_run_reports_the_duplicate_over_telegram(tmp_path, garmin_api):
    mock_mywhoosh()
    delete = mock_strava_delete()
    telegram = responses.post(TELEGRAM, json={"ok": True})
    bridge = build(settings(tmp_path, dry_run=True))
    client = TestClient(create_app(bridge))

    post_and_process(client, bridge, strava_payload())
    post_and_process(client, bridge, garmin_duplicate_payload())
    assert delete.call_count == 0
    assert "would delete" in json.loads(telegram.calls[0].request.body)["text"]


@responses.activate
def test_outdoor_garmin_ride_is_never_touched(tmp_path, garmin_api):
    delete = mock_strava_delete()
    bridge = build(settings(tmp_path, dry_run=False))
    client = TestClient(create_app(bridge))

    post_and_process(client, bridge, garmin_duplicate_payload(sport_type="Ride"))
    assert delete.call_count == 0
    assert garmin_api.instances == []
