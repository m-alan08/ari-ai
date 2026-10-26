"""Live Defense shadow-pilot API (Phase 2, 3, 5, and 6.5 RL-gated readiness).

Connected staging apps, ingest-token issue/rotate/revoke, disconnect,
recorded health/topology, analyst reviews, genuine ``LivePolicyDecision``
rows, and exportable pilot reports. Nothing here applies, isolates,
rate-limits, or blocks. ``AnalystReview.accept`` is a recorded opinion.
``LivePolicyDecision.enforcement`` is always NOT_EXECUTED.
``DefenseHealth.status`` (and worker/telemetry/policy) is always ``unknown``.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Query, Request
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend import audit
from backend.api.deps import ApiError, not_found, require_project, require_user
from backend.db import (
    analyst_reviews,
    assessments,
    audit_events,
    connected_applications,
    control_channels,
    defense_recommendations,
    incidents,
    ingest_tokens,
    jobs,
    live_policy_decisions,
    monitoring_events,
    runtime_windows,
    transaction,
)
from backend.live_defense.pilot_guard import (
    leftover_inventory,
    leftovers_block_enable,
    reconcile_pending_jobs,
)
from backend.live_defense.readiness import assess
from monitoring.runtime import ANOMALY_EVENT_THRESHOLD
from shared.audit import ActorType
from shared.base import utcnow
from shared.errors import ErrorCode
from shared.ids import IdPrefix, new_id
from shared.live_defense import (
    PILOT_REPORT_CAVEATS,
    PILOT_UNSUPPORTED_CLAIMS,
    AnalystReview,
    AnalystReviewCreate,
    ApplicationVerification,
    ComponentGraph,
    ComponentHealthState,
    ComponentLink,
    ComponentNode,
    ComponentNodeKind,
    ComponentObservation,
    ComponentObservationSource,
    ConnectedApplication,
    ConnectedApplicationCreate,
    DefenseHealth,
    IngestTokenRotated,
    IngestTokenView,
    LivePolicyDecision,
    PilotEnablement,
    PilotReport,
    PilotTrailEntry,
    RlReadinessAssessment,
    StagingEnvironment,
)
from shared.monitoring import (
    AUTHENTICATED_RUNTIME_SOURCES,
    EventSource,
    EventStatus,
    EventType,
    IngestTokenCreated,
    MonitoringEvent,
)
from shared.tenancy import MemberRole

router = APIRouter(tags=["live-defense"])


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _token_view(row: sa.RowMapping) -> IngestTokenView:
    return IngestTokenView(
        id=row["id"],
        project_id=row["project_id"],
        application_id=row["application_id"],
        created_at=_aware(row["created_at"]) or utcnow(),
        revoked_at=_aware(row["revoked_at"]),
        last_used_at=_aware(row["last_used_at"]),
    )


def _application_from_rows(
    app_row: sa.RowMapping,
    tokens: list[sa.RowMapping],
    last_ingest_at: datetime | None,
    control_channel_configured: bool = False,
) -> ConnectedApplication:
    active = [t for t in tokens if t["revoked_at"] is None]
    had_any = bool(tokens)
    disconnected_at = (app_row["doc"] or {}).get("disconnected_at")
    if disconnected_at is not None:
        verification = ApplicationVerification.disconnected
        ingest_configured = False
    elif last_ingest_at is not None:
        verification = ApplicationVerification.ingest_seen
        ingest_configured = bool(active)
    elif had_any and not active:
        verification = ApplicationVerification.token_revoked
        ingest_configured = False
    else:
        verification = ApplicationVerification.unverified
        ingest_configured = bool(active)
    return ConnectedApplication(
        id=app_row["id"],
        project_id=app_row["project_id"],
        name=app_row["name"],
        environment=StagingEnvironment(app_row["environment"]),
        ingest_configured=ingest_configured,
        last_ingest_at=_aware(last_ingest_at),
        verification=verification,
        control_channel_configured=control_channel_configured,
        rl_gated_pilot=bool((app_row["doc"] or {}).get("rl_gated_pilot")),
        disconnected_at=disconnected_at,
        created_by=app_row["created_by"],
        created_at=_aware(app_row["created_at"]) or utcnow(),
    )


def _persist_application(conn: sa.Connection, app: ConnectedApplication) -> None:
    conn.execute(
        sa.update(connected_applications)
        .where(connected_applications.c.id == app.id)
        .values(doc=app.model_dump(mode="json"))
    )


def _emit_advisory(
    conn: sa.Connection,
    *,
    project_id: str,
    application_id: str,
    action: str,
    reason: str,
) -> None:
    ev = MonitoringEvent(
        id=new_id(IdPrefix.evt),
        project_id=project_id,
        source=EventSource.internal,
        type=EventType.advisory,
        occurred_at=utcnow(),
        received_at=utcnow(),
        dedupe_key=f"live-defense:{action}:{application_id}:{utcnow().isoformat()}",
        simulated=False,
        status=EventStatus.processed,
        status_reason=reason[:500],
    )
    conn.execute(
        pg_insert(monitoring_events)
        .values(
            id=ev.id,
            project_id=project_id,
            repo_id=None,
            dedupe_key=ev.dedupe_key,
            status=ev.status.value,
            doc=ev.model_dump(mode="json"),
        )
        .on_conflict_do_nothing()
    )


def _load_application(conn: sa.Connection, app_id: str) -> ConnectedApplication:
    row = (
        conn.execute(sa.select(connected_applications).where(connected_applications.c.id == app_id))
        .mappings()
        .first()
    )
    if row is None:
        raise not_found("application")
    tokens = (
        conn.execute(sa.select(ingest_tokens).where(ingest_tokens.c.application_id == app_id))
        .mappings()
        .all()
    )
    last_ingest = conn.execute(
        sa.select(sa.func.max(runtime_windows.c.window_end)).where(
            runtime_windows.c.application_id == app_id
        )
    ).scalar()
    channel = conn.execute(
        sa.select(control_channels.c.id).where(
            control_channels.c.application_id == app_id,
            control_channels.c.revoked_at.is_(None),
        )
    ).scalar()
    return _application_from_rows(row, list(tokens), last_ingest, bool(channel))


@router.post(
    "/projects/{project_id}/applications", status_code=201, response_model=ConnectedApplication
)
def create_application(
    project_id: str, body: ConnectedApplicationCreate, request: Request
) -> ConnectedApplication:
    with transaction() as conn:
        user = require_user(conn, request)
        org_id, _ = require_project(conn, user, project_id, MemberRole.owner)
        app_id = new_id(IdPrefix.app)
        now = utcnow()
        app = ConnectedApplication(
            id=app_id,
            project_id=project_id,
            name=body.name,
            environment=body.environment,
            ingest_configured=False,
            last_ingest_at=None,
            verification=ApplicationVerification.unverified,
            rl_gated_pilot=body.rl_gated_pilot,
            created_by=user.id,
            created_at=now,
        )
        conn.execute(
            sa.insert(connected_applications).values(
                id=app_id,
                project_id=project_id,
                name=body.name,
                environment=body.environment.value,
                created_by=user.id,
                doc=app.model_dump(mode="json"),
            )
        )
        audit.record(
            conn,
            org_id=org_id,
            project_id=project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="live_defense.application.created",
            subject_type="connected_application",
            subject_id=app_id,
            details={
                "environment": body.environment.value,
                "shadow_mode": True,
                "rl_gated_pilot": body.rl_gated_pilot,
            },
        )
        return app


@router.get("/projects/{project_id}/applications")
def list_applications(project_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id)
        rows = (
            conn.execute(
                sa.select(connected_applications)
                .where(connected_applications.c.project_id == project_id)
                .order_by(connected_applications.c.created_at.desc())
            )
            .mappings()
            .all()
        )
        tokens = (
            conn.execute(sa.select(ingest_tokens).where(ingest_tokens.c.project_id == project_id))
            .mappings()
            .all()
        )
        last_by_app = dict(
            conn.execute(
                sa.select(
                    runtime_windows.c.application_id, sa.func.max(runtime_windows.c.window_end)
                )
                .where(
                    runtime_windows.c.project_id == project_id,
                    runtime_windows.c.application_id.is_not(None),
                )
                .group_by(runtime_windows.c.application_id)
            ).all()
        )
        by_app: dict[str, list[sa.RowMapping]] = {}
        for token in tokens:
            if token["application_id"]:
                by_app.setdefault(token["application_id"], []).append(token)
        channel_apps = {
            row_id
            for (row_id,) in conn.execute(
                sa.select(control_channels.c.application_id).where(
                    control_channels.c.project_id == project_id,
                    control_channels.c.revoked_at.is_(None),
                )
            ).all()
        }
        items = [
            _application_from_rows(
                row,
                by_app.get(row["id"], []),
                last_by_app.get(row["id"]),
                row["id"] in channel_apps,
            ).model_dump(mode="json")
            for row in rows
        ]
        return {"items": items, "next_cursor": None}


@router.get("/applications/{app_id}", response_model=ConnectedApplication)
def get_application(app_id: str, request: Request) -> ConnectedApplication:
    with transaction() as conn:
        user = require_user(conn, request)
        app = _load_application(conn, app_id)
        require_project(conn, user, app.project_id)
        return app


@router.post(
    "/applications/{app_id}/ingest-tokens", status_code=201, response_model=IngestTokenCreated
)
def create_application_ingest_token(app_id: str, request: Request) -> IngestTokenCreated:
    with transaction() as conn:
        user = require_user(conn, request)
        app = _load_application(conn, app_id)
        org_id, _ = require_project(conn, user, app.project_id, MemberRole.owner)
        if app.disconnected_at is not None:
            raise ApiError(ErrorCode.CONFLICT_STATE, "application is disconnected", 409)
        token = "hkt_ing_" + secrets.token_urlsafe(32)
        tid = new_id(IdPrefix.itk)
        now = utcnow()
        conn.execute(
            sa.insert(ingest_tokens).values(
                id=tid,
                project_id=app.project_id,
                token_sha256=_sha(token),
                created_by=user.id,
                application_id=app_id,
            )
        )
        audit.record(
            conn,
            org_id=org_id,
            project_id=app.project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="ingest_token.created",
            subject_type="ingest_token",
            subject_id=tid,
            details={"application_id": app_id, "shown_once": True},
        )
        return IngestTokenCreated(id=tid, project_id=app.project_id, token=token, created_at=now)


@router.post(
    "/applications/{app_id}/ingest-tokens/rotate",
    status_code=201,
    response_model=IngestTokenRotated,
)
def rotate_application_ingest_token(app_id: str, request: Request) -> IngestTokenRotated:
    """Issue a new secret and revoke prior active tokens. Secret shown once."""
    with transaction() as conn:
        user = require_user(conn, request)
        app = _load_application(conn, app_id)
        org_id, _ = require_project(conn, user, app.project_id, MemberRole.owner)
        if app.disconnected_at is not None:
            raise ApiError(ErrorCode.CONFLICT_STATE, "application is disconnected", 409)
        conn.execute(  # serialize concurrent rotations of the same application
            sa.select(connected_applications.c.id)
            .where(connected_applications.c.id == app_id)
            .with_for_update()
        )
        active = (
            conn.execute(
                sa.select(ingest_tokens.c.id).where(
                    ingest_tokens.c.application_id == app_id,
                    ingest_tokens.c.revoked_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        if active:
            conn.execute(
                sa.update(ingest_tokens)
                .where(ingest_tokens.c.id.in_(list(active)))
                .values(revoked_at=sa.func.now())
            )
        token = "hkt_ing_" + secrets.token_urlsafe(32)
        tid = new_id(IdPrefix.itk)
        now = utcnow()
        conn.execute(
            sa.insert(ingest_tokens).values(
                id=tid,
                project_id=app.project_id,
                token_sha256=_sha(token),
                created_by=user.id,
                application_id=app_id,
            )
        )
        audit.record(
            conn,
            org_id=org_id,
            project_id=app.project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="ingest_token.rotated",
            subject_type="ingest_token",
            subject_id=tid,
            details={"application_id": app_id, "revoked_ids": list(active), "shown_once": True},
        )
        _emit_advisory(
            conn,
            project_id=app.project_id,
            application_id=app_id,
            action="ingest_token.rotated",
            reason=f"Ingest credential rotated for staging app {app_id}; prior tokens revoked.",
        )
        return IngestTokenRotated(
            id=tid,
            project_id=app.project_id,
            application_id=app_id,
            token=token,
            created_at=now,
            revoked_ids=list(active),
        )


@router.post("/applications/{app_id}/disconnect", response_model=ConnectedApplication)
def disconnect_application(app_id: str, request: Request) -> ConnectedApplication:
    """Revoke ingest credentials and stop accepting telemetry. Not production enforcement."""
    with transaction() as conn:
        user = require_user(conn, request)
        conn.execute(
            sa.select(connected_applications.c.id)
            .where(connected_applications.c.id == app_id)
            .with_for_update()
        )
        app = _load_application(conn, app_id)
        org_id, _ = require_project(conn, user, app.project_id, MemberRole.owner)
        now = utcnow()
        conn.execute(
            sa.update(ingest_tokens)
            .where(
                ingest_tokens.c.application_id == app_id,
                ingest_tokens.c.revoked_at.is_(None),
            )
            .values(revoked_at=sa.func.now())
        )
        app = app.model_copy(
            update={
                "disconnected_at": now,
                "ingest_configured": False,
                "verification": ApplicationVerification.disconnected,
            }
        )
        _persist_application(conn, app)
        audit.record(
            conn,
            org_id=org_id,
            project_id=app.project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="live_defense.application.disconnected",
            subject_type="connected_application",
            subject_id=app_id,
            details={"shadow_mode": True, "enforcement": "none"},
        )
        _emit_advisory(
            conn,
            project_id=app.project_id,
            application_id=app_id,
            action="application.disconnected",
            reason=f"Staging app {app_id} disconnected; ingest tokens revoked.",
        )
        return app


@router.post("/applications/{app_id}/shadow-verify", response_model=ConnectedApplication)
def shadow_verify_application(app_id: str, request: Request) -> ConnectedApplication:
    """Recompute verification from recorded ingest. Never a healthy or executed verdict."""
    with transaction() as conn:
        user = require_user(conn, request)
        app = _load_application(conn, app_id)
        org_id, _ = require_project(conn, user, app.project_id, MemberRole.owner)
        audit.record(
            conn,
            org_id=org_id,
            project_id=app.project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="live_defense.application.verified",
            subject_type="connected_application",
            subject_id=app_id,
            details={
                "verification": app.verification.value,
                "health": "unknown",
                "executes": False,
            },
        )
        return app


@router.post(
    "/applications/{app_id}/enable-rl-gated-pilot",
    response_model=PilotEnablement,
)
def enable_rl_gated_pilot(app_id: str, request: Request) -> PilotEnablement:
    """Server-enforced shadow-only. Fail closed if leftover controls remain."""
    with transaction() as conn:
        user = require_user(conn, request)
        conn.execute(
            sa.select(connected_applications.c.id)
            .where(connected_applications.c.id == app_id)
            .with_for_update()
        )
        app = _load_application(conn, app_id)
        org_id, _ = require_project(conn, user, app.project_id, MemberRole.owner)
        leftover = leftover_inventory(conn, app_id)
        if leftovers_block_enable(leftover):
            raise ApiError(
                ErrorCode.CONFLICT_STATE,
                "Existing controls must be removed and verified before enabling.",
                409,
                details=leftover.model_dump(mode="json"),
            )
        reconciled = reconcile_pending_jobs(conn, app_id)
        leftover = leftover_inventory(conn, app_id)
        app = app.model_copy(update={"rl_gated_pilot": True})
        _persist_application(conn, app)
        audit.record(
            conn,
            org_id=org_id,
            project_id=app.project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="live_defense.application.rl_gated_pilot_enabled",
            subject_type="connected_application",
            subject_id=app_id,
            details={
                "jobs_reconciled": reconciled,
                "executes": False,
                "shadow_only": True,
            },
        )
        return PilotEnablement(
            application=app,
            enabled=True,
            leftover=leftover,
            fail_closed=False,
            jobs_reconciled=reconciled,
            reason="Shadow-only enforced. Analyst accept still does not execute.",
        )


@router.get("/projects/{project_id}/ingest-tokens")
def list_ingest_tokens(project_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id, MemberRole.owner)
        rows = (
            conn.execute(
                sa.select(ingest_tokens)
                .where(ingest_tokens.c.project_id == project_id)
                .order_by(ingest_tokens.c.created_at.desc())
            )
            .mappings()
            .all()
        )
        return {
            "items": [_token_view(row).model_dump(mode="json") for row in rows],
            "next_cursor": None,
        }


_TRAIL_PREFIXES = ("live_defense.", "ingest_token.", "runtime.")
_LIVE_DEFENSE_JOB_KINDS = (
    "live_defense_shadow",
    "live_defense_first_responder",
    "live_defense_agent_step",
    "live_defense_execute",
    "live_defense_verify",
    "live_defense_rollback",
    "live_defense_expire",
    "live_defense_emergency_stop",
)


def _project_queue_depth(conn: sa.Connection, project_id: str) -> int | None:
    """Queued jobs bound to this project. Other tenants' work is not counted.

    ``run_assessment`` jobs carry only ``assessment_id``; those rows are included
    through the assessment. Remaining kinds without either key stay uncounted
    rather than guessed.
    """
    project_assessments = sa.select(assessments.c.id).where(assessments.c.project_id == project_id)
    queued = conn.execute(
        sa.select(sa.func.count())
        .select_from(jobs)
        .where(
            jobs.c.status == "queued",
            sa.or_(
                jobs.c.payload["project_id"].astext == project_id,
                jobs.c.payload["assessment_id"].astext.in_(project_assessments),
            ),
        )
    ).scalar()
    return int(queued) if queued is not None else None


def _defense_health(conn: sa.Connection, project_id: str) -> DefenseHealth:
    worker_seen = conn.execute(sa.select(sa.func.max(jobs.c.heartbeat_at))).scalar()
    last_ingest = conn.execute(
        sa.select(sa.func.max(runtime_windows.c.window_end)).where(
            runtime_windows.c.project_id == project_id
        )
    ).scalar()
    last_event = conn.execute(
        sa.select(sa.func.max(monitoring_events.c.created_at)).where(
            monitoring_events.c.project_id == project_id,
            monitoring_events.c.doc["source"].astext == "runtime",
        )
    ).scalar()
    return DefenseHealth(
        project_id=project_id,
        worker_seen_at=_aware(worker_seen),
        queue_depth=_project_queue_depth(conn, project_id),
        last_ingest_at=_aware(last_ingest),
        last_runtime_event_at=_aware(last_event),
        worker_status="unknown",
        telemetry_status="unknown",
        policy_status="unknown",
    )


def _failed_job_trail(conn: sa.Connection, project_id: str) -> list[PilotTrailEntry]:
    """Measured worker failures for this project. Not a health verdict.

    ``jobs.complete`` leaves ``last_error`` on a later success; those rows stay
    visible as recovered failures rather than disappearing from the trail.
    """
    rows = (
        conn.execute(
            sa.select(jobs)
            .where(
                jobs.c.kind.in_(_LIVE_DEFENSE_JOB_KINDS),
                jobs.c.payload["project_id"].astext == project_id,
                jobs.c.last_error.is_not(None),
            )
            .order_by(jobs.c.updated_at.desc())
            .limit(20)
        )
        .mappings()
        .all()
    )
    trail: list[PilotTrailEntry] = []
    for row in rows:
        error = row["last_error"] or {}
        code = str(error.get("code") or "unknown")
        recovered = row["status"] == "succeeded"
        trail.append(
            PilotTrailEntry(
                at=_aware(row["updated_at"]) or utcnow(),
                action="job.recovered" if recovered else f"job.{row['status']}",
                subject_type="job",
                subject_id=row["id"],
                detail=(
                    f"{row['kind']} recovered last_error={code}"
                    if recovered
                    else f"{row['kind']} last_error={code}"
                ),
            )
        )
    return trail


@router.get("/projects/{project_id}/defense-health", response_model=DefenseHealth)
def defense_health(project_id: str, request: Request) -> DefenseHealth:
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id)
        return _defense_health(conn, project_id)


@router.get("/projects/{project_id}/rl-readiness", response_model=RlReadinessAssessment)
def rl_readiness(project_id: str, request: Request) -> RlReadinessAssessment:
    """Internal dashboard source of truth. Frontend flags cannot set ready=true."""
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id)
        gated = sum(
            1
            for row in conn.execute(
                sa.select(connected_applications.c.doc).where(
                    connected_applications.c.project_id == project_id
                )
            ).scalars()
            if row and row.get("rl_gated_pilot")
        )
        return assess(gated_application_count=gated)


@router.get("/projects/{project_id}/pilot-report", response_model=PilotReport)
def pilot_report(project_id: str, request: Request) -> PilotReport:
    """Exportable shadow-pilot snapshot. HTTP 200 is not attack success."""
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id)
        app_rows = (
            conn.execute(
                sa.select(connected_applications)
                .where(connected_applications.c.project_id == project_id)
                .order_by(connected_applications.c.created_at.desc())
            )
            .mappings()
            .all()
        )
        tokens = (
            conn.execute(sa.select(ingest_tokens).where(ingest_tokens.c.project_id == project_id))
            .mappings()
            .all()
        )
        last_by_app = dict(
            conn.execute(
                sa.select(
                    runtime_windows.c.application_id, sa.func.max(runtime_windows.c.window_end)
                )
                .where(
                    runtime_windows.c.project_id == project_id,
                    runtime_windows.c.application_id.is_not(None),
                )
                .group_by(runtime_windows.c.application_id)
            ).all()
        )
        by_app: dict[str, list[sa.RowMapping]] = {}
        for token in tokens:
            if token["application_id"]:
                by_app.setdefault(token["application_id"], []).append(token)
        apps = [
            _application_from_rows(row, by_app.get(row["id"], []), last_by_app.get(row["id"]))
            for row in app_rows
        ]
        ingest_windows = int(
            conn.execute(
                sa.select(sa.func.count())
                .select_from(runtime_windows)
                .where(runtime_windows.c.project_id == project_id)
            ).scalar()
            or 0
        )
        shadow_n = int(
            conn.execute(
                sa.select(sa.func.count())
                .select_from(live_policy_decisions)
                .where(live_policy_decisions.c.project_id == project_id)
            ).scalar()
            or 0
        )
        review_n = int(
            conn.execute(
                sa.select(sa.func.count())
                .select_from(analyst_reviews)
                .where(analyst_reviews.c.project_id == project_id)
            ).scalar()
            or 0
        )
        incident_n = int(
            conn.execute(
                sa.select(sa.func.count())
                .select_from(incidents)
                .where(incidents.c.project_id == project_id)
            ).scalar()
            or 0
        )
        health = _defense_health(conn, project_id)
        trail_rows = (
            conn.execute(
                sa.select(audit_events.c.doc)
                .where(
                    audit_events.c.project_id == project_id,
                    sa.or_(
                        *(
                            audit_events.c.doc["action"].astext.like(f"{prefix}%")
                            for prefix in _TRAIL_PREFIXES
                        )
                    ),
                )
                .order_by(audit_events.c.seq.desc())
                .limit(50)
            )
            .scalars()
            .all()
        )
        trail: list[PilotTrailEntry] = []
        for doc in trail_rows:
            action = str(doc.get("action") or "")
            trail.append(
                PilotTrailEntry(
                    at=doc["at"],
                    action=action,
                    subject_type=str(doc.get("subject_type") or ""),
                    subject_id=str(doc.get("subject_id") or ""),
                    detail=None,
                )
            )
        trail.extend(_failed_job_trail(conn, project_id))
        trail.sort(key=lambda item: item.at, reverse=True)
        return PilotReport(
            project_id=project_id,
            generated_at=utcnow(),
            applications=apps,
            ingest_windows=ingest_windows,
            shadow_decisions=shadow_n,
            reviews=review_n,
            incidents=incident_n,
            health=health,
            investigation_trail=trail,
            caveats=list(PILOT_REPORT_CAVEATS),
            unsupported_claims=list(PILOT_UNSUPPORTED_CLAIMS),
        )


@router.get("/projects/{project_id}/defense-graph", response_model=ComponentGraph)
def defense_graph(project_id: str, request: Request) -> ComponentGraph:
    """Recorded apps and ingest routes. Never marks a node healthy from HTTP 200."""
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id)
        apps = (
            conn.execute(
                sa.select(connected_applications).where(
                    connected_applications.c.project_id == project_id
                )
            )
            .mappings()
            .all()
        )
        windows = (
            conn.execute(
                sa.select(runtime_windows)
                .where(runtime_windows.c.project_id == project_id)
                .order_by(runtime_windows.c.window_end.desc())
            )
            .mappings()
            .all()
        )
    nodes: list[ComponentNode] = []
    links: list[ComponentLink] = []
    observations: list[ComponentObservation] = []
    seen_routes: set[tuple[str | None, str]] = set()
    for app in apps:
        nodes.append(
            ComponentNode(
                id=app["id"],
                label=app["name"],
                kind=ComponentNodeKind.application,
            )
        )
        observations.append(
            ComponentObservation(
                node_id=app["id"],
                state=ComponentHealthState.unknown,
                last_checked_at=None,
                source=ComponentObservationSource.none,
                simulated=False,
                note="Applications have no health-check producer; last ingest is on child routes.",
            )
        )
    for window in windows:
        key = (window["application_id"], window["route"])
        if key in seen_routes:
            continue
        seen_routes.add(key)
        node_id = f"{window['application_id'] or 'project'}:{window['route']}"
        nodes.append(
            ComponentNode(
                id=node_id,
                label=window["route"],
                kind=ComponentNodeKind.route,
            )
        )
        if window["application_id"]:
            links.append(ComponentLink(source_id=window["application_id"], target_id=node_id))
        score = window["score"] or {}
        anomaly = float(score.get("anomaly_score") or 0)
        simulated = window["source"] not in AUTHENTICATED_RUNTIME_SOURCES
        attention = anomaly >= ANOMALY_EVENT_THRESHOLD
        observations.append(
            ComponentObservation(
                node_id=node_id,
                state=ComponentHealthState.attention if attention else ComponentHealthState.unknown,
                last_checked_at=_aware(window["window_end"]),
                source=ComponentObservationSource.ingest,
                simulated=simulated,
                note=(
                    "Latest recorded runtime score was anomalous."
                    if attention
                    else "Latest recorded runtime window is not a health check; state stays unknown."
                ),
            )
        )
    return ComponentGraph(
        project_id=project_id,
        nodes=nodes,
        links=links,
        observations=observations,
        generated_at=utcnow(),
    )


def _review_from_row(row: sa.RowMapping) -> AnalystReview:
    return AnalystReview.model_validate(row["doc"])


@router.get("/projects/{project_id}/defense-reviews")
def list_project_reviews(project_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id)
        rows = (
            conn.execute(
                sa.select(analyst_reviews)
                .where(analyst_reviews.c.project_id == project_id)
                .order_by(analyst_reviews.c.created_at.desc())
            )
            .mappings()
            .all()
        )
        return {
            "items": [_review_from_row(row).model_dump(mode="json") for row in rows],
            "next_cursor": None,
        }


@router.get("/defense-recommendations/{rec_id}/reviews")
def list_recommendation_reviews(rec_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        user = require_user(conn, request)
        rec = (
            conn.execute(
                sa.select(defense_recommendations).where(defense_recommendations.c.id == rec_id)
            )
            .mappings()
            .first()
        )
        if rec is None or rec["project_id"] is None:
            raise not_found("recommendation")
        require_project(conn, user, rec["project_id"])
        rows = (
            conn.execute(
                sa.select(analyst_reviews)
                .where(analyst_reviews.c.recommendation_id == rec_id)
                .order_by(analyst_reviews.c.created_at.desc())
            )
            .mappings()
            .all()
        )
        return {
            "items": [_review_from_row(row).model_dump(mode="json") for row in rows],
            "next_cursor": None,
        }


@router.post(
    "/defense-recommendations/{rec_id}/reviews", status_code=201, response_model=AnalystReview
)
def create_recommendation_review(
    rec_id: str, body: AnalystReviewCreate, request: Request
) -> AnalystReview:
    """Record an analyst opinion. Accept does not enqueue work or call adapters."""
    with transaction() as conn:
        user = require_user(conn, request)
        rec = (
            conn.execute(
                sa.select(defense_recommendations).where(defense_recommendations.c.id == rec_id)
            )
            .mappings()
            .first()
        )
        if rec is None or rec["project_id"] is None:
            raise not_found("recommendation")
        org_id, _ = require_project(conn, user, rec["project_id"], MemberRole.member)
        now = utcnow()
        review = AnalystReview(
            id=new_id(IdPrefix.arv),
            project_id=rec["project_id"],
            recommendation_id=rec_id,
            decision=body.decision,
            reason=body.reason,
            reviewed_by=user.id,
            reviewed_at=now,
        )
        conn.execute(
            sa.insert(analyst_reviews).values(
                id=review.id,
                project_id=rec["project_id"],
                recommendation_id=rec_id,
                live_policy_decision_id=None,
                decision=body.decision.value,
                reviewed_by=user.id,
                doc=review.model_dump(mode="json"),
            )
        )
        audit.record(
            conn,
            org_id=org_id,
            project_id=rec["project_id"],
            actor_type=ActorType.user,
            actor_id=user.id,
            action="live_defense.recommendation.reviewed",
            subject_type="analyst_review",
            subject_id=review.id,
            details={
                "recommendation_id": rec_id,
                "decision": body.decision.value,
                "executes": False,
                "enforcement": "none",
            },
        )
        return review


def _keyset_where(table: sa.Table, cursor: str | None) -> list[Any]:
    if not cursor:
        return []
    ts, sep, rid = cursor.partition("|")
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        when = None
    if not sep or not rid or when is None:
        raise ApiError(ErrorCode.VALIDATION_FAILED, "invalid cursor", 422)
    return [
        sa.tuple_(table.c.created_at, table.c.id) < sa.tuple_(sa.literal(when), sa.literal(rid))
    ]


def _keyset_page(rows: list[Any], limit: int) -> tuple[list[Any], str | None]:
    items = rows[:limit]
    if len(rows) > limit and items:
        last = items[-1]
        return [r[2] for r in items], f"{last[0].isoformat()}|{last[1]}"
    return [r[2] for r in items], None


@router.get("/projects/{project_id}/live-policy-decisions")
def list_live_policy_decisions(
    project_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> dict[str, Any]:
    """Newest first. ``next_cursor`` is set when older shadow decisions exist."""
    t = live_policy_decisions
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id)
        rows = conn.execute(
            sa.select(t.c.created_at, t.c.id, t.c.doc)
            .where(t.c.project_id == project_id, *_keyset_where(t, cursor))
            .order_by(t.c.created_at.desc(), t.c.id.desc())
            .limit(limit + 1)
        ).all()
        docs, nxt = _keyset_page(rows, limit)
        return {
            "items": [LivePolicyDecision.model_validate(d).model_dump(mode="json") for d in docs],
            "next_cursor": nxt,
            "label": "shadow decisions; enforcement NOT_EXECUTED; Q-values only when v6 ran",
        }


@router.get("/live-policy-decisions/{decision_id}", response_model=LivePolicyDecision)
def get_live_policy_decision(decision_id: str, request: Request) -> LivePolicyDecision:
    with transaction() as conn:
        user = require_user(conn, request)
        row = (
            conn.execute(
                sa.select(live_policy_decisions).where(live_policy_decisions.c.id == decision_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise not_found("live policy decision")
        require_project(conn, user, row["project_id"])
        return LivePolicyDecision.model_validate(row["doc"])


@router.get("/live-policy-decisions/{decision_id}/reviews")
def list_live_policy_decision_reviews(decision_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        user = require_user(conn, request)
        row = (
            conn.execute(
                sa.select(live_policy_decisions).where(live_policy_decisions.c.id == decision_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise not_found("live policy decision")
        require_project(conn, user, row["project_id"])
        rows = (
            conn.execute(
                sa.select(analyst_reviews)
                .where(analyst_reviews.c.live_policy_decision_id == decision_id)
                .order_by(analyst_reviews.c.created_at.desc())
            )
            .mappings()
            .all()
        )
        return {
            "items": [_review_from_row(r).model_dump(mode="json") for r in rows],
            "next_cursor": None,
        }


@router.post(
    "/live-policy-decisions/{decision_id}/reviews",
    status_code=201,
    response_model=AnalystReview,
)
def create_live_policy_decision_review(
    decision_id: str, body: AnalystReviewCreate, request: Request
) -> AnalystReview:
    """Record an analyst opinion on a shadow decision. Accept does not execute."""
    with transaction() as conn:
        user = require_user(conn, request)
        row = (
            conn.execute(
                sa.select(live_policy_decisions).where(live_policy_decisions.c.id == decision_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise not_found("live policy decision")
        org_id, _ = require_project(conn, user, row["project_id"], MemberRole.member)
        now = utcnow()
        review = AnalystReview(
            id=new_id(IdPrefix.arv),
            project_id=row["project_id"],
            recommendation_id=None,
            live_policy_decision_id=decision_id,
            decision=body.decision,
            reason=body.reason,
            reviewed_by=user.id,
            reviewed_at=now,
        )
        conn.execute(
            sa.insert(analyst_reviews).values(
                id=review.id,
                project_id=row["project_id"],
                recommendation_id=None,
                live_policy_decision_id=decision_id,
                decision=body.decision.value,
                reviewed_by=user.id,
                doc=review.model_dump(mode="json"),
            )
        )
        audit.record(
            conn,
            org_id=org_id,
            project_id=row["project_id"],
            actor_type=ActorType.user,
            actor_id=user.id,
            action="live_defense.shadow_decision.reviewed",
            subject_type="analyst_review",
            subject_id=review.id,
            details={
                "live_policy_decision_id": decision_id,
                "decision": body.decision.value,
                "executes": False,
                "enforcement": "none",
                "rl_label": False,
            },
        )
        return review
