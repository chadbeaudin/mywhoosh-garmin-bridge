from bridge.strava_web import StravaAuthError
from bridge.worker import MAX_ATTEMPTS, Worker

from .fakes import FakeNotifier


class StubProcessor:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.handled: list[dict] = []
        self.token_checks = 0
        self.connections_logged = 0

    def log_connections(self) -> None:
        self.connections_logged += 1

    def handle_activity(self, payload: dict) -> None:
        if self.error:
            raise self.error
        self.handled.append(payload)

    def check_remember_token(self) -> None:
        if self.error:
            raise self.error
        self.token_checks += 1


def run_all(worker: Worker, db) -> None:
    while worker.tick():
        pass


def test_runs_activity_job_and_keeps_token_check_scheduled(db):
    processor = StubProcessor()
    worker = Worker(db, processor, FakeNotifier(), token_check_interval_seconds=3600)
    db.enqueue("strava_activity", {"id": 1})
    run_all(worker, db)
    assert processor.handled == [{"id": 1}]
    assert db.has_pending("token_check")


def test_token_check_reschedules_after_running(db):
    processor = StubProcessor()
    worker = Worker(db, processor, FakeNotifier(), token_check_interval_seconds=3600)
    db.enqueue("token_check", {})
    run_all(worker, db)
    assert processor.token_checks == 1
    assert db.has_pending("token_check")


def test_failing_job_retries_then_gives_up_with_notification(db):
    notifier = FakeNotifier()
    worker = Worker(db, StubProcessor(RuntimeError("not yet")), notifier, token_check_interval_seconds=3600)
    db.enqueue("strava_activity", {"id": 1})
    for _ in range(MAX_ATTEMPTS):
        job = db.next_job()
        assert job is not None and job.kind == "strava_activity"
        worker.tick()
        # Make the retried job due immediately.
        db._execute("UPDATE jobs SET run_after = 0 WHERE status = 'pending' AND kind = 'strava_activity'")
    assert not db.has_pending("strava_activity")
    assert len(notifier.messages) == 1 and "not yet" in notifier.messages[0]


def make_due(db) -> None:
    db._execute("UPDATE jobs SET run_after = 0 WHERE status = 'pending'")


def test_ride_waits_for_a_working_strava_token_then_syncs(db):
    notifier = FakeNotifier()
    processor = StubProcessor(StravaAuthError("Strava remember token has expired"))
    worker = Worker(db, processor, notifier, token_check_interval_seconds=3600)
    db.enqueue("strava_activity", {"id": 1})
    for _ in range(MAX_ATTEMPTS + 1):
        worker.tick()
        make_due(db)
    assert db.next_job().attempts == 0
    assert db.count_jobs("failed") == 0
    assert "expired" in notifier.messages[0] and "Gave up" not in " ".join(notifier.messages)

    processor.error = None
    run_all(worker, db)
    assert processor.handled == [{"id": 1}]


def test_token_check_that_cannot_log_in_alerts_and_stays_scheduled(db):
    notifier = FakeNotifier()
    worker = Worker(db, StubProcessor(StravaAuthError("Strava rejected the remember token (not logged in)")), notifier, 3600)
    db.enqueue("token_check", {})
    worker.tick()
    assert "rejected" in notifier.messages[0]
    assert db.has_pending("token_check") and db.count_jobs("failed") == 0


def test_unexpected_error_is_retried(db):
    notifier = FakeNotifier()
    worker = Worker(db, StubProcessor(ValueError("bad")), notifier, token_check_interval_seconds=3600)
    db.enqueue("strava_activity", {"id": 1})
    worker.tick()
    assert db.has_pending("strava_activity")
    assert notifier.messages == []


def test_queued_job_of_a_retired_kind_is_finished_without_running(db):
    processor = StubProcessor()
    worker = Worker(db, processor, FakeNotifier(), token_check_interval_seconds=3600)
    db.enqueue("reconcile", {})
    run_all(worker, db)
    assert not db.has_pending("reconcile")
    assert processor.handled == [] and processor.token_checks == 0


def test_run_logs_connections_before_draining_jobs(db):
    processor = StubProcessor()
    worker = Worker(db, processor, FakeNotifier(), token_check_interval_seconds=3600)
    worker.stop()
    worker.run()
    assert processor.connections_logged == 1
