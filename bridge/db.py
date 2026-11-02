import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    run_after REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS jobs_pending ON jobs (status, run_after);
CREATE TABLE IF NOT EXISTS rides (
    mywhoosh_id TEXT PRIMARY KEY,
    start_time INTEGER NOT NULL,
    strava_mywhoosh_id INTEGER,
    strava_duplicate_id INTEGER,
    status TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int


@dataclass(frozen=True)
class Ride:
    mywhoosh_id: str
    start_time: int
    strava_mywhoosh_id: int | None
    strava_duplicate_id: int | None
    status: str
    created: float


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def _execute(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def enqueue(self, kind: str, payload: dict[str, Any], delay_seconds: float = 0) -> None:
        self._execute(
            "INSERT INTO jobs (kind, payload, run_after) VALUES (?, ?, ?)",
            (kind, json.dumps(payload), time.time() + delay_seconds),
        )

    def next_job(self) -> Job | None:
        rows = self._execute(
            "SELECT id, kind, payload, attempts FROM jobs WHERE status = 'pending' AND run_after <= ? "
            "ORDER BY run_after, id LIMIT 1",
            (time.time(),),
        )
        if not rows:
            return None
        row = rows[0]
        return Job(row["id"], row["kind"], json.loads(row["payload"]), row["attempts"])

    def has_pending(self, kind: str) -> bool:
        return bool(self._execute("SELECT 1 FROM jobs WHERE status = 'pending' AND kind = ? LIMIT 1", (kind,)))

    def complete(self, job: Job) -> None:
        self._execute("UPDATE jobs SET status = 'done' WHERE id = ?", (job.id,))

    def retry(self, job: Job, delay_seconds: float, error: str) -> None:
        self._execute(
            "UPDATE jobs SET attempts = attempts + 1, run_after = ?, last_error = ? WHERE id = ?",
            (time.time() + delay_seconds, error, job.id),
        )

    def fail(self, job: Job, error: str) -> None:
        self._execute(
            "UPDATE jobs SET status = 'failed', attempts = attempts + 1, last_error = ? WHERE id = ?",
            (error, job.id),
        )

    def save_ride(self, ride: Ride) -> None:
        self._execute(
            "INSERT OR REPLACE INTO rides (mywhoosh_id, start_time, strava_mywhoosh_id, strava_duplicate_id, status, created) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ride.mywhoosh_id, ride.start_time, ride.strava_mywhoosh_id, ride.strava_duplicate_id, ride.status, ride.created),
        )

    def ride(self, mywhoosh_id: str) -> Ride | None:
        rows = self._execute("SELECT * FROM rides WHERE mywhoosh_id = ?", (mywhoosh_id,))
        return Ride(**dict(rows[0])) if rows else None

    def rides_near(self, start_time: int, tolerance_seconds: int) -> list[Ride]:
        rows = self._execute(
            "SELECT * FROM rides WHERE start_time BETWEEN ? AND ? ORDER BY ABS(start_time - ?)",
            (start_time - tolerance_seconds, start_time + tolerance_seconds, start_time),
        )
        return [Ride(**dict(r)) for r in rows]

    def get(self, key: str) -> str | None:
        rows = self._execute("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def put(self, key: str, value: str) -> None:
        self._execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
