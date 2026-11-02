import time

from bridge.db import Database, Ride


def test_jobs_run_in_order_once_due(db):
    db.enqueue("a", {"n": 1})
    db.enqueue("b", {"n": 2})
    db.enqueue("later", {}, delay_seconds=3600)
    first = db.next_job()
    assert (first.kind, first.payload) == ("a", {"n": 1})
    db.complete(first)
    assert db.next_job().kind == "b"
    db.complete(db.next_job())
    assert db.next_job() is None
    assert db.has_pending("later")


def test_retry_delays_job_and_counts_attempt(db):
    db.enqueue("a", {})
    job = db.next_job()
    db.retry(job, 3600, "boom")
    assert db.next_job() is None
    assert db.has_pending("a")


def test_failed_job_is_not_pending(db):
    db.enqueue("a", {})
    db.fail(db.next_job(), "boom")
    assert not db.has_pending("a")


def test_jobs_and_rides_survive_reopen(tmp_path):
    first = Database(tmp_path / "state.db")
    first.enqueue("a", {"x": 1})
    first.save_ride(Ride("w1", 100, None, None, "uploaded", time.time()))
    reopened = Database(tmp_path / "state.db")
    assert reopened.next_job().payload == {"x": 1}
    assert reopened.ride("w1").status == "uploaded"


def test_rides_near_orders_by_closeness(db):
    for ride_id, start in (("far", 150), ("near", 105), ("outside", 200)):
        db.save_ride(Ride(ride_id, start, None, None, "uploaded", 0))
    assert [r.mywhoosh_id for r in db.rides_near(100, 60)] == ["near", "far"]


def test_kv_roundtrip(db):
    assert db.get("k") is None
    db.put("k", "v")
    db.put("k", "v2")
    assert db.get("k") == "v2"
