import pytest
from fastapi.testclient import TestClient

from bridge.app import build, create_app
from bridge.config import Settings

from .conftest import ATHLETE_ID, strava_payload

ENV = {
    "MYWHOOSH_EMAIL": "m@example.com",
    "MYWHOOSH_PASSWORD": "mp",
    "GARMIN_USERNAME": "g@example.com",
    "GARMIN_PASSWORD": "gp",
    "BRIDGE_SECRET": "s3cret",
    "STRAVA_ATHLETE_ID": str(ATHLETE_ID),
}


@pytest.fixture
def bridge(tmp_path):
    return build(Settings.from_env({**ENV, "DATA_DIR": str(tmp_path)}))


@pytest.fixture
def client(bridge):
    # Not entered as a context manager, so the lifespan (and worker thread) doesn't start.
    return TestClient(create_app(bridge))


def test_settings_require_core_variables():
    with pytest.raises(ValueError, match="BRIDGE_SECRET"):
        Settings.from_env({k: v for k, v in ENV.items() if k != "BRIDGE_SECRET"})


def test_settings_default_to_dry_run(tmp_path):
    assert Settings.from_env(ENV).dry_run
    assert not Settings.from_env({**ENV, "DRY_RUN": "False"}).dry_run


def test_event_is_queued(client, bridge):
    response = client.post("/events", json=strava_payload(), headers={"X-Bridge-Secret": "s3cret"})
    assert response.status_code == 202 and response.json() == {"queued": True}
    job = bridge.db.next_job()
    assert job.kind == "strava_activity" and job.payload["id"] == 1001


def test_wrong_secret_is_rejected(client, bridge):
    assert client.post("/events", json=strava_payload(), headers={"X-Bridge-Secret": "nope"}).status_code == 401
    assert client.post("/events", json=strava_payload()).status_code == 401
    assert bridge.db.next_job() is None


def test_other_athletes_are_ignored(client, bridge):
    response = client.post("/events", json=strava_payload(athlete_id=1), headers={"X-Bridge-Secret": "s3cret"})
    assert response.json() == {"queued": False}
    assert bridge.db.next_job() is None


def test_malformed_event_is_rejected(client):
    response = client.post("/events", json={"id": "x"}, headers={"X-Bridge-Secret": "s3cret"})
    assert response.status_code == 422


def test_healthz_reports_worker_state(bridge):
    assert TestClient(create_app(bridge)).get("/healthz").status_code == 503
    with TestClient(create_app(bridge)) as running:
        assert running.get("/healthz").json() == {"worker_alive": True}
    assert bridge.db.has_pending("reconcile")


def test_build_records_install_time(bridge):
    assert float(bridge.db.get("installed_at")) > 0
