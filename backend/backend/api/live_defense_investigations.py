"""First-responder observability: read-only investigation timeline, invocation
detail, evidence chain, and operational diagnostics.

Every endpoint is project-scoped and read-only. Data comes only from persisted
rows (decisions, invocations, jobs, handoffs, mappings, snapshots, monitoring
events). Missing fields are reported as unavailable — never estimated. The
``execution`` payload inside an invocation doc (Phase 7C) is surfaced verbatim
when present; its absence means no agent loop ran, not failure.

Artifact *contents* are never inlined here: consumers fetch them through the
bounded ``GET /artifacts/{id}`` endpoint, which re-checks the SHA-256 digest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Query, Request

from backend.api.deps import not_found, require_project, require_user
from backend.api.routes import _keyset_page, _keyset_where
from backend.db import (
    analyst_handoffs,
    connected_applications,
    deploy_commit_mappings,
    first_responder_decisions,
    first_responder_invocations,
    jobs,
    monitoring_events,
    snapshots,
    transaction,
)
from backend.redaction import redact_text
from shared.live_defense import (
    FIRST_RESPONDER_JOB_KINDS,
    AgentExecutionView,
    AnalystHandoff,
    DeployCommitMapping,
    FirstResponderDecision,
    FirstResponderDiagnostics,
    FirstResponderInvocation,
    FirstResponderInvocationView,
    InvestigationJobError,
    InvestigationSnapshotView,
    InvestigationSummary,
    InvestigationTimeline,
    InvocationJobView,
    JobLatencyStats,
    WaitingJobView,
)
from shared.monitoring import MonitoringEvent

router = APIRouter(tags=["live-defense-investigations"])

SKIPPED_STATUSES = {
    "skipped_no_mapping",
    "skipped_no_snapshot",
    "skipped_mode",
    "skipped_no_scope",
    "skipped_no_approval",
    "skipped_no_patch",
    "skipped_no_prerequisite",
    "blocked",
}
COMPLETED_STATUSES = {"completed_unverified", "executed"}
ACTIVE_STATUSES = {"eligible", "queued"}

MAX_RECENT_ERRORS = 20
MAX_WAITING_JOBS = 20


def _job_view(row: sa.RowMapping, *, now: datetime) -> InvocationJobView:
    last_error = row["last_error"] or {}
    lease_expires = row["lease_expires_at"]
    stalled = (
        row["status"] == "running"
        and lease_expires is not None
        and lease_expires.replace(tzinfo=UTC) < now
    )
    finished = row["status"] in {"succeeded", "failed", "cancelled", "dead"}
    wall_ms = None
    if finished and row["created_at"] and row["updated_at"]:
        wall_ms = max(
            0,
            int(
                (
                    row["updated_at"].replace(tzinfo=UTC) - row["created_at"].replace(tzinfo=UTC)
                ).total_seconds()
                * 1000
            ),
        )
    message = last_error.get("message")
    return InvocationJobView(
        job_id=row["id"],
        kind=row["kind"],
        status=row["status"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        run_after=row["run_after"],
        heartbeat_at=row["heartbeat_at"],
        cancel_requested=bool(row["cancel_requested"]),
        error_code=last_error.get("code"),
        error_message=redact_text(str(message))[:500] if message is not None else None,
        enqueued_at=row["created_at"],
        updated_at=row["updated_at"],
        stalled=stalled,
        wall_time_ms=wall_ms,
    )


def _invocation_view(
    conn: sa.Connection, row: sa.RowMapping, *, now: datetime
) -> FirstResponderInvocationView:
    doc = dict(row["doc"])
    raw_execution = doc.pop("execution", None)
    execution: AgentExecutionView | None = None
    if isinstance(raw_execution, dict):
        try:
            execution = AgentExecutionView.model_validate(raw_execution)
        except Exception:  # forward-compatible: unknown shapes stay unavailable
            execution = None
    invocation = FirstResponderInvocation.model_validate(doc)
    job_view: InvocationJobView | None = None
    if invocation.job_id:
        job_row = (
            conn.execute(sa.select(jobs).where(jobs.c.id == invocation.job_id)).mappings().first()
        )
        if job_row is not None:
            job_view = _job_view(job_row, now=now)
    return FirstResponderInvocationView(
        invocation=invocation,
        job=job_view,
        execution=execution,
        execution_recorded=execution is not None,
    )


def _final_state(
    decision: FirstResponderDecision,
    invocations: list[FirstResponderInvocationView],
    handoff: AnalystHandoff | None,
) -> str:
    if decision.cancelled:
        return "cancelled"
    statuses = {v.invocation.status.value for v in invocations}
    if "failed" in statuses or (decision.action.value == "cancel_run"):
        return "failed"
    if statuses & ACTIVE_STATUSES:
        return "in_progress"
    if handoff is not None:
        return f"handoff_{handoff.state.value}"
    if statuses & COMPLETED_STATUSES:
        return "completed_unverified"
    if statuses and statuses <= SKIPPED_STATUSES:
        return "skipped"
    return decision.evidence_state.value


def _load_timeline(conn: sa.Connection, decision_id: str) -> InvestigationTimeline:
    decision_row = (
        conn.execute(
            sa.select(first_responder_decisions).where(
                first_responder_decisions.c.id == decision_id
            )
        )
        .mappings()
        .first()
    )
    if decision_row is None:
        raise not_found("investigation")
    decision = FirstResponderDecision.model_validate(decision_row["doc"])

    trigger_rows: list[Any] = []
    if decision.trigger_event_ids:
        trigger_rows = list(
            conn.execute(
                sa.select(monitoring_events.c.doc).where(
                    monitoring_events.c.id.in_(decision.trigger_event_ids),
                    monitoring_events.c.project_id == decision.project_id,
                )
            )
            .scalars()
            .all()
        )
    events = [MonitoringEvent.model_validate(doc) for doc in trigger_rows]

    application = None
    if decision.application_id:
        app_row = (
            conn.execute(
                sa.select(connected_applications.c.doc).where(
                    connected_applications.c.id == decision.application_id,
                    connected_applications.c.project_id == decision.project_id,
                )
            )
            .scalars()
            .first()
        )
        if app_row:
            from shared.live_defense import ConnectedApplication

            application = ConnectedApplication.model_validate(app_row)

    mapping = None
    if decision.correlation.mapping_id:
        map_row = (
            conn.execute(
                sa.select(deploy_commit_mappings.c.doc).where(
                    deploy_commit_mappings.c.id == decision.correlation.mapping_id,
                    deploy_commit_mappings.c.project_id == decision.project_id,
                )
            )
            .scalars()
            .first()
        )
        if map_row:
            mapping = DeployCommitMapping.model_validate(map_row)

    snapshot: InvestigationSnapshotView | None = None
    repo_id = mapping.repo_id if mapping else None
    commit_sha = decision.correlation.commit_sha or (mapping.commit_sha if mapping else None)
    if commit_sha or repo_id:
        snap_id = None
        if repo_id and commit_sha:
            snap_id = conn.execute(
                sa.select(snapshots.c.id).where(
                    snapshots.c.repo_id == repo_id,
                    snapshots.c.commit_sha == commit_sha,
                )
            ).scalar()
        snapshot = InvestigationSnapshotView(
            repo_id=repo_id,
            commit_sha=commit_sha,
            mapping_id=decision.correlation.mapping_id,
            snapshot_id=snap_id,
            resolved=snap_id is not None,
            note=(
                "Indexed snapshot resolved for the mapped commit"
                if snap_id
                else "No indexed snapshot at the mapped commit; snapshot resolution unavailable"
            ),
        )

    inv_rows = (
        conn.execute(
            sa.select(first_responder_invocations)
            .where(first_responder_invocations.c.decision_id == decision.id)
            .order_by(first_responder_invocations.c.created_at.asc())
        )
        .mappings()
        .all()
    )
    now = datetime.now(UTC)
    views = [_invocation_view(conn, row, now=now) for row in inv_rows]

    handoff = None
    handoff_row = (
        conn.execute(
            sa.select(analyst_handoffs.c.doc).where(
                analyst_handoffs.c.decision_id == decision.id,
                analyst_handoffs.c.project_id == decision.project_id,
            )
        )
        .scalars()
        .first()
    )
    if handoff_row:
        handoff = AnalystHandoff.model_validate(handoff_row)

    return InvestigationTimeline(
        decision=decision,
        trigger_events=events,
        application=application,
        mapping=mapping,
        snapshot=snapshot,
        invocations=views,
        handoff=handoff,
        final_state=_final_state(decision, views, handoff),
    )


@router.get("/projects/{project_id}/first-responder/investigations")
def list_investigations(
    project_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> dict[str, Any]:
    """Newest first. `next_cursor` pages older investigations (bounded)."""
    t = first_responder_decisions
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id)
        rows = conn.execute(
            sa.select(t.c.created_at, t.c.id, t.c.doc)
            .where(t.c.project_id == project_id, *_keyset_where(t, cursor))
            .order_by(t.c.created_at.desc(), t.c.id.desc())
            .limit(limit + 1)
        ).all()
        docs, nxt = _keyset_page(list(rows), limit)
        decision_ids = [doc["id"] for doc in docs]

        inv_counts: dict[str, dict[str, int]] = {}
        if decision_ids:
            for row in conn.execute(
                sa.select(
                    first_responder_invocations.c.decision_id,
                    first_responder_invocations.c.status,
                    sa.func.count(),
                )
                .where(first_responder_invocations.c.decision_id.in_(decision_ids))
                .group_by(
                    first_responder_invocations.c.decision_id,
                    first_responder_invocations.c.status,
                )
            ):
                inv_counts.setdefault(row[0], {})[row[1]] = row[2]

        handoff_states: dict[str, str] = {}
        if decision_ids:
            for row in conn.execute(
                sa.select(analyst_handoffs.c.decision_id, analyst_handoffs.c.state).where(
                    analyst_handoffs.c.decision_id.in_(decision_ids),
                    analyst_handoffs.c.project_id == project_id,
                )
            ):
                handoff_states[row[0]] = row[1]

        items = []
        for doc in docs:
            decision = FirstResponderDecision.model_validate(doc)
            counts = inv_counts.get(decision.id, {})
            total = sum(counts.values())
            items.append(
                InvestigationSummary(
                    decision_id=decision.id,
                    created_at=decision.created_at,
                    action=decision.action.value,
                    evidence_state=decision.evidence_state.value,
                    confirmation=decision.confirmation.value,
                    application_id=decision.application_id,
                    simulated=decision.simulated,
                    cancelled=decision.cancelled,
                    trigger_event_count=len(decision.trigger_event_ids),
                    invocations_total=total,
                    invocations_queued_or_running=sum(counts.get(s, 0) for s in ACTIVE_STATUSES),
                    invocations_completed=sum(counts.get(s, 0) for s in COMPLETED_STATUSES),
                    invocations_failed=counts.get("failed", 0),
                    invocations_skipped=sum(counts.get(s, 0) for s in SKIPPED_STATUSES),
                    invocations_cancelled=counts.get("cancelled", 0),
                    handoff_state=handoff_states.get(decision.id),
                ).model_dump(mode="json")
            )
        return {"items": items, "next_cursor": nxt}


@router.get("/first-responder/investigations/{decision_id}")
def investigation_timeline(decision_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        require_user(conn, request)
        row = conn.execute(
            sa.select(first_responder_decisions.c.project_id).where(
                first_responder_decisions.c.id == decision_id
            )
        ).scalar()
        if row is None:
            raise not_found("investigation")
        user = require_user(conn, request)
        require_project(conn, user, row)
        return _load_timeline(conn, decision_id).model_dump(mode="json")


@router.get("/first-responder/invocations/{invocation_id}")
def invocation_detail(invocation_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        row = (
            conn.execute(
                sa.select(first_responder_invocations).where(
                    first_responder_invocations.c.id == invocation_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise not_found("invocation")
        require_project(conn, require_user(conn, request), row["project_id"])
        view = _invocation_view(conn, row, now=datetime.now(UTC))
        return view.model_dump(mode="json")


@router.get("/projects/{project_id}/first-responder/diagnostics")
def diagnostics(project_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id)
        now = datetime.now(UTC)

        investigations_total = conn.execute(
            sa.select(sa.func.count())
            .select_from(first_responder_decisions)
            .where(first_responder_decisions.c.project_id == project_id)
        ).scalar_one()

        decision_docs = (
            conn.execute(
                sa.select(first_responder_decisions.c.doc).where(
                    first_responder_decisions.c.project_id == project_id
                )
            )
            .scalars()
            .all()
        )
        decisions_cancelled = sum(1 for d in decision_docs if d.get("cancelled"))

        status_rows = conn.execute(
            sa.select(first_responder_invocations.c.status, sa.func.count())
            .where(first_responder_invocations.c.project_id == project_id)
            .group_by(first_responder_invocations.c.status)
        ).all()
        invocations_by_status = {row[0]: row[1] for row in status_rows}

        failure_rows = conn.execute(
            sa.select(first_responder_invocations.c.agent, sa.func.count())
            .where(
                first_responder_invocations.c.project_id == project_id,
                first_responder_invocations.c.status == "failed",
            )
            .group_by(first_responder_invocations.c.agent)
        ).all()
        failures_by_agent = {row[0]: row[1] for row in failure_rows}

        missing_prerequisites = {
            status: count
            for status, count in invocations_by_status.items()
            if status.startswith("skipped_")
        }

        fr_kinds = list(FIRST_RESPONDER_JOB_KINDS)
        job_status_rows = conn.execute(
            sa.select(jobs.c.status, sa.func.count())
            .select_from(jobs)
            .where(
                jobs.c.kind.in_(fr_kinds),
                jobs.c.payload["project_id"].astext == project_id,
            )
            .group_by(jobs.c.status)
        ).all()
        job_counts = {row[0]: row[1] for row in job_status_rows}

        stalled = conn.execute(
            sa.select(sa.func.count())
            .select_from(jobs)
            .where(
                jobs.c.kind.in_(fr_kinds),
                jobs.c.payload["project_id"].astext == project_id,
                jobs.c.status == "running",
                jobs.c.lease_expires_at < now,
            )
        ).scalar_one()

        waiting_rows = (
            conn.execute(
                sa.select(
                    jobs.c.id, jobs.c.kind, jobs.c.run_after, jobs.c.created_at, jobs.c.attempts
                )
                .where(
                    jobs.c.kind.in_(fr_kinds),
                    jobs.c.payload["project_id"].astext == project_id,
                    jobs.c.status == "queued",
                )
                .order_by(jobs.c.run_after.asc())
                .limit(MAX_WAITING_JOBS)
            )
            .mappings()
            .all()
        )
        waiting = [
            WaitingJobView(
                job_id=r["id"],
                kind=r["kind"],
                run_after=r["run_after"],
                enqueued_at=r["created_at"],
                attempts=r["attempts"],
            )
            for r in waiting_rows
        ]

        error_rows = (
            conn.execute(
                sa.select(
                    jobs.c.id,
                    jobs.c.kind,
                    jobs.c.payload,
                    jobs.c.last_error,
                    jobs.c.updated_at,
                )
                .where(
                    jobs.c.kind.in_(fr_kinds),
                    jobs.c.payload["project_id"].astext == project_id,
                    jobs.c.last_error.is_not(None),
                )
                .order_by(jobs.c.updated_at.desc())
                .limit(MAX_RECENT_ERRORS)
            )
            .mappings()
            .all()
        )
        recent_errors = []
        for r in error_rows:
            err = r["last_error"] or {}
            payload = r["payload"] or {}
            message = err.get("message")
            recent_errors.append(
                InvestigationJobError(
                    job_id=r["id"],
                    kind=r["kind"],
                    decision_id=payload.get("decision_id"),
                    invocation_id=payload.get("invocation_id"),
                    agent=payload.get("agent"),
                    code=err.get("code"),
                    message=redact_text(str(message))[:500] if message is not None else None,
                    at=r["updated_at"],
                )
            )

        latency_rows = conn.execute(
            sa.select(jobs.c.created_at, jobs.c.updated_at).where(
                jobs.c.kind == "live_defense_agent_step",
                jobs.c.payload["project_id"].astext == project_id,
                jobs.c.status.in_(["succeeded", "failed", "cancelled", "dead"]),
            )
        ).all()
        durations = [
            max(
                0,
                int((r[1].replace(tzinfo=UTC) - r[0].replace(tzinfo=UTC)).total_seconds() * 1000),
            )
            for r in latency_rows
            if r[0] and r[1]
        ]
        latency = None
        if durations:
            latency = JobLatencyStats(
                samples=len(durations),
                avg_ms=sum(durations) // len(durations),
                max_ms=max(durations),
            )

        cost_total: float | None = None
        cost_seen = False
        for doc in conn.execute(
            sa.select(first_responder_invocations.c.doc).where(
                first_responder_invocations.c.project_id == project_id
            )
        ).scalars():
            execution = (doc or {}).get("execution")
            if isinstance(execution, dict) and execution.get("cost_usd") is not None:
                cost_seen = True
                cost_total = (cost_total or 0.0) + float(execution["cost_usd"])

        notes = [
            "Stalled = running job whose lease expired (reclaimable by another worker).",
            "Latency is job wall time (queue + run), not model latency.",
        ]
        if not cost_seen:
            notes.append(
                "No persisted execution records: cost, model, tokens and transcript "
                "references are unavailable until Phase 7C lands."
            )

        return FirstResponderDiagnostics(
            generated_at=now,
            investigations_total=investigations_total,
            decisions_cancelled=decisions_cancelled,
            invocations_by_status=invocations_by_status,
            failures_by_agent=failures_by_agent,
            missing_prerequisites=missing_prerequisites,
            jobs_queued=job_counts.get("queued", 0),
            jobs_running=job_counts.get("running", 0),
            jobs_stalled=stalled,
            jobs_succeeded=job_counts.get("succeeded", 0),
            jobs_failed=job_counts.get("failed", 0),
            jobs_cancelled=job_counts.get("cancelled", 0),
            jobs_dead=job_counts.get("dead", 0),
            waiting_jobs=waiting,
            recent_errors=recent_errors,
            agent_step_latency=latency,
            cost_usd_total=cost_total,
        ).model_dump(mode="json")
