import logging
import time

import pytest
from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.file_id_message import FileIdMessage

from bridge.db import Ride
from bridge.processor import Processor, describe
from bridge.strava_web import StravaWebError

from .conftest import RIDE_START, garmin_duplicate_payload, make_fit, strava_payload
from .fakes import FakeGarmin, FakeNotifier, FakeRememberToken, FakeStravaWeb


class Harness:
    def __init__(self, db, backup_dir, garmin_starts=None, dry_run=False, strava_error=None, download_error=None, token_expires=None):
        self.db = db
        self.garmin = FakeGarmin(garmin_starts)
        self.strava = FakeStravaWeb(make_fit(), strava_error, download_error)
        self.notifier = FakeNotifier()
        self.processor = Processor(
            db, self.garmin, self.strava,
            FakeRememberToken(token_expires or time.time() + 30 * 86400), self.notifier, dry_run, backup_dir,
        )

    def upload_ride(self) -> bytes:
        """Sync the MyWhoosh ride; Strava then serves the file Garmin got as the duplicate's original."""
        self.processor.handle_activity(strava_payload())
        self.strava.original = self.garmin.uploads[0][0]
        return self.strava.original


def test_mywhoosh_ride_is_uploaded_as_garmin_edge(db, tmp_path):
    h = Harness(db, tmp_path)
    h.processor.handle_activity(strava_payload())
    assert h.strava.downloads == [1001]
    data, name = h.garmin.uploads[0]
    assert name == "mywhoosh_1001"
    file_id = next(r.message for r in FitFile.from_bytes(data).records if isinstance(r.message, FileIdMessage))
    assert file_id.manufacturer == 1
    ride = db.ride("1001")
    assert (ride.status, ride.strava_mywhoosh_id, ride.start_time) == ("uploaded", 1001, RIDE_START)
    assert h.notifier.messages == [f"Uploaded {describe(RIDE_START)} to Garmin Connect."]


def test_failed_strava_download_is_left_for_the_worker_to_retry(db, tmp_path):
    h = Harness(db, tmp_path, download_error=StravaWebError("not a FIT file"))
    with pytest.raises(StravaWebError):
        h.processor.handle_activity(strava_payload())
    assert h.garmin.uploads == []
    assert db.ride("1001") is None


def test_ride_already_on_garmin_is_not_uploaded_or_deduplicated(db, tmp_path):
    h = Harness(db, tmp_path, garmin_starts=[RIDE_START + 10])
    h.processor.handle_activity(strava_payload())
    assert h.garmin.uploads == []
    assert db.ride("1001").status == "already_on_garmin"
    assert h.notifier.messages[-1].startswith("Garmin already had")
    h.processor.handle_activity(garmin_duplicate_payload())
    assert h.strava.deleted == []


def test_known_ride_records_strava_id_without_downloading(db, tmp_path):
    db.save_ride(Ride("w1", RIDE_START, None, None, "uploaded", time.time()))
    h = Harness(db, tmp_path)
    h.processor.handle_activity(strava_payload())
    assert h.strava.downloads == []
    assert db.ride("w1").strava_mywhoosh_id == 1001


def test_upload_crash_is_recovered_as_ours(db, tmp_path):
    h = Harness(db, tmp_path)
    h.garmin.fail_upload = True
    with pytest.raises(RuntimeError):
        h.processor.handle_activity(strava_payload())
    assert db.ride("1001").status == "uploading"
    # The upload actually reached Garmin before the crash.
    h.garmin.fail_upload = False
    h.garmin.existing_starts = [RIDE_START]
    h.processor.handle_activity(strava_payload())
    assert db.ride("1001").status == "uploaded"
    assert h.notifier.messages[-1].startswith("Uploaded")


def test_garmin_duplicate_is_backed_up_then_deleted(db, tmp_path):
    h = Harness(db, tmp_path)
    original = h.upload_ride()
    h.processor.handle_activity(garmin_duplicate_payload())
    assert h.strava.downloads[-1] == 1002
    assert h.strava.deleted == [1002]
    backup = tmp_path / "1002.fit"
    assert backup.read_bytes() == original
    assert backup.stat().st_mode & 0o777 == 0o600
    ride = db.ride("1001")
    assert (ride.status, ride.strava_duplicate_id) == ("duplicate_deleted", 1002)
    assert h.notifier.messages[-1] == (
        f"Deleted the Garmin duplicate https://www.strava.com/activities/1002 from Strava; its original is saved as {backup}."
    )


