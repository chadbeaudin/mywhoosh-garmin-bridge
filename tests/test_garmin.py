from pathlib import Path

import pytest

from bridge import garmin
from bridge.garmin import GarminClient

from .conftest import RIDE_START


class FakeGarminApi:
    instances: list["FakeGarminApi"] = []

    def __init__(self, username, password, prompt_mfa):
        self.prompt_mfa = prompt_mfa
        self.tokenstore = None
        self.activities: list[dict] = []
        self.uploaded: list[bytes] = []
        self.upload_error: Exception | None = None
        FakeGarminApi.instances.append(self)

    def login(self, tokenstore=None):
        self.tokenstore = tokenstore

    def get_activities_by_date(self, start, end):
        self.queried = (start, end)
        return self.activities

    def upload_activity(self, path):
        if self.upload_error:
            raise self.upload_error
        assert path.endswith(".fit")
        self.uploaded.append(Path(path).read_bytes())


@pytest.fixture
def api(monkeypatch):
    FakeGarminApi.instances = []
    monkeypatch.setattr(garmin, "Garmin", FakeGarminApi)
    return FakeGarminApi


def test_login_uses_token_dir(api, tmp_path):
    GarminClient("u", "p", tmp_path / "tokens").login()
    assert api.instances[0].tokenstore == str(tmp_path / "tokens")
    assert (tmp_path / "tokens").is_dir()


def test_default_mfa_prompt_refuses_instead_of_blocking(api, tmp_path):
    GarminClient("u", "p", tmp_path).login()
    with pytest.raises(garmin.GarminNeedsLogin):
        api.instances[0].prompt_mfa()


def test_has_activity_near_compares_gmt_start(api, tmp_path):
    client = GarminClient("u", "p", tmp_path)
    client.login()
    api.instances[0].activities = [{"startTimeGMT": "2025-11-02 16:00:30"}]
    assert client.has_activity_near(RIDE_START)
    assert api.instances[0].queried == ("2025-11-01", "2025-11-03")
    api.instances[0].activities = [{"startTimeGMT": "2025-11-02 16:02:00"}, {}]
    assert not client.has_activity_near(RIDE_START)


def test_upload_writes_fit_file(api, tmp_path):
    client = GarminClient("u", "p", tmp_path)
    client.upload(b"data", "ride")
    assert api.instances[0].uploaded == [b"data"]


def test_upload_conflict_means_already_there(api, tmp_path):
    client = GarminClient("u", "p", tmp_path)
    client.login()
    api.instances[0].upload_error = RuntimeError("409 Conflict")
    client.upload(b"data", "ride")


def test_upload_other_errors_raise(api, tmp_path):
    client = GarminClient("u", "p", tmp_path)
    client.login()
    api.instances[0].upload_error = RuntimeError("500 Server Error")
    with pytest.raises(RuntimeError):
        client.upload(b"data", "ride")
