"""Durable PostgreSQL job queue (ADR-0005): SKIP LOCKED claims, leases, heartbeats,
retry with backoff, dedupe keys, cancellation and dead-lettering.

Semantics: at-least-once. A job whose worker dies keeps ``status='running'`` until
its lease expires; the next claim (or ``backend.execution.reconcile``) re-takes it
(attempt + 1). Handlers must be idempotent (they check durable state before side
effects).

Lifecycle (docs/architecture/execution-reliability.md): every status change goes
through an explicit transition table and is appended to ``job_events``. A job that
declared an external side effect (``begin_effect``) is never re-run automatically
once that effect may have happened; it becomes ``recovery_required`` instead.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend.db import assessments, job_events, jobs
from backend.settings import get_settings
from shared.base import utcnow
from shared.errors import ErrorCode
from shared.ids import IdPrefix, new_id
from shared.live_defense import CONTROL_JOB_KINDS


class JobStatus(StrEnum):
    queued = "queued"
    waiting = "waiting"  # waiting on a prerequisite job (``depends_on``)
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    dead = "dead"  # retry budget exhausted
    timed_out = "timed_out"  # execution deadline passed
    recovery_required = "recovery_required"  # uncertain external effect; operator decides


TERMINAL = frozenset(
    {
        JobStatus.succeeded,
        JobStatus.failed,
        JobStatus.cancelled,
        JobStatus.dead,
        JobStatus.timed_out,
    }
)
PENDING = frozenset({JobStatus.queued, JobStatus.waiting})

TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.queued: frozenset(
        {JobStatus.running, JobStatus.cancelled, JobStatus.timed_out, JobStatus.dead}
    ),
    JobStatus.waiting: frozenset(
        {JobStatus.queued, JobStatus.cancelled, JobStatus.failed, JobStatus.timed_out}
    ),
    # running -> running is a lease re-claim after the previous owner lost it.
    JobStatus.running: frozenset(
        {
            JobStatus.running,
            JobStatus.queued,
            JobStatus.waiting,
            JobStatus.succeeded,
            JobStatus.failed,
            JobStatus.cancelled,
            JobStatus.dead,
            JobStatus.timed_out,
            JobStatus.recovery_required,
        }
    ),
    JobStatus.recovery_required: frozenset(
        {JobStatus.succeeded, JobStatus.failed, JobStatus.queued}
    ),
    **{s: frozenset() for s in TERMINAL},
}


class ErrorCategory(StrEnum):
    transient = "transient"
    infrastructure = "infrastructure"  # database / queue unavailable
    network = "network"
    backpressure = "backpressure"
    permanent = "permanent"
    authorization = "authorization"
    cancelled = "cancelled"
    deadline_exceeded = "deadline_exceeded"
    lease_expired = "lease_expired"
    retry_exhausted = "retry_exhausted"
    prerequisite_failed = "prerequisite_failed"
    uncertain_side_effect = "uncertain_side_effect"
    operator = "operator"


class InvalidTransition(RuntimeError):
    def __init__(self, job_id: str, src: str, dst: str) -> None:
        super().__init__(f"job {job_id}: invalid transition {src} -> {dst}")
        self.job_id, self.src, self.dst = job_id, src, dst


class QueueBackpressure(RuntimeError):
    """A project's pending queue is full. API callers map this to 429."""

    code = ErrorCode.RATE_LIMITED

    def __init__(self, project_id: str, pending: int, limit: int) -> None:
        super().__init__(f"project queue full ({pending}/{limit} pending jobs)")
        self.project_id, self.pending, self.limit = project_id, pending, limit
        self.retry_after_s = 30