def test_duplicate_whose_file_is_not_the_bridge_upload_is_not_deleted(db, tmp_path):
    h = Harness(db, tmp_path)
    h.processor.handle_activity(strava_payload())
    # Strava serves the plain MyWhoosh file, not the Garmin Edge copy the bridge uploaded.
    h.processor.handle_activity(garmin_duplicate_payload())
    assert h.strava.deleted == []
    assert not (tmp_path / "1002.fit").exists()
    assert db.ride("1001").status == "duplicate_needs_review"
    assert "/activities/1002" in h.notifier.messages[-1] and "not deleted" in h.notifier.messages[-1]


def test_duplicate_is_not_deleted_when_its_original_cannot_be_downloaded(db, tmp_path):
    h = Harness(db, tmp_path)
    h.upload_ride()
    h.strava.download_error = StravaWebError("Strava download of 1002 failed (HTTP 500)")
    with pytest.raises(StravaWebError):
        h.processor.handle_activity(garmin_duplicate_payload())
    assert h.strava.deleted == []
    assert db.ride("1001").status == "uploaded"


def test_duplicate_is_not_deleted_when_the_backup_cannot_be_saved(db, tmp_path):
    blocked = tmp_path / "backups"
    blocked.write_text("a file where the backup folder should be")
    h = Harness(db, blocked)
    h.upload_ride()
    with pytest.raises(OSError):
        h.processor.handle_activity(garmin_duplicate_payload())
    assert h.strava.deleted == []
    assert db.ride("1001").status == "uploaded"


def test_dry_run_verifies_but_only_reports(db, tmp_path):
    h = Harness(db, tmp_path, dry_run=True)
    h.upload_ride()
    h.processor.handle_activity(garmin_duplicate_payload())
    assert h.strava.downloads[-1] == 1002
    assert h.strava.deleted == []
    assert not (tmp_path / "1002.fit").exists()
    assert db.ride("1001").status == "duplicate_dry_run"
    assert "DRY_RUN" in h.notifier.messages[-1] and "/activities/1002" in h.notifier.messages[-1]


def test_failed_delete_notifies_with_link(db, tmp_path):
    h = Harness(db, tmp_path, strava_error=StravaWebError("Strava rejected the remember token (not logged in)"))
    h.upload_ride()
    h.processor.handle_activity(garmin_duplicate_payload())
    assert db.ride("1001").status == "duplicate_delete_failed"
    assert "https://www.strava.com/activities/1002" in h.notifier.messages[-1]


def test_unrelated_garmin_ride_is_left_alone(db, tmp_path):
    h = Harness(db, tmp_path)
    h.processor.handle_activity(garmin_duplicate_payload(start_date="2025-11-03T07:00:00Z"))
    assert h.strava.deleted == [] and h.notifier.messages == []


def test_duplicate_of_ride_without_a_strava_id_is_flagged_not_deleted(db, tmp_path):
    db.save_ride(Ride("w1", RIDE_START, None, None, "uploaded", time.time()))
    h = Harness(db, tmp_path)
    h.processor.handle_activity(garmin_duplicate_payload())
    assert h.strava.deleted == []
    assert db.ride("w1").status == "duplicate_needs_review"
    assert "/activities/1002" in h.notifier.messages[0]


def test_check_warns_when_strava_token_expires_soon(db, tmp_path):
    h = Harness(db, tmp_path, token_expires=time.time() + 2 * 86400)
    h.processor.check_remember_token()
    assert "expires" in h.notifier.messages[0]


@pytest.mark.parametrize("token", [FakeRememberToken(None), FakeRememberToken(error=True)])
def test_check_warns_when_strava_token_unusable(db, tmp_path, token):
    h = Harness(db, tmp_path)
    h.processor._remember_token = token
    h.processor.check_remember_token()
    assert "STRAVA_REMEMBER_TOKEN" in h.notifier.messages[0]


def test_describe_gives_the_ride_time():
    assert describe(RIDE_START).startswith("ride from ")


def test_log_connections_reports_each_service(db, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="bridge.processor")
    h = Harness(db, tmp_path, token_expires=1893456000)
    h.processor.log_connections()
    expires = time.strftime("%Y-%m-%d", time.localtime(1893456000))
    assert [r.getMessage() for r in caplog.records] == [
        f"Strava: connected, token expires {expires}",
        "Garmin: connected",
        "Telegram: not configured",
    ]


def test_a_failed_connection_is_logged_and_the_rest_still_run(db, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="bridge.processor")
    h = Harness(db, tmp_path, strava_error=StravaWebError("Strava rejected the remember token (not logged in)"))
    h.garmin.login_error = RuntimeError("Garmin down")
    h.processor.log_connections()
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("ERROR", "Strava: connection failed: Strava rejected the remember token (not logged in)"),
        ("ERROR", "Garmin: connection failed: Garmin down"),
        ("INFO", "Telegram: not configured"),
    ]
