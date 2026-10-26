"""Server-enforced first-responder modes. Fail closed. No AUTO-EXECUTE."""

from __future__ import annotations

from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import jobs as jobq
from backend.db import (
    control_grants,
    emergency_stops,
    first_responder_modes,
    jobs,
    live_controls,
)
from backend.live_defense.quarantine import secret_like_reason
from backend.services import active_scope_for_repo, project_settings
from shared.base import utcnow
from shared.live_defense import (
    CONTROL_JOB_KINDS,
    FIRST_RESPONDER_JOB_KINDS,
    ApplicationVerification,
    ConnectedApplication,
    FirstResponderEffectivePermissions,
    FirstResponderIntent,
    FirstResponderModeView,
    StopKind,
)
from shared.tenancy import ProjectSettings

# Distinct from other advisory locks. Serializes mode transitions with FR dispatch.
_FIRST_RESPONDER_LOCK_CLASS = 7919


def acquire_first_responder_lock(conn: Connection, project_id: str) -> None:
    """Hold for the rest of this transaction. Mode writes and FR dispatch share it."""
    conn.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:project_id, :klass))"),
        {"project_id": project_id, "klass": _FIRST_RESPONDER_LOCK_CLASS},
    )


def load_intent(conn: Connection, project_id: str) -> FirstResponderModeView:
    row = (
        conn.execute(
            sa.select(first_responder_modes).where(first_responder_modes.c.project_id == project_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        return FirstResponderModeView(project_id=project_id, intent=FirstResponderIntent.off)
    doc = row["doc"]
    return FirstResponderModeView.model_validate(doc)


def save_intent(
    conn: Connection,
    *,
    project_id: str,
    intent: FirstResponderIntent,
    actor_id: str,
    note: str | None,
) -> FirstResponderModeView:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    acquire_first_responder_lock(conn, project_id)
    previous = load_intent(conn, project_id)
    now = utcnow()
    stop = StopKind.stop_monitoring if intent is FirstResponderIntent.off else StopKind.none
    view = FirstResponderModeView(
        project_id=project_id,
        intent=intent,
        updated_by=actor_id,
        updated_at=now,
        previous_intent=previous.intent,
        stop_kind=stop,
    )
    payload = {
        "project_id": project_id,
        "intent": intent.value,
        "updated_by": actor_id,
        "updated_at": now,
        "doc": view.model_dump(mode="json"),
    }
    conn.execute(
        pg_insert(first_responder_modes)
        .values(**payload)
        .on_conflict_do_update(
            index_elements=[first_responder_modes.c.project_id],
            set_={
                "intent": payload["intent"],
                "updated_by": actor_id,
                "updated_at": now,
                "doc": payload["doc"],
            },
        )
    )
    _ = note
    return view


def _kill_switch_active(conn: Connection, project_id: str, application_id: str | None) -> bool:
    stmt = sa.select(emergency_stops.c.id).where(
        emergency_stops.c.project_id == project_id,
        emergency_stops.c.active.is_(True),
    )
    if application_id is not None:
        stmt = stmt.where(
            sa.or_(
                emergency_stops.c.application_id.is_(None),
                emergency_stops.c.application_id == application_id,
            )
        )
    return conn.execute(stmt.limit(1)).scalar() is not None


def _grant_active(conn: Connection, application_id: str | None) -> bool:
    if not application_id:
        return False
    now = utcnow()
    return (
        conn.execute(
            sa.select(control_grants.c.id).where(
                control_grants.c.application_id == application_id,
                control_grants.c.revoked_at.is_(None),
                sa.or_(control_grants.c.expires_at.is_(None), control_grants.c.expires_at > now),
            )
        ).scalar()
        is not None
    )


def effective_permissions(
    conn: Connection,
    *,
    project_id: str,
    application: ConnectedApplication | None,
    repo_id: str | None = None,
    trusted_mapping: bool = False,
) -> FirstResponderEffectivePermissions:
    intent = load_intent(conn, project_id).intent
    reasons: list[str] = []
    connected = False
    if application is None:
        reasons.append("no connected application")
    elif application.verification is ApplicationVerification.disconnected:
        reasons.append("application is disconnected")
    else:
        connected = True
    kill = _kill_switch_active(conn, project_id, application.id if application else None)
    grant = _grant_active(conn, application.id if application else None)
    scope = None
    if repo_id:
        scope = active_scope_for_repo(conn, project_id, repo_id)
    settings: ProjectSettings = project_settings(conn, project_id)
    monitoring_active = bool(settings.monitoring_policy.enabled) and connected
    if intent is FirstResponderIntent.off:
        reasons.append("intent is OFF")
        effective = FirstResponderIntent.off
    elif not connected:
        effective = FirstResponderIntent.off
    else:
        effective = intent
    if not trusted_mapping:
        reasons.append("no owner-attested deploy-to-commit mapping")
    if kill:
        reasons.append("kill-switch active (stop-enforcement, not stop-monitoring)")
    if grant:
        reasons.append("independently authorized control grant present")
    enforcement_active = bool(grant) and not kill and connected
    return FirstResponderEffectivePermissions(
        intent=intent,
        effective=effective,
        application_connected=connected,
        monitoring_active=monitoring_active,
        enforcement_active=enforcement_active,
        kill_switch_active=kill,
        authorized_scope_active=scope is not None,
        trusted_mapping=trusted_mapping,
        reasons=reasons,
    )


def cancel_first_responder_jobs(conn: Connection, project_id: str) -> int:
    """Cancel FR jobs only. Independently authorized control jobs are left alone."""
    rows = (
        conn.execute(
            sa.select(jobs.c.id, jobs.c.payload).where(
                jobs.c.kind.in_(FIRST_RESPONDER_JOB_KINDS),
                # waiting jobs must not survive OFF; recovery_required jobs keep their
                # state and effect evidence — cancel_requested blocks a retry
                # resolution — while the job itself is never resurrected.
                jobs.c.status.in_(("queued", "waiting", "running", "recovery_required")),
            )
        )
        .mappings()
        .all()
    )
    cancelled = 0
    for row in rows:
        payload = row["payload"] or {}
        if payload.get("project_id") != project_id:
            continue
        jobq.request_cancel(conn, row["id"])
        cancelled += 1
    return cancelled


def independently_authorized_control_jobs(conn: Connection, project_id: str) -> int:
    """Count in-flight control jobs that OFF must not abandon."""
    control_ids = [
        row[0]
        for row in conn.execute(
            sa.select(live_controls.c.id).where(live_controls.c.project_id == project_id)
        )
    ]
    rows = (
        conn.execute(
            sa.select(jobs.c.payload).where(
                jobs.c.kind.in_(CONTROL_JOB_KINDS),
                jobs.c.status.in_(("queued", "running")),
            )
        )
        .mappings()
        .all()
    )
    n = 0
    for row in rows:
        payload = row["payload"] or {}
        if payload.get("project_id") == project_id or payload.get("control_id") in control_ids:
            n += 1
    return n


def mapping_fresh(deployed_at: datetime | None) -> bool:
    if deployed_at is None:
        return False
    return utcnow() - deployed_at <= timedelta(hours=24)


def telemetry_untrusted(payload: dict[str, object] | str | None) -> str | None:
    if payload is None:
        return None
    if isinstance(payload, dict):
        text = " ".join(str(v) for v in payload.values())
    else:
        text = str(payload)
    return secret_like_reason(text)