def validate_transition(job_id: str, src: str, dst: str) -> None:
    if JobStatus(dst) not in TRANSITIONS[JobStatus(src)]:
        raise InvalidTransition(job_id, src, dst)


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with bounded positive jitter. Defaults keep the original
    ``min(300, 2**attempts)`` curve; jitter spreads synchronized retries."""

    base_s: float = 1.0
    factor: float = 2.0
    max_s: float = 300.0
    jitter: float = 0.1

    def delay_s(self, attempts: int) -> float:
        d = min(self.max_s, self.base_s * self.factor**attempts)
        return d + random.uniform(0, self.jitter * d)


DEFAULT_RETRY_POLICY = RetryPolicy()
RETRY_POLICIES: dict[str, RetryPolicy] = {}


def register_retry_policy(kind: str, policy: RetryPolicy) -> None:
    RETRY_POLICIES[kind] = policy


def retry_policy_for(kind: str) -> RetryPolicy:
    return RETRY_POLICIES.get(kind, DEFAULT_RETRY_POLICY)


# Control jobs (including emergency stop) are never refused by backpressure.
BACKPRESSURE_EXEMPT_KINDS = frozenset(CONTROL_JOB_KINDS)

# Emergency/cleanup operations get their own bounded claim capacity so they stay
# claimable when ordinary per-project and global limits are saturated. New apply
# operations (``live_defense_execute``) and verification deliberately do NOT get
# this exemption — they consume ordinary capacity like any other job.
EMERGENCY_JOB_KINDS = frozenset(
    {"live_defense_emergency_stop", "live_defense_rollback", "live_defense_expire"}
)

# Advisory lock keys (two-int keyspace; does not collide with modes.py's bigint keys).
CLAIM_LOCK = (7920, 1)
RECONCILE_LOCK = (7920, 2)


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    defer_count: int
    lease_owner: str
    lease_expires_at: datetime
    project_id: str | None = None
    deadline_at: datetime | None = None


_ROW_COLS = (
    jobs.c.id,
    jobs.c.kind,
    jobs.c.status,
    jobs.c.project_id,
    jobs.c.attempts,
    jobs.c.max_attempts,
    jobs.c.cancel_requested,
    jobs.c.deadline_at,
    jobs.c.effect_state,
    jobs.c.effect_key,
    jobs.c.lease_owner,
    jobs.c.lease_expires_at,
    jobs.c.heartbeat_at,
    jobs.c.depends_on,
    jobs.c.defer_count,
)


def record_event(
    conn: Connection,
    row: Any,
    to_status: str,
    *,
    from_status: str | None = None,
    worker: str | None = None,
    category: str | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        sa.insert(job_events).values(
            job_id=row["id"],
            project_id=row["project_id"],
            kind=row["kind"],
            from_status=from_status if from_status is not None else row["status"],
            to_status=to_status,
            attempt=row["attempts"],
            worker=worker,
            category=category,
            reason=(reason or "")[:2000] or None,
            details=details,
        )
    )


def transition(
    conn: Connection,
    row: Any,
    to_status: JobStatus,
    *,
    worker: str | None = None,
    category: str | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    **values: Any,
) -> None:
    """Validate, apply and record one transition. ``row`` must be locked by the caller."""
    validate_transition(row["id"], row["status"], to_status)
    if to_status in TERMINAL:
        values.setdefault("finished_at", sa.func.now())
    if category is not None:
        values.setdefault("error_category", category)
    res = conn.execute(
        sa.update(jobs)
        .where(jobs.c.id == row["id"], jobs.c.status == row["status"])
        .values(status=to_status.value, updated_at=sa.func.now(), **values)
    )
    if res.rowcount != 1:
        raise InvalidTransition(row["id"], row["status"], to_status)
    record_event(
        conn, row, to_status.value, worker=worker, category=category, reason=reason, details=details
    )
    if to_status in TERMINAL:
        settle_dependents(conn, row["id"], to_status)


def _locked(conn: Connection, job_id: str, *where: Any, skip_locked: bool = False) -> Any:
    return (
        conn.execute(
            sa.select(*_ROW_COLS)
            .where(jobs.c.id == job_id, *where)
            .with_for_update(skip_locked=skip_locked)
        )
        .mappings()
        .first()
    )


def _project_for(conn: Connection, payload: dict[str, Any]) -> str | None:
    pid = payload.get("project_id")
    if isinstance(pid, str) and pid:
        return pid
    aid = payload.get("assessment_id")
    if isinstance(aid, str) and aid:
        return conn.execute(
            sa.select(assessments.c.project_id).where(assessments.c.id == aid)
        ).scalar_one_or_none()
    return None


def pending_count(conn: Connection, project_id: str) -> int:
    return int(
        conn.execute(
            sa.select(sa.func.count())
            .select_from(jobs)
            .where(jobs.c.project_id == project_id, jobs.c.status.in_(("queued", "waiting")))
        ).scalar_one()
    )


def enqueue(
    conn: Connection,
    kind: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    priority: int = 100,
    delay_s: float = 0,
    max_attempts: int = 5,
    project_id: str | None = None,
    deadline_s: float | None = None,
    deadline_at: datetime | None = None,
    depends_on: str | None = None,
) -> tuple[str, bool]:
    """Insert a job; returns (job_id, created). An existing dedupe_key returns that job.

    ``project_id`` defaults to the payload's project (or its assessment's). It scopes
    per-project concurrency, backpressure and observability. ``deadline_s`` bounds the
    whole execution including retries; pass ``deadline_at`` instead to propagate an
    existing absolute deadline across follow-on enqueues (it is never extended by
    redelivery). ``depends_on`` parks the job in ``waiting`` until that job succeeds
    (and fails it if the prerequisite ends unsuccessfully).
    """
    if dedupe_key is not None:
        existing = conn.execute(
            sa.select(jobs.c.id).where(jobs.c.dedupe_key == dedupe_key)
        ).scalar_one_or_none()
        if existing:
            return existing, False
    project_id = project_id or _project_for(conn, payload)
    limit = get_settings().job_max_pending_per_project
    if project_id and limit > 0 and kind not in BACKPRESSURE_EXEMPT_KINDS:
        pending = pending_count(conn, project_id)
        if pending >= limit:
            raise QueueBackpressure(project_id, pending, limit)
    status = JobStatus.queued
    if depends_on is not None:
        dep = conn.execute(sa.select(jobs.c.status).where(jobs.c.id == depends_on)).scalar()
        if dep is None:
            raise ValueError(f"unknown prerequisite job {depends_on!r}")
        if dep != JobStatus.succeeded:
            status = JobStatus.waiting
    now = utcnow()
    job_id = new_id(IdPrefix.job)
    stmt = (
        sa.dialects.postgresql.insert(jobs)
        .values(
            id=job_id,
            kind=kind,
            payload=payload,
            status=status.value,
            priority=priority,
            run_after=now + timedelta(seconds=delay_s),
            max_attempts=max_attempts,
            dedupe_key=dedupe_key,
            project_id=project_id,
            depends_on=depends_on,
            deadline_at=deadline_at
            if deadline_at is not None
            else (now + timedelta(seconds=deadline_s) if deadline_s is not None else None),
        )
        .on_conflict_do_nothing(index_elements=[jobs.c.dedupe_key])
        .returning(jobs.c.id)
    )
    created = conn.execute(stmt).scalar()
    if created:
        row = {"id": created, "project_id": project_id, "kind": kind, "attempts": 0}
        record_event(
            conn,
            row,
            status.value,
            from_status="",
            reason="enqueued",
            details={"depends_on": depends_on} if depends_on else None,
        )
        if status is JobStatus.waiting:
            settle_waiting(conn, created)
        return created, True
    existing = conn.execute(
        sa.select(jobs.c.id).where(jobs.c.dedupe_key == dedupe_key)
    ).scalar_one()
    return existing, False


def settle_waiting(conn: Connection, job_id: str) -> str | None:
    """Promote or fail one waiting job from its prerequisite's state."""
    row = _locked(conn, job_id, jobs.c.status == JobStatus.waiting.value)
    if row is None:
        return None
    dep = conn.execute(sa.select(jobs.c.status).where(jobs.c.id == row["depends_on"])).scalar()
    if dep == JobStatus.succeeded:
        transition(conn, row, JobStatus.queued, reason="prerequisite succeeded", run_after=utcnow())
        return JobStatus.queued.value
    if dep is None or JobStatus(dep) in TERMINAL:
        transition(
            conn,
            row,
            JobStatus.failed,
            category=ErrorCategory.prerequisite_failed,
            reason=f"prerequisite {row['depends_on']} ended {dep or 'missing'}",
            last_error={
                "code": ErrorCode.CONFLICT_STATE.value,
                "message": f"prerequisite job ended {dep or 'missing'}",
                "category": ErrorCategory.prerequisite_failed.value,
            },
        )
        return JobStatus.failed.value
    return None


