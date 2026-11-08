"""End to end: StreetSweep events in, real clients out, with HTTP mocked at the boundary and a fake garminconnect."""
import json
import logging

import pytest
import responses
from fastapi.testclient import TestClient
from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.file_id_message import FileIdMessage

from bridge import garmin, strava_web
from bridge.app import build, create_app
from bridge.config import Settings

from .conftest import ATHLETE_ID, garmin_duplicate_payload, make_fit, make_jwt, strava_payload
from .test_garmin import FakeGarminApi
from .test_strava_web import ABOUT_HTML

TELEGRAM = "https://api.telegram.org/botBOT/sendMessage"


def settings(tmp_path, dry_run: bool) -> Settings:
    return Settings.from_env({
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


def mock_strava_download(body: bytes, activity_id: int = 1001):
    responses.get(f"{strava_web.BASE_URL}/activities/{activity_id}/export_original", body=body)


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
    mock_strava_download(make_fit())
    delete = mock_strava_delete()
    bridge = build(settings(tmp_path, dry_run=False))
    client = TestClient(create_app(bridge))

    post_and_process(client, bridge, strava_payload())
    uploaded = garmin_api.instances[0].uploaded[0]
    file_id = next(r.message for r in FitFile.from_bytes(uploaded).records if isinstance(r.message, FileIdMessage))
    assert (file_id.manufacturer, file_id.product) == (1, 4024)
    assert garmin_api.instances[0].tokenstore == str(tmp_path / "garmin_tokens")

    # Garmin pushes the file it was sent back to Strava as the duplicate.
    mock_strava_download(uploaded, activity_id=1002)
    post_and_process(client, bridge, garmin_duplicate_payload())
    assert delete.call_count == 1
    assert bridge.db.ride("1001").status == "duplicate_deleted"
    assert (tmp_path / "strava_backups" / "1002.fit").read_bytes() == uploaded

    # A later edit event for the same ride changes nothing.
    post_and_process(client, bridge, garmin_duplicate_payload(aspect_type="update"))
    assert delete.call_count == 1


@responses.activate
def test_dry_run_reports_the_duplicate_over_telegram(tmp_path, garmin_api):
    mock_strava_download(make_fit())
    delete = mock_strava_delete()
    telegram = responses.post(TELEGRAM, json={"ok": True})
    bridge = build(settings(tmp_path, dry_run=True))
    client = TestClient(create_app(bridge))

    post_and_process(client, bridge, strava_payload())
    mock_strava_download(garmin_api.instances[0].uploaded[0], activity_id=1002)
    post_and_process(client, bridge, garmin_duplicate_payload())
    assert delete.call_count == 0
    texts = [json.loads(c.request.body)["text"] for c in telegram.calls]
    assert [t.split(":")[1].split()[0] for t in texts] == ["Downloaded", "Uploaded", "Downloaded", "DRY_RUN"]
    assert "would delete" in texts[-1] and '"Virtual Cycling"' in texts[-1]


@responses.activate
def test_forged_duplicate_event_for_a_real_activity_deletes_nothing(tmp_path, garmin_api):
    mock_strava_download(make_fit())
    delete = mock_strava_delete()
    bridge = build(settings(tmp_path, dry_run=False))
    client = TestClient(create_app(bridge))

    post_and_process(client, bridge, strava_payload())
    # The event claims 1002 is the Garmin copy, but Strava's original for it is a different file.
    mock_strava_download(make_fit(), activity_id=1002)
    post_and_process(client, bridge, garmin_duplicate_payload())
    assert delete.call_count == 0
    assert bridge.db.ride("1001").status == "duplicate_needs_review"
    assert not (tmp_path / "strava_backups").exists()


@responses.activate
def test_outdoor_garmin_ride_is_never_touched(tmp_path, garmin_api):
    delete = mock_strava_delete()
    bridge = build(settings(tmp_path, dry_run=False))
    client = TestClient(create_app(bridge))

    post_and_process(client, bridge, garmin_duplicate_payload(sport_type="Ride"))
    assert delete.call_count == 0
    assert garmin_api.instances == []


@responses.activate
def test_ride_strava_cannot_supply_as_fit_is_retried_without_uploading(tmp_path, garmin_api):
    mock_strava_download(b"not a fit file")
    mock_strava_delete()
    bridge = build(settings(tmp_path, dry_run=False))

    post_and_process(TestClient(create_app(bridge)), bridge, strava_payload())
    assert bridge.db.has_pending("strava_activity")
    assert bridge.db.ride("1001") is None
    assert all(api.uploaded == [] for api in garmin_api.instances)


@responses.activate
def test_worker_logs_live_connections_on_start(tmp_path, garmin_api, caplog):
    caplog.set_level(logging.INFO, logger="bridge.processor")
    mock_strava_delete()
    responses.get("https://api.telegram.org/botBOT/getMe", json={"ok": True})
    bridge = build(settings(tmp_path, dry_run=True))

    bridge.worker.stop()
    bridge.worker.run()

    messages = [r.getMessage() for r in caplog.records]
    assert messages[0].startswith("Strava: connected, token expires ")
    assert messages[1:] == ["Garmin: connected", "Telegram: connected"]
    assert garmin_api.instances[0].tokenstore == str(tmp_path / "garmin_tokens")
