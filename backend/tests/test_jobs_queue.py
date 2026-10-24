"""Tier 2: durable queue — dedupe, lease expiry recovery after a worker crash, retry,
permanent failure, dead-lettering and cancellation (real PostgreSQL)."""

from __future__ import annotations

import sqlalchemy as sa

from backend import jobs, worker
from backend.db import jobs as jobs_t
from backend.db import transaction
from backend.worker import JobContext, JobDeferred, PermanentJobError, handler
from shared.errors import ErrorCode

CALLS: list[str] = []


@handler("test_counter")
def _counter(ctx: JobContext) -> dict:
    CALLS.append(ctx.job.payload["n"])
    return {"seen": ctx.job.payload["n"], "attempt": ctx.job.attempts}


@handler("test_flaky")
def _flaky(ctx: JobContext) -> dict:
    CALLS.append(f"flaky-{ctx.job.attempts}")
    if ctx.job.attempts < 2:
        raise RuntimeError("transient")
    return {"ok": True}


@handler("test_permanent")
def _permanent(ctx: JobContext) -> None:
    raise PermanentJobError(ErrorCode.SCOPE_DENIED, "policy says no")


@handler("test_wait")
def _wait(ctx: JobContext) -> dict:
    CALLS.append(f"wait-{ctx.job.attempts}")
    if ctx.job.payload.get("ready"):
        return {"ok": True}
    raise JobDeferred(delay_s=0, message="not yet")


def _job(job_id: str) -> dict:
    with transaction() as conn:
        return dict(conn.execute(sa.select(jobs_t).where(jobs_t.c.id == job_id)).mappings().one())


def _make_runnable(job_id: str) -> None:
    with transaction() as conn:
        conn.execute(sa.update(jobs_t).where(jobs_t.c.id == job_id).values(run_after=sa.func.now()))


def test_dedupe_key_returns_existing_job() -> None:
    with transaction() as conn:
        a, created_a = jobs.enqueue(conn, "test_counter", {"n": "1"}, dedupe_key="same")
        b, created_b = jobs.enqueue(conn, "test_counter", {"n": "2"}, dedupe_key="same")
    assert created_a and not created_b and a == b


def test_crashed_worker_lease_expires_and_job_runs_exactly_once_more() -> None:
    CALLS.clear()
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "test_counter", {"n": "crash"})
    # Worker A claims and "crashes" (never completes, never heartbeats).
    with transaction() as conn:
        claimed = jobs.claim(conn, "worker-A", lease_s=30)
    assert claimed and claimed.id == jid and claimed.attempts == 1
    assert worker.drain(kinds=["test_counter"]) == []  # lease still held: nothing runnable
    with transaction() as conn:  # simulate the lease timing out
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == jid)
            .values(lease_expires_at=sa.func.now() - sa.text("interval '1 second'"))
        )
    assert worker.drain(kinds=["test_counter"]) == [jid]
    row = _job(jid)
    assert row["status"] == "succeeded" and row["attempts"] == 2 and CALLS == ["crash"]
    assert row["result"] == {"seen": "crash", "attempt": 2}
    with transaction() as conn:  # stale worker A cannot complete a job it no longer owns
        assert jobs.complete(conn, jid, "worker-A") is False


def test_transient_failure_retries_with_backoff() -> None:
    CALLS.clear()
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "test_flaky", {})
    worker.drain(kinds=["test_flaky"])
    row = _job(jid)
    assert row["status"] == "queued" and row["last_error"]["code"] == "INTERNAL"
    assert row["run_after"] > row["updated_at"].__class__.now(
        row["run_after"].tzinfo
    )  # backoff scheduled
    _make_runnable(jid)
    worker.drain(kinds=["test_flaky"])
    assert _job(jid)["status"] == "succeeded" and CALLS == ["flaky-1", "flaky-2"]


def test_permanent_failure_is_not_retried() -> None:
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "test_permanent", {})
    worker.drain(kinds=["test_permanent"])
    row = _job(jid)
    assert (
        row["status"] == "failed"
        and row["last_error"]["code"] == "SCOPE_DENIED"
        and row["attempts"] == 1
    )


def test_exhausted_attempts_dead_letter() -> None:
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "test_flaky", {}, max_attempts=1)
    worker.drain(kinds=["test_flaky"])
    assert _job(jid)["status"] == "dead"


def test_cancel_queued_job() -> None:
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "test_counter", {"n": "x"})
        jobs.request_cancel(conn, jid)
    assert worker.drain(kinds=["test_counter"]) == []
    assert _job(jid)["status"] == "cancelled"


def test_claim_skips_cancel_requested_running_job_even_after_lease_expiry() -> None:
    CALLS.clear()
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "test_counter", {"n": "off-race"})
        claimed = jobs.claim(conn, "worker-off", lease_s=30)
        assert claimed is not None and claimed.id == jid
        jobs.request_cancel(conn, jid)
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == jid)
            .values(lease_expires_at=sa.func.now() - sa.text("interval '1 second'"))
        )
    with transaction() as conn:
        assert jobs.claim(conn, "worker-retry", lease_s=30, kinds=["test_counter"]) is None
        assert jobs.complete(conn, jid, "worker-off", {"ok": True}) is False
    assert worker.drain(kinds=["test_counter"]) == []
    assert CALLS == []
    row = _job(jid)
    assert row["cancel_requested"] is True
    # Reconciliation confirms the cancellation instead of leaving the row "running" forever.
    assert row["status"] == "cancelled"
    assert row["error_category"] == "cancelled"


def test_defer_does_not_consume_failure_attempts() -> None:
    CALLS.clear()
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "test_wait", {"ready": False}, max_attempts=2)
    assert worker.drain(max_jobs=1, kinds=["test_wait"]) == [jid]
    row = _job(jid)
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert CALLS == ["wait-1"]
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == jid)
            .values(payload={"ready": True}, run_after=sa.func.now())
        )
    worker.drain(max_jobs=1, kinds=["test_wait"])
    row = _job(jid)
    assert row["status"] == "succeeded"
    assert row["attempts"] == 1
    assert CALLS == ["wait-1", "wait-1"]


def test_unknown_kind_fails_permanently() -> None:
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "no_such_kind", {})
    worker.drain(kinds=["no_such_kind"])
    assert _job(jid)["status"] == "failed"