def settle_dependents(conn: Connection, job_id: str, status: JobStatus) -> None:
    ids = conn.execute(
        sa.select(jobs.c.id).where(
            jobs.c.depends_on == job_id, jobs.c.status == JobStatus.waiting.value
        )
    ).scalars()
    for dep_id in list(ids):
        settle_waiting(conn, dep_id)


_CLAIM_SQL = sa.text(
    """
    WITH running AS (
      SELECT project_id, count(*) AS n FROM jobs
      WHERE status = 'running' AND lease_expires_at >= now()
        AND kind <> ALL(:emergency_kinds)
      GROUP BY project_id
    ),
    emergency_running AS (
      SELECT count(*) AS n FROM jobs
      WHERE status = 'running' AND lease_expires_at >= now()
        AND kind = ANY(:emergency_kinds)
    ),
    picked AS (
      SELECT j.id, j.status AS prev_status, j.lease_owner AS prev_owner,
             j.lease_expires_at AS prev_lease_expires_at
      FROM jobs j LEFT JOIN running r ON r.project_id = j.project_id
      WHERE ((j.status = 'queued' AND j.run_after <= clock_timestamp())
          OR (j.status = 'running' AND j.lease_expires_at < now() AND j.effect_state IS NULL))
        AND j.cancel_requested IS NOT TRUE
        AND (j.deadline_at IS NULL OR j.deadline_at > now())
        AND (:kinds_empty OR j.kind = ANY(:kinds))
        AND (
          -- Emergency/cleanup kinds: own bounded capacity, exempt from ordinary limits.
          (j.kind = ANY(:emergency_kinds) AND :emergency_limit > 0
            AND (SELECT n FROM emergency_running) < :emergency_limit)
          OR
          -- Ordinary jobs (and emergency kinds when no reserve is configured).
          ((j.kind <> ALL(:emergency_kinds) OR :emergency_limit <= 0)
            AND (j.project_id IS NULL OR :project_limit <= 0 OR coalesce(r.n, 0) < :project_limit)
            AND (:global_limit <= 0
                 OR (SELECT coalesce(sum(n), 0) FROM running) < :global_limit))
        )
      ORDER BY j.priority, coalesce(r.n, 0), j.run_after, j.id
      FOR UPDATE OF j SKIP LOCKED
      LIMIT 1
    )
    UPDATE jobs SET
      status = 'running',
      attempts = jobs.attempts + 1,
      lease_owner = :owner,
      lease_expires_at = now() + make_interval(secs => :lease_s),
      heartbeat_at = now(),
      started_at = now(),
      first_started_at = coalesce(jobs.first_started_at, now()),
      updated_at = now()
    FROM picked
    WHERE jobs.id = picked.id
    RETURNING jobs.id, jobs.kind, jobs.payload, jobs.attempts, jobs.max_attempts,
              jobs.defer_count, jobs.lease_owner, jobs.lease_expires_at, jobs.project_id,
              jobs.deadline_at,
              picked.prev_status, picked.prev_owner, picked.prev_lease_expires_at
    """
)


