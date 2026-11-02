import logging
import threading

from .db import Database, Job
from .notify import Notifier
from .processor import Processor, RetryLater

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 6
RETRY_DELAY_SECONDS = 5 * 60


class Worker(threading.Thread):
    def __init__(self, db: Database, processor: Processor, notifier: Notifier, reconcile_interval_seconds: float):
        super().__init__(name="worker", daemon=True)
        self._db = db
        self._processor = processor
        self._notifier = notifier
        self._reconcile_interval = reconcile_interval_seconds
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            if not self.tick():
                self._stop.wait(5)

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> bool:
        """Run one due job; returns whether there was one."""
        if not self._db.has_pending("reconcile"):
            self._db.enqueue("reconcile", {}, delay_seconds=self._reconcile_interval)
        job = self._db.next_job()
        if job is None:
            return False
        try:
            if job.kind == "strava_activity":
                self._processor.handle_activity(job.payload)
            elif job.kind == "reconcile":
                self._processor.reconcile()
            self._db.complete(job)
        except Exception as e:
            self._retry_or_fail(job, e)
        return True

    def _retry_or_fail(self, job: Job, error: Exception) -> None:
        message = f"{type(error).__name__}: {error}"
        if not isinstance(error, RetryLater):
            log.exception("Job %s (%s) failed", job.id, job.kind)
        if job.attempts + 1 < MAX_ATTEMPTS:
            log.info("Retrying job %s (%s) in %ds: %s", job.id, job.kind, RETRY_DELAY_SECONDS, message)
            self._db.retry(job, RETRY_DELAY_SECONDS, message)
            return
        self._db.fail(job, message)
        # Reconcile reruns on its own schedule, so one alert per day is enough for it.
        key = "reconcile_failed" if job.kind == "reconcile" else None
        self._notifier.send(f"Gave up on {job.kind} job {job.id} after {MAX_ATTEMPTS} attempts: {message}", key=key)
