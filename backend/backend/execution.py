"""Execution reliability operations: reconciliation, stuck detection, operational
metrics, worker registry and operator recovery resolution.

Metrics are computed from ``jobs``/``job_events``/``worker_heartbeats`` with SQL
(docs/19: DB-query metrics, no separate observability backend).

    uv run python -m backend.execution stats [--project PROJECT_ID]
    uv run python -m backend.execution stuck [--project PROJECT_ID]
    uv run python -m backend.execution reconcile
"""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import jobs as jobq
from backend.db import job_events, jobs, transaction, worker_heartbeats
from backend.jobs import ErrorCategory, JobStatus
from backend.settings import get_settings
from shared.base import utcnow
from shared.errors import ErrorCode

Resolution = Literal["applied", "not_applied", "retry"]


# --- worker registry -------------------------------------------------------------


def worker_seen(
    conn: Connection,
    worker_id: str,
    state: Literal["idle", "busy", "stopping"],
    current_job_id: str | None = None,
    *,
    finished: int = 0,
) -> None:
    stmt = sa.dialects.postgresql.insert(worker_heartbeats).values(
        worker_id=worker_id,
        state=state,
        current_job_id=current_job_id,
        jobs_finished=finished,
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[worker_heartbeats.c.worker_id],
            set_={
                "state": state,
                "current_job_id": current_job_id,
                "jobs_finished": worker_heartbeats.c.jobs_finished + finished,
                "last_seen_at": sa.func.now(),
            },
        )
    )


def worker_gone(conn: Connection, worker_id: str) -> None:
    conn.execute(sa.delete(worker_heartbeats).where(worker_heartbeats.c.worker_id == worker_id))


# --- reconciliation ----------------------------------------------------------------


def reconcile(conn: Connection) -> dict[str, Any]:
    """Reconcile queue state after crashes, restarts and interruptions.

    Safe to run from every worker: a transaction-scoped try-lock lets one run at a
    time, and rows a live worker is mutating are skipped (SKIP LOCKED). Never
    re-runs a job whose external effect may have happened.
    """
    got = conn.execute(
        sa.text("SELECT pg_try_advisory_xact_lock(:a, :b)"),
        {"a": jobq.RECONCILE_LOCK[0], "b": jobq.RECONCILE_LOCK[1]},
    ).scalar()
    report: dict[str, Any] = {
        "skipped": not got,
        "requeued": 0,
        "dead": 0,
        "cancelled": 0,
        "timed_out": 0,
        "recovery_required": 0,
        "promoted": 0,
        "prerequisite_failed": 0,
        "workers_pruned": 0,
    }
    if not got:
        return report
    now = utcnow()
    expired = (
        conn.execute(
            sa.select(*jobq._ROW_COLS)
            .where(jobs.c.status == "running", jobs.c.lease_expires_at < sa.func.now())
            .order_by(jobs.c.lease_expires_at)
            .with_for_update(skip_locked=True)
        )
        .mappings()
        .all()
    )
    for row in expired:
        diag = {
            "previous_owner": row["lease_owner"],
            "lease_expired_at": row["lease_expires_at"].isoformat(),
            "last_heartbeat_at": row["heartbeat_at"].isoformat() if row["heartbeat_at"] else None,
            "attempt": row["attempts"],
            "detected_at": now.isoformat(),
        }
        if row["effect_state"] is not None:
            diag |= {
                "reason": ErrorCategory.uncertain_side_effect.value,
                "effect_key": row["effect_key"],
                "effect_state": row["effect_state"],
                "required_action": "confirm whether the external effect was applied, then "
                "resolve as applied, not_applied or retry",
            }
            jobq.transition(
                conn,
                row,
                JobStatus.recovery_required,
                category=ErrorCategory.uncertain_side_effect,
                reason="worker lost its lease after starting an external effect",
                details=diag,
                recovery=diag,
                lease_expires_at=None,
            )
            report["recovery_required"] += 1
        elif row["cancel_requested"]:
            jobq.transition(
                conn,
                row,
                JobStatus.cancelled,
                category=ErrorCategory.cancelled,
                reason="cancellation confirmed: owning worker lost its lease",
                details=diag,
                lease_owner=None,
                lease_expires_at=None,
            )
            report["cancelled"] += 1
        elif row["deadline_at"] is not None and row["deadline_at"] <= now:
            jobq.transition(
                conn,
                row,
                JobStatus.timed_out,
                category=ErrorCategory.deadline_exceeded,
                reason="deadline passed while the owning worker was lost",
                details=diag,
                lease_owner=None,
                lease_expires_at=None,
            )
            report["timed_out"] += 1
        elif row["attempts"] - row["defer_count"] >= row["max_attempts"]:
            jobq.transition(
                conn,
                row,
                JobStatus.dead,
                category=ErrorCategory.retry_exhausted,
                reason="lease expired on the final attempt",
                details=diag,
                lease_owner=None,
                lease_expires_at=None,
                last_error={
                    "code": ErrorCode.JOB_LEASE_EXPIRED.value,
                    "message": "attempts exhausted (lease expired or repeated failure)",
                    "category": ErrorCategory.retry_exhausted.value,
                },
            )
            report["dead"] += 1
        else:
            jobq.transition(
                conn,
                row,
                JobStatus.queued,
                category=ErrorCategory.lease_expired,
                reason="worker lost its lease; requeued",
                details=diag,
                lease_owner=None,
                lease_expires_at=None,
                run_after=now,
            )
            report["requeued"] += 1
    overdue = (
        conn.execute(
            sa.select(*jobq._ROW_COLS)
            .where(
                jobs.c.status.in_(("queued", "waiting")),
                jobs.c.deadline_at.is_not(None),
                jobs.c.deadline_at <= sa.func.now(),
            )
            .with_for_update(skip_locked=True)
        )
        .mappings()
        .all()
    )
    for row in overdue:
        jobq.transition(
            conn,
            row,
            JobStatus.timed_out,
            category=ErrorCategory.deadline_exceeded,
            reason="deadline passed before the job could run",
        )
        report["timed_out"] += 1
    waiting = conn.execute(sa.select(jobs.c.id).where(jobs.c.status == "waiting")).scalars().all()
    for job_id in waiting:
        outcome = jobq.settle_waiting(conn, job_id)
        if outcome == JobStatus.queued:
            report["promoted"] += 1
        elif outcome == JobStatus.failed:
            report["prerequisite_failed"] += 1
    stale = timedelta(seconds=get_settings().worker_stale_s * 10)
    report["workers_pruned"] = conn.execute(
        sa.delete(worker_heartbeats).where(worker_heartbeats.c.last_seen_at < now - stale)
    ).rowcount
    return report