def claim(conn: Connection, owner: str, lease_s: int, kinds: list[str] | None = None) -> Job | None:
    """Claim the next runnable job, fairly across projects.

    Ordering is priority first, then the least-busy project, then age, so one noisy
    project cannot starve another at equal priority. When concurrency limits are on,
    claims serialize on an advisory lock so the limits hold across workers (claims
    are a single short statement; ADR-0005 targets a handful of workers).
    """
    settings = get_settings()
    project_limit = settings.job_max_running_per_project
    global_limit = settings.job_max_running_global
    emergency_limit = get_settings().job_max_running_emergency
    if project_limit > 0 or global_limit > 0 or emergency_limit > 0:
        conn.execute(
            sa.text("SELECT pg_advisory_xact_lock(:a, :b)"),
            {"a": CLAIM_LOCK[0], "b": CLAIM_LOCK[1]},
        )
    row = (
        conn.execute(
            _CLAIM_SQL,
            {
                "owner": owner,
                "lease_s": lease_s,
                "kinds": kinds or [],
                "kinds_empty": not kinds,
                "project_limit": project_limit,
                "global_limit": global_limit,
                "emergency_limit": emergency_limit,
                "emergency_kinds": sorted(EMERGENCY_JOB_KINDS),
            },
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    data = dict(row)
    prev_status, prev_owner = data.pop("prev_status"), data.pop("prev_owner")
    prev_lease = data.pop("prev_lease_expires_at")
    job = Job(**data)
    ev = {"id": job.id, "project_id": job.project_id, "kind": job.kind, "attempts": job.attempts}
    reclaimed = prev_status == JobStatus.running
    record_event(
        conn,
        ev,
        JobStatus.running.value,
        from_status=prev_status,
        worker=owner,
        category=ErrorCategory.lease_expired.value if reclaimed else None,
        reason="lease re-claimed after previous owner lost it" if reclaimed else "claimed",
        details=(
            {
                "previous_owner": prev_owner,
                "previous_lease_expires_at": prev_lease.isoformat() if prev_lease else None,
            }
            if reclaimed
            else None
        ),
    )
    if job.attempts - job.defer_count > job.max_attempts:
        locked = _locked(conn, job.id)
        transition(
            conn,
            locked,
            JobStatus.dead,
            worker=owner,
            category=ErrorCategory.retry_exhausted,
            reason="attempts exhausted (lease expired or repeated failure)",
            lease_owner=None,
            lease_expires_at=None,
            last_error={
                "code": ErrorCode.JOB_LEASE_EXPIRED.value,
                "message": "attempts exhausted (lease expired or repeated failure)",
                "category": ErrorCategory.retry_exhausted.value,
            },
        )
        return None
    return job


def heartbeat_state(conn: Connection, job_id: str, owner: str, lease_s: int) -> str:
    """Extend the lease. Returns ``ok``, ``lost``, ``cancelled`` or ``deadline``."""
    row = conn.execute(
        sa.update(jobs)
        .where(jobs.c.id == job_id, jobs.c.lease_owner == owner, jobs.c.status == "running")
        .values(
            lease_expires_at=sa.func.now() + sa.text(f"interval '{int(lease_s)} seconds'"),
            heartbeat_at=sa.func.now(),
        )
        .returning(
            jobs.c.cancel_requested,
            sa.and_(jobs.c.deadline_at.is_not(None), jobs.c.deadline_at <= sa.func.now()),
        )
    ).first()
    if row is None:
        return "lost"
    if row[0]:
        return "cancelled"
    if row[1]:
        return "deadline"
    return "ok"


def heartbeat(conn: Connection, job_id: str, owner: str, lease_s: int) -> bool:
    """Extend the lease. Returns False if the lease was lost or cancellation requested."""
    return heartbeat_state(conn, job_id, owner, lease_s) == "ok"


def complete(
    conn: Connection, job_id: str, owner: str, result: dict[str, Any] | None = None
) -> bool:
    """Succeed only while this worker still holds a running, not-cancelled lease.

    The original owner of a ``recovery_required`` job may still complete it: it
    observed the outcome of the uncertain effect, which resolves the uncertainty.
    """
    row = _locked(conn, job_id, jobs.c.lease_owner == owner)
    if row is None:
        return False
    late = row["status"] == JobStatus.recovery_required and row["effect_state"] is not None
    if not late:
        # Fencing: ordinary completion requires a currently valid lease. A worker
        # whose lease expired (even before reconciliation notices) cannot report
        # success — its attempt is recovered through the lease-expiry path.
        fenced = conn.execute(
            sa.select(jobs.c.id).where(
                jobs.c.id == job_id,
                jobs.c.status == JobStatus.running.value,
                jobs.c.cancel_requested.is_not(True),
                jobs.c.lease_expires_at > sa.func.now(),
            )
        ).scalar_one_or_none()
        if fenced is None:
            return False
    transition(
        conn,
        row,
        JobStatus.succeeded,
        worker=owner,
        reason="late completion by original owner resolved uncertain effect"
        if late
        else "completed",
        result=result or {},
        lease_owner=None,
        lease_expires_at=None,
        effect_state="settled" if row["effect_state"] else None,
    )
    return True


def defer(
    conn: Connection,
    job_id: str,
    owner: str,
    *,
    delay_s: float = 0,
    depends_on: str | None = None,
    note: str | None = None,
) -> str | None:
    """Return a claimed job to the queue without consuming an execution attempt.

    The transition is validated and recorded in ``job_events`` like every other
    lifecycle change, and ``defer_count`` makes the claim/defer pair neutral on the
    retry budget. With ``depends_on`` the job parks in ``waiting`` until that job
    settles. Returns the new status, or ``None`` when deferral is refused: the
    lease was lost, or an external effect is in flight (an uncertain effect must
    never be replayed — the job stays ``running`` for the recovery path).
    Cancellation and an elapsed deadline take precedence over the deferral.
    """
    row = _locked(conn, job_id, jobs.c.lease_owner == owner)
    if row is None or row["status"] != JobStatus.running:
        return None
    if row["effect_state"] is not None:
        return None
    common: dict[str, Any] = {"lease_owner": None, "lease_expires_at": None}
    if row["cancel_requested"]:
        transition(
            conn,
            row,
            JobStatus.cancelled,
            worker=owner,
            category=ErrorCategory.cancelled,
            reason="cancelled while deferring",
            **common,
        )
        return JobStatus.cancelled.value
    overdue = conn.execute(
        sa.select(jobs.c.deadline_at <= sa.func.now()).where(jobs.c.id == job_id)
    ).scalar_one()
    if overdue:
        transition(
            conn,
            row,
            JobStatus.timed_out,
            worker=owner,
            category=ErrorCategory.deadline_exceeded,
            reason="deadline passed while deferring",
            **common,
        )
        return JobStatus.timed_out.value
    # DB clock, consistent with claim's run_after comparison and reconciliation.
    run_after = sa.func.now() + timedelta(seconds=delay_s)
    if depends_on is not None:
        dep = conn.execute(sa.select(jobs.c.status).where(jobs.c.id == depends_on)).scalar()
        if dep is None:
            raise ValueError(f"unknown prerequisite job {depends_on!r}")
        to_status = JobStatus.waiting if dep != JobStatus.succeeded else JobStatus.queued
    else:
        to_status = JobStatus.queued
    transition(
        conn,
        row,
        to_status,
        worker=owner,
        reason=note or ("deferred onto prerequisite" if depends_on else "deferred"),
        details={"delay_s": delay_s, "depends_on": depends_on},
        run_after=run_after,
        depends_on=depends_on if depends_on is not None else row["depends_on"],
        defer_count=row["defer_count"] + 1,
        **common,
    )
    return to_status.value


def is_cancel_requested(conn: Connection, job_id: str) -> bool:
    row = conn.execute(sa.select(jobs.c.cancel_requested).where(jobs.c.id == job_id)).first()
    return bool(row and row[0])


def fail(
    conn: Connection,
    job_id: str,
    owner: str,
    error: dict[str, Any],
    *,
    retryable: bool,
    category: ErrorCategory | None = None,
) -> str:
    """Record failure; requeue with backoff while attempts and the deadline allow.

    Precedence: an effect that may have happened -> ``recovery_required`` (never
    replayed); cancellation -> ``cancelled``; deadline -> ``timed_out``; then retry,
    ``dead`` (retry budget exhausted) or ``failed`` (permanent).
    """
    row = _locked(conn, job_id, jobs.c.lease_owner == owner)
    if row is None:
        return "lost"
    if row["status"] != JobStatus.running:
        return str(row["status"])
    # Fencing: an expired lease cannot record ordinary outcomes either — the
    # expired-lease path in reconciliation owns that recovery.
    fenced = conn.execute(
        sa.select(jobs.c.id).where(jobs.c.id == job_id, jobs.c.lease_expires_at > sa.func.now())
    ).scalar_one_or_none()
    if fenced is None:
        return "lost"
    if category is None:
        if error.get("code") == ErrorCode.CANCELLED.value:
            category = ErrorCategory.cancelled
        else:
            category = ErrorCategory.transient if retryable else ErrorCategory.permanent
    now = utcnow()
    overdue = row["deadline_at"] is not None and row["deadline_at"] <= now
    common: dict[str, Any] = {"lease_owner": None, "lease_expires_at": None}
    if row["effect_state"] is not None:
        diag = {
            "reason": ErrorCategory.uncertain_side_effect.value,
            "effect_key": row["effect_key"],
            "effect_state": row["effect_state"],
            "attempt": row["attempts"],
            "worker": owner,
            "error_category": category.value,
            "detected_at": now.isoformat(),
            "required_action": "confirm whether the external effect was applied, then "
            "resolve as applied, not_applied or retry",
        }
        transition(
            conn,
            row,
            JobStatus.recovery_required,
            worker=owner,
            category=ErrorCategory.uncertain_side_effect,
            reason=f"external effect outcome uncertain after {category.value} failure",
            details=diag,
            recovery=diag,
            last_error={**error, "category": category.value},
            lease_owner=owner,
            lease_expires_at=None,
        )
        return JobStatus.recovery_required.value
    if row["cancel_requested"]:
        status, category = JobStatus.cancelled, ErrorCategory.cancelled
    elif overdue or category is ErrorCategory.deadline_exceeded:
        status, category = JobStatus.timed_out, ErrorCategory.deadline_exceeded
    elif retryable and row["attempts"] - row["defer_count"] < row["max_attempts"]:
        run_after = now + timedelta(seconds=retry_policy_for(row["kind"]).delay_s(row["attempts"]))
        if row["deadline_at"] is not None and run_after >= row["deadline_at"]:
            status, category = JobStatus.timed_out, ErrorCategory.deadline_exceeded
        else:
            transition(
                conn,
                row,
                JobStatus.queued,
                worker=owner,
                category=category,
                reason="retry scheduled",
                details={"run_after": run_after.isoformat()},
                run_after=run_after,
                last_error={**error, "category": category.value},
                **common,
            )
            return JobStatus.queued.value
    elif retryable:
        status, category = JobStatus.dead, ErrorCategory.retry_exhausted
    else:
        status = JobStatus.failed
    transition(
        conn,
        row,
        status,
        worker=owner,
        category=category,
        reason=str(error.get("message") or status.value)[:500],
        run_after=now,
        last_error={**error, "category": category.value},
        **common,
    )
    return status.value


def begin_effect(conn: Connection, job_id: str, owner: str, effect_key: str) -> bool:
    """Durably mark that an external side effect is about to be attempted.

    Must commit before the effect starts. Returns False (do not start the effect)
    when this worker no longer owns a live, uncancelled, in-deadline lease.
    """
    row = _locked(
        conn,
        job_id,
        jobs.c.lease_owner == owner,
        jobs.c.status == JobStatus.running.value,
        jobs.c.cancel_requested.is_not(True),
        jobs.c.lease_expires_at > sa.func.now(),
        sa.or_(jobs.c.deadline_at.is_(None), jobs.c.deadline_at > sa.func.now()),
    )
    if row is None:
        return False
    conn.execute(
        sa.update(jobs)
        .where(jobs.c.id == job_id)
        .values(effect_state="in_flight", effect_key=effect_key[:200], updated_at=sa.func.now())
    )
    record_event(
        conn,
        row,
        row["status"],
        worker=owner,
        reason="external effect started",
        details={"effect_key": effect_key[:200]},
    )
    return True


def end_effect(conn: Connection, job_id: str, owner: str, *, applied: bool) -> None:
    """Record the observed outcome. ``applied=False`` asserts the effect definitely did
    not happen, which makes the job safely retryable again — unless an earlier effect
    for this job was already applied, in which case a replay is still unsafe and the
    job keeps its ``settled`` marker (failures then end ``recovery_required``)."""
    row = _locked(
        conn, job_id, jobs.c.lease_owner == owner, jobs.c.status == JobStatus.running.value
    )
    if row is None:
        return
    if applied:
        effect_state: str | None = "settled"
    else:
        prior_applied = conn.execute(
            sa.select(sa.func.count())
            .select_from(job_events)
            .where(
                job_events.c.job_id == job_id,
                job_events.c.reason == "external effect settled",
            )
        ).scalar_one()
        effect_state = "settled" if prior_applied else None
    conn.execute(
        sa.update(jobs)
        .where(jobs.c.id == job_id)
        .values(effect_state=effect_state, updated_at=sa.func.now())
    )
    record_event(
        conn,
        row,
        row["status"],
        worker=owner,
        reason="external effect settled" if applied else "external effect not applied",
        details={"effect_key": row["effect_key"]},
    )


def request_cancel(conn: Connection, job_id: str) -> None:
    row = _locked(conn, job_id)
    if row is None:
        return
    conn.execute(
        sa.update(jobs)
        .where(jobs.c.id == job_id)
        .values(cancel_requested=True, updated_at=sa.func.now())
    )
    if row["status"] in PENDING:
        transition(
            conn,
            row,
            JobStatus.cancelled,
            category=ErrorCategory.cancelled,
            reason="cancelled before start",
        )
    elif (
        row["status"] in (JobStatus.running, JobStatus.recovery_required)
        and not row["cancel_requested"]
    ):
        record_event(conn, row, row["status"], reason="cancel requested")


def queue_reachable(conn: Connection) -> bool:
    conn.execute(sa.select(sa.func.count()).select_from(jobs).where(jobs.c.status == "queued"))
    return True
