"""Durable PostgreSQL job queue (ADR-0005): SKIP LOCKED claims, leases, heartbeats,
retry with backoff, dedupe keys, cancellation and dead-lettering.

Semantics: at-least-once. A job whose worker dies keeps ``status='running'`` until
its lease expires; the next claim re-takes it (attempt + 1). Handlers must be
idempotent (they check durable state before side effects).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend.db import jobs, worker_heartbeats
from backend.redaction import redact
from shared.base import utcnow
from shared.errors import ErrorCode
from shared.ids import IdPrefix, new_id


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    lease_owner: str
    lease_expires_at: datetime


def enqueue(
    conn: Connection,
    kind: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    priority: int = 100,
    delay_s: float = 0,
    max_attempts: int = 5,
) -> tuple[str, bool]:
    """Insert a job; returns (job_id, created). An existing dedupe_key returns that job."""
    job_id = new_id(IdPrefix.job)
    stmt = (
        sa.dialects.postgresql.insert(jobs)
        .values(
            id=job_id,
            kind=kind,
            payload=payload,
            status="queued",
            priority=priority,
            run_after=utcnow() + timedelta(seconds=delay_s),
            max_attempts=max_attempts,
            dedupe_key=dedupe_key,
        )
        .on_conflict_do_nothing(index_elements=[jobs.c.dedupe_key])
        .returning(jobs.c.id)
    )
    created = conn.execute(stmt).scalar()
    if created:
        return created, True
    existing = conn.execute(
        sa.select(jobs.c.id).where(jobs.c.dedupe_key == dedupe_key)
    ).scalar_one()
    return existing, False


_CLAIM_SQL = sa.text(
    """
    UPDATE jobs SET
      status = 'running',
      attempts = jobs.attempts + 1,
      lease_owner = :owner,
      lease_expires_at = now() + make_interval(secs => :lease_s),
      heartbeat_at = now(),
      updated_at = now()
    WHERE id = (
      SELECT id FROM jobs
      WHERE ((status = 'queued' AND run_after <= now())
          OR (status = 'running' AND lease_expires_at < now()))
        AND cancel_requested IS NOT TRUE
        AND (:kinds_empty OR kind = ANY(:kinds))
      ORDER BY priority, run_after
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    RETURNING id, kind, payload, attempts, max_attempts, lease_owner, lease_expires_at
    """
)


def claim(conn: Connection, owner: str, lease_s: int, kinds: list[str] | None = None) -> Job | None:
    row = (
        conn.execute(
            _CLAIM_SQL,
            {"owner": owner, "lease_s": lease_s, "kinds": kinds or [], "kinds_empty": not kinds},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    job = Job(**dict(row))
    if job.attempts > job.max_attempts:
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == job.id)
            .values(
                status="dead",
                lease_owner=None,
                updated_at=sa.func.now(),
                last_error=redact(
                    {
                        "code": ErrorCode.JOB_LEASE_EXPIRED.value,
                        "message": "attempts exhausted (lease expired or repeated failure)",
                    }
                ),
            )
        )
        return None
    return job


def heartbeat(conn: Connection, job_id: str, owner: str, lease_s: int) -> bool:
    """Extend the lease. Returns False if the lease was lost or cancellation requested."""
    row = conn.execute(
        sa.update(jobs)
        .where(jobs.c.id == job_id, jobs.c.lease_owner == owner, jobs.c.status == "running")
        .values(
            lease_expires_at=sa.func.now() + sa.text(f"interval '{int(lease_s)} seconds'"),
            heartbeat_at=sa.func.now(),
        )
        .returning(jobs.c.cancel_requested)
    ).first()
    return row is not None and not row[0]


def complete(
    conn: Connection, job_id: str, owner: str, result: dict[str, Any] | None = None
) -> bool:
    """Succeed only while this worker still holds a running, not-cancelled lease."""
    res = conn.execute(
        sa.update(jobs)
        .where(
            jobs.c.id == job_id,
            jobs.c.lease_owner == owner,
            jobs.c.status == "running",
            jobs.c.cancel_requested.is_not(True),
        )
        .values(status="succeeded", result=result or {}, lease_owner=None, updated_at=sa.func.now())
    )
    return res.rowcount == 1


def is_cancel_requested(conn: Connection, job_id: str) -> bool:
    row = conn.execute(sa.select(jobs.c.cancel_requested).where(jobs.c.id == job_id)).first()
    return bool(row and row[0])


def defer(conn: Connection, job_id: str, owner: str, *, delay_s: float) -> bool:
    """Requeue a running job without consuming a failure attempt.

    Claim already incremented ``attempts``. Prerequisite waits must not burn the
    ordinary retry budget, so this undoes that increment and schedules a retry.
    """
    row = conn.execute(
        sa.select(jobs.c.attempts)
        .where(jobs.c.id == job_id, jobs.c.lease_owner == owner, jobs.c.status == "running")
        .with_for_update()
    ).first()
    if row is None:
        return False
    conn.execute(
        sa.update(jobs)
        .where(jobs.c.id == job_id)
        .values(
            status="queued",
            attempts=max(int(row.attempts) - 1, 0),
            run_after=utcnow() + timedelta(seconds=max(0.0, delay_s)),
            lease_owner=None,
            lease_expires_at=None,
            last_error={"code": "PREREQUISITE_NOT_READY", "message": "waiting for prerequisite"},
            updated_at=sa.func.now(),
        )
    )
    return True


def fail(
    conn: Connection, job_id: str, owner: str, error: dict[str, Any], *, retryable: bool
) -> str:
    """Record failure; requeue with exponential backoff while attempts remain."""
    row = conn.execute(
        sa.select(jobs.c.attempts, jobs.c.max_attempts, jobs.c.cancel_requested)
        .where(jobs.c.id == job_id, jobs.c.lease_owner == owner)
        .with_for_update()
    ).first()
    if row is None:
        return "lost"
    attempts, max_attempts, cancelled = row
    if cancelled:
        status, run_after = "cancelled", utcnow()
    elif retryable and attempts < max_attempts:
        status, run_after = "queued", utcnow() + timedelta(seconds=min(300, 2**attempts))
    else:
        status, run_after = ("failed" if not retryable else "dead"), utcnow()
    conn.execute(
        sa.update(jobs)
        .where(jobs.c.id == job_id)
        .values(
            status=status,
            run_after=run_after,
            last_error=redact(error),
            lease_owner=None,
            lease_expires_at=None,
            updated_at=sa.func.now(),
        )
    )
    return status


def request_cancel(conn: Connection, job_id: str) -> None:
    conn.execute(
        sa.update(jobs)
        .where(jobs.c.id == job_id)
        .values(cancel_requested=True, updated_at=sa.func.now())
    )
    conn.execute(
        sa.update(jobs)
        .where(jobs.c.id == job_id, jobs.c.status == "queued")
        .values(status="cancelled")
    )


def queue_reachable(conn: Connection) -> bool:
    conn.execute(sa.select(sa.func.count()).select_from(jobs).where(jobs.c.status == "queued"))
    return True


# --------------------------------------------------------------- worker liveness


def worker_heartbeat(
    conn: Connection, worker_id: str, job_id: str | None = None, *, poll: bool = False
) -> None:
    """Upsert this worker's liveness row.

    ``heartbeat_at`` is written by the claim loop (each poll) and by the lease-heartbeat
    thread while a job runs — it proves the process is alive. ``last_poll_at`` is written
    ONLY by the claim loop itself (``poll=True``), so a worker whose main loop stopped
    still beats while it holds a job but cannot fake idle-alive: liveness requires
    ``heartbeat_at`` fresh AND (``last_poll_at`` fresh OR ``current_job_id`` set)."""
    values: dict[str, Any] = {
        "worker_id": worker_id,
        "heartbeat_at": sa.func.now(),
        "current_job_id": job_id,
    }
    set_: dict[str, Any] = {"heartbeat_at": sa.func.now(), "current_job_id": job_id}
    if poll:
        values["last_poll_at"] = sa.func.now()
        set_["last_poll_at"] = sa.func.now()
    conn.execute(
        sa.dialects.postgresql.insert(worker_heartbeats)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[worker_heartbeats.c.worker_id],
            set_=set_,
        )
    )


def worker_stopped(conn: Connection, worker_id: str) -> None:
    conn.execute(sa.delete(worker_heartbeats).where(worker_heartbeats.c.worker_id == worker_id))


def live_workers(conn: Connection, liveness_s: float) -> list[dict[str, Any]]:
    """Workers counted alive for readiness, oldest heartbeat first.

    A row counts when the process beat recently AND is verifiably processing: either the
    claim loop polled inside the liveness window (``last_poll_at`` fresh — only the main
    loop writes it) or the worker holds a claimed job (``current_job_id``). A process
    that is alive but whose claim loop stopped fails both tests; a worker mid-job keeps
    qualifying via ``current_job_id`` even though it is not polling."""
    cutoff = utcnow() - timedelta(seconds=liveness_s)
    rows = (
        conn.execute(
            sa.select(worker_heartbeats)
            .where(worker_heartbeats.c.heartbeat_at > cutoff)
            .where(
                sa.or_(
                    worker_heartbeats.c.last_poll_at > cutoff,
                    worker_heartbeats.c.current_job_id.is_not(None),
                )
            )
            .order_by(worker_heartbeats.c.heartbeat_at)
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def prune_worker_heartbeats(conn: Connection, older_than_s: float = 86400.0) -> None:
    conn.execute(
        sa.delete(worker_heartbeats).where(
            worker_heartbeats.c.heartbeat_at < utcnow() - timedelta(seconds=older_than_s)
        )
    )


def queue_counts(conn: Connection) -> dict[str, int]:
    """Job counts by coarse status for diagnostics (exact, the queue is small)."""
    rows = conn.execute(sa.select(jobs.c.status, sa.func.count()).group_by(jobs.c.status)).all()
    counts = {s: 0 for s in ("queued", "running", "succeeded", "failed", "cancelled", "dead")}
    counts.update({s: n for s, n in rows})
    return counts
