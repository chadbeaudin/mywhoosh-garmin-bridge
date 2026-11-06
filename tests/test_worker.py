from bridge.worker import MAX_ATTEMPTS, Worker

from .fakes import FakeNotifier


class StubProcessor:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.handled: list[dict] = []
        self.token_checks = 0

    def handle_activity(self, payload: dict) -> None:
        if self.error:
            raise self.error
        self.handled.append(payload)

    def check_remember_token(self) -> None:
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