# --- stuck detection ---------------------------------------------------------------


def stuck_jobs(
    conn: Connection, project_id: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    """Executions that need attention, with the reason they are considered stuck."""
    s = get_settings()
    now = sa.func.now()
    pending_cutoff = now - sa.text(f"interval '{int(s.job_stuck_pending_s)} seconds'")
    reason = sa.case(
        (jobs.c.status == "recovery_required", "recovery_required"),
        (
            sa.and_(jobs.c.status == "running", jobs.c.lease_expires_at < now),
            "lease_expired",
        ),
        (
            sa.and_(jobs.c.status == "running", jobs.c.deadline_at <= now),
            "deadline_exceeded",
        ),
        (
            sa.and_(jobs.c.status == "queued", jobs.c.run_after < pending_cutoff),
            "queued_starved",
        ),
        (
            sa.and_(jobs.c.status == "waiting", jobs.c.created_at < pending_cutoff),
            "waiting_on_prerequisite",
        ),
        else_=None,
    ).label("reason")
    q = sa.select(
        jobs.c.id,
        jobs.c.kind,
        jobs.c.project_id,
        jobs.c.status,
        jobs.c.attempts,
        jobs.c.lease_owner,
        jobs.c.depends_on,
        jobs.c.error_category,
        jobs.c.recovery,
        sa.extract("epoch", now - jobs.c.updated_at).label("since_update_s"),
        reason,
    ).subquery()
    stmt = sa.select(q).where(q.c.reason.is_not(None))
    if project_id is not None:
        stmt = stmt.where(q.c.project_id == project_id)
    rows = conn.execute(stmt.order_by(q.c.since_update_s.desc()).limit(limit)).mappings()
    return [{**dict(r), "since_update_s": round(float(r["since_update_s"] or 0), 1)} for r in rows]


# --- operational metrics -----------------------------------------------------------


def _pct(col: Any) -> list[Any]:
    return [
        sa.func.percentile_cont(0.5).within_group(col).label("p50"),
        sa.func.percentile_cont(0.95).within_group(col).label("p95"),
        sa.func.max(col).label("max"),
        sa.func.count(col).label("samples"),
    ]


def _dist(row: Any) -> dict[str, Any]:
    def r(v: Any) -> float | None:
        return round(float(v), 3) if v is not None else None

    return {"p50_s": r(row.p50), "p95_s": r(row.p95), "max_s": r(row.max), "samples": row.samples}


def stats(conn: Connection, project_id: str | None = None, window_s: int = 86400) -> dict[str, Any]:
    """Operational snapshot. ``project_id`` scopes every job-derived figure; worker
    counts are fleet-wide and only reported for the unscoped (operator) view."""
    s = get_settings()
    now = sa.func.now()
    since = now - sa.text(f"interval '{int(window_s)} seconds'")
    scope = [jobs.c.project_id == project_id] if project_id is not None else []
    ev_scope = [job_events.c.project_id == project_id] if project_id is not None else []

    counts = {st.value: 0 for st in JobStatus}
    for st, n in conn.execute(
        sa.select(jobs.c.status, sa.func.count()).where(*scope).group_by(jobs.c.status)
    ):
        counts[st] = n

    wait_s = sa.extract("epoch", jobs.c.first_started_at - jobs.c.created_at)
    wait = conn.execute(
        sa.select(*_pct(wait_s)).where(*scope, jobs.c.first_started_at >= since)
    ).one()
    oldest = conn.execute(
        sa.select(sa.func.max(sa.extract("epoch", now - jobs.c.created_at))).where(
            *scope, jobs.c.status.in_(("queued", "waiting"))
        )
    ).scalar()

    dur_s = sa.extract("epoch", jobs.c.finished_at - jobs.c.first_started_at)
    duration = conn.execute(
        sa.select(*_pct(dur_s)).where(
            *scope, jobs.c.finished_at >= since, jobs.c.first_started_at.is_not(None)
        )
    ).one()

    lease = conn.execute(
        sa.select(
            sa.func.count(),
            sa.func.max(sa.extract("epoch", now - jobs.c.started_at)),
            sa.func.max(sa.extract("epoch", now - jobs.c.heartbeat_at)),
            sa.func.count().filter(jobs.c.lease_expires_at < now),
        ).where(*scope, jobs.c.status == "running")
    ).one()

    retried, retries = conn.execute(
        sa.select(
            sa.func.count().filter(jobs.c.attempts > 1),
            sa.func.coalesce(sa.func.sum(sa.func.greatest(jobs.c.attempts - 1, 0)), 0),
        ).where(*scope, jobs.c.updated_at >= since)
    ).one()

    cancel: dict[str, int] = {"before_start": 0, "while_running": 0, "after_lease_loss": 0}
    for src, reason_txt, n in conn.execute(
        sa.select(job_events.c.from_status, job_events.c.reason, sa.func.count())
        .where(*ev_scope, job_events.c.to_status == "cancelled", job_events.c.created_at >= since)
        .group_by(job_events.c.from_status, job_events.c.reason)
    ):
        if src in ("queued", "waiting"):
            cancel["before_start"] += n
        elif reason_txt and "lost its lease" in reason_txt:
            cancel["after_lease_loss"] += n
        else:
            cancel["while_running"] += n
    cancel["pending"] = conn.execute(
        sa.select(sa.func.count()).where(
            *scope, jobs.c.status == "running", jobs.c.cancel_requested.is_(True)
        )
    ).scalar_one()

    failures = {
        cat: n
        for cat, n in conn.execute(
            sa.select(job_events.c.category, sa.func.count())
            .where(
                *ev_scope,
                job_events.c.created_at >= since,
                job_events.c.category.is_not(None),
                job_events.c.to_status.in_(
                    ("queued", "failed", "dead", "timed_out", "recovery_required", "running")
                ),
            )
            .group_by(job_events.c.category)
        )
    }
    res_expr = job_events.c.details.op("->>")(sa.literal_column("'resolution'"))
    resolved = {
        res: n
        for res, n in conn.execute(
            sa.select(res_expr, sa.func.count())
            .where(*ev_scope, job_events.c.created_at >= since, res_expr.is_not(None))
            .group_by(res_expr)
        )
    }
    stuck = stuck_jobs(conn, project_id, limit=10_000)
    by_reason: dict[str, int] = {}
    for j in stuck:
        by_reason[j["reason"]] = by_reason.get(j["reason"], 0) + 1

    out: dict[str, Any] = {
        "generated_at": utcnow().isoformat(),
        "project_id": project_id,
        "window_s": window_s,
        "status_counts": counts,
        "queue": {
            "pending": counts["queued"] + counts["waiting"],
            "oldest_pending_age_s": round(float(oldest), 1) if oldest is not None else None,
            "wait": _dist(wait),
        },
        "leases": {
            "running": lease[0],
            "max_lease_age_s": round(float(lease[1]), 1) if lease[1] is not None else None,
            "max_heartbeat_age_s": round(float(lease[2]), 1) if lease[2] is not None else None,
            "expired": lease[3],
        },
        "execution": {"duration": _dist(duration)},
        "retries": {"jobs_retried": retried, "total_retries": int(retries)},
        "cancellation": cancel,
        "failure_categories": failures,
        "recovery": {"required": counts["recovery_required"], "resolved": resolved},
        "stuck": {"count": len(stuck), "by_reason": by_reason},
        "limits": {
            "max_running_per_project": s.job_max_running_per_project,
            "max_pending_per_project": s.job_max_pending_per_project,
        },
    }
    if project_id is None:
        fresh = now - sa.text(f"interval '{int(s.worker_stale_s)} seconds'")
        w = conn.execute(
            sa.select(
                sa.func.count().filter(worker_heartbeats.c.last_seen_at >= fresh),
                sa.func.count().filter(
                    worker_heartbeats.c.last_seen_at >= fresh, worker_heartbeats.c.state == "busy"
                ),
                sa.func.count().filter(worker_heartbeats.c.last_seen_at < fresh),
            )
        ).one()
        out["workers"] = {"active": w[0], "busy": w[1], "stale": w[2]}
        out["limits"]["max_running_global"] = s.job_max_running_global
    return out


def history(conn: Connection, job_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        sa.select(
            job_events.c.seq,
            job_events.c.from_status,
            job_events.c.to_status,
            job_events.c.attempt,
            job_events.c.worker,
            job_events.c.category,
            job_events.c.reason,
            job_events.c.details,
            job_events.c.created_at,
        )
        .where(job_events.c.job_id == job_id)
        .order_by(job_events.c.seq)
    ).mappings()
    return [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]


# --- operator recovery -------------------------------------------------------------


class RecoveryError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.status = code, status


def resolve_recovery(
    conn: Connection, job_id: str, *, resolution: Resolution, actor_id: str, note: str
) -> dict[str, Any]:
    """Operator decision for a ``recovery_required`` job.

    ``applied`` -> succeeded, ``not_applied`` -> failed, ``retry`` -> queued (the
    operator asserts the effect did not happen). A retried job runs its handler again,
    which re-checks authorization, mode and cancellation exactly as on first run; a job
    whose cancellation was requested cannot be retried.
    """
    row = jobq._locked(conn, job_id)
    if row is None:
        raise RecoveryError(ErrorCode.NOT_FOUND, "job not found", 404)
    if row["status"] != JobStatus.recovery_required:
        raise RecoveryError(
            ErrorCode.CONFLICT_STATE, f"job is {row['status']}, not recovery_required"
        )
    details = {"resolution": resolution, "actor_id": actor_id, "note": note[:1000]}
    common: dict[str, Any] = {"lease_owner": None, "lease_expires_at": None}
    if resolution == "applied":
        jobq.transition(
            conn,
            row,
            JobStatus.succeeded,
            worker=None,
            reason="operator confirmed the external effect was applied",
            details=details,
            effect_state="settled",
            result={"resolved_by_operator": True, "resolution": "applied"},
            **common,
        )
    elif resolution == "not_applied":
        jobq.transition(
            conn,
            row,
            JobStatus.failed,
            category=ErrorCategory.operator,
            reason="operator confirmed the external effect was not applied",
            details=details,
            effect_state=None,
            **common,
        )
    else:
        if row["cancel_requested"]:
            raise RecoveryError(
                ErrorCode.CONFLICT_STATE, "cancellation was requested; the job cannot be retried"
            )
        jobq.transition(
            conn,
            row,
            JobStatus.queued,
            category=ErrorCategory.operator,
            reason="operator requested re-execution after confirming the effect did not apply",
            details=details,
            effect_state=None,
            effect_key=None,
            run_after=utcnow(),
            **common,
        )
    status = conn.execute(sa.select(jobs.c.status).where(jobs.c.id == job_id)).scalar_one()
    return {"job_id": job_id, "status": status, "resolution": resolution}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m backend.execution")
    p.add_argument("command", choices=["stats", "stuck", "reconcile"])
    p.add_argument("--project")
    a = p.parse_args(argv)
    with transaction() as conn:
        if a.command == "stats":
            out: Any = stats(conn, a.project)
        elif a.command == "stuck":
            out = stuck_jobs(conn, a.project)
        else:
            out = reconcile(conn)
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
