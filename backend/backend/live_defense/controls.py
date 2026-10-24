"""Persist and advance LiveControl rows. Gate is the only path to authorized."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import audit, jobs
from backend.db import (
    connected_applications,
    control_channels,
    control_grants,
    emergency_stops,
    live_controls,
    live_policy_decisions,
)
from backend.live_defense.adapters import AdapterDenied, call, registered_control_url
from backend.live_defense.authorize import (
    AuthorizationInput,
    ChannelView,
    GateDecision,
    GrantView,
    check,
)
from backend.live_defense.state import IllegalControlTransition, transition
from backend.live_defense.verify import verify_control, verify_removed
from backend.services import project_org
from shared.audit import ActorType
from shared.base import utcnow
from shared.live_defense import (
    ControlChannelView,
    ControlGrant,
    ControlTarget,
    LiveControl,
    LiveControlKind,
    LiveControlOrigin,
    LiveControlStatus,
    LivePolicyDecision,
)

UNRESOLVED_STATUSES = (LiveControlStatus.failed.value,)
COUNTED_STATUSES = (
    LiveControlStatus.authorized.value,
    LiveControlStatus.executing.value,
    LiveControlStatus.verifying.value,
    LiveControlStatus.verified.value,
)


def _aware(value: Any) -> Any:
    return value


def persist(conn: Connection, control: LiveControl) -> None:
    conn.execute(
        sa.update(live_controls)
        .where(live_controls.c.id == control.id)
        .values(
            status=control.status.value,
            unresolved=control.unresolved,
            expires_at=control.expires_at,
            doc=control.model_dump(mode="json"),
        )
    )


def load_control(conn: Connection, control_id: str, *, for_update: bool = False) -> LiveControl:
    stmt = sa.select(live_controls).where(live_controls.c.id == control_id)
    if for_update:
        stmt = stmt.with_for_update()
    row = conn.execute(stmt).mappings().first()
    if row is None:
        raise LookupError(control_id)
    return LiveControl.model_validate(row["doc"])


def load_grant(
    conn: Connection, application_id: str, *, for_update: bool = False
) -> ControlGrant | None:
    stmt = (
        sa.select(control_grants)
        .where(
            control_grants.c.application_id == application_id,
            control_grants.c.revoked_at.is_(None),
        )
        .order_by(control_grants.c.created_at.desc())
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = conn.execute(stmt).mappings().first()
    return ControlGrant.model_validate(row["doc"]) if row else None


def load_channel(conn: Connection, application_id: str) -> tuple[ControlChannelView, str] | None:
    row = (
        conn.execute(
            sa.select(control_channels).where(
                control_channels.c.application_id == application_id,
                control_channels.c.revoked_at.is_(None),
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    view = ControlChannelView.model_validate(row["doc"])
    return view, row["hmac_secret"]


def emergency_active(conn: Connection, project_id: str, application_id: str | None) -> bool:
    rows = (
        conn.execute(
            sa.select(emergency_stops).where(
                emergency_stops.c.project_id == project_id,
                emergency_stops.c.active.is_(True),
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        if row["application_id"] is None or row["application_id"] == application_id:
            return True
    return False


def unresolved(conn: Connection, application_id: str) -> bool:
    found = conn.execute(
        sa.select(sa.func.count())
        .select_from(live_controls)
        .where(
            live_controls.c.application_id == application_id,
            live_controls.c.unresolved.is_(True),
        )
    ).scalar()
    return bool(found)


def actions_last_hour(conn: Connection, grant_id: str, application_id: str) -> int:
    since = utcnow() - timedelta(hours=1)
    count = conn.execute(
        sa.select(sa.func.count())
        .select_from(live_controls)
        .where(
            live_controls.c.application_id == application_id,
            live_controls.c.status.in_(COUNTED_STATUSES),
            live_controls.c.created_at >= since,
        )
    ).scalar()
    return int(count or 0)


def origin_from_decision(decision: LivePolicyDecision | None) -> LiveControlOrigin:
    if decision is None:
        return LiveControlOrigin.analyst
    if decision.producer.value == "rules":
        return LiveControlOrigin.rules
    if decision.producer.value == "v6_shadow":
        return LiveControlOrigin.v6_shadow
    return LiveControlOrigin.analyst


def gate_for(
    conn: Connection,
    *,
    session_user_id: str,
    claimed_actor_id: str | None,
    project_id: str,
    application_id: str,
    kind: LiveControlKind,
    target: ControlTarget,
    duration_s: int,
    max_requests: int | None,
) -> tuple[GateDecision, ControlGrant | None, tuple[ControlChannelView, str] | None]:
    app = (
        conn.execute(
            sa.select(connected_applications)
            .where(connected_applications.c.id == application_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    disconnected = False
    if app is not None:
        disconnected = (app["doc"] or {}).get("disconnected_at") is not None
    grant = load_grant(conn, application_id, for_update=True)
    channel = load_channel(conn, application_id)
    inp = AuthorizationInput(
        session_user_id=session_user_id,
        claimed_actor_id=claimed_actor_id,
        project_id=project_id,
        application_id=application_id,
        application_exists=app is not None and app["project_id"] == project_id,
        application_disconnected=disconnected,
        application_environment=None if app is None else app["environment"],
        kind=kind.value,
        target_type=target.type.value,
        target_value=target.value,
        duration_s=duration_s,
        max_requests=max_requests,
        grant=None
        if grant is None
        else GrantView(
            id=grant.id,
            application_id=grant.application_id,
            allowed_kinds=tuple(k.value for k in grant.allowed_kinds),
            max_duration_s=grant.max_duration_s,
            hourly_budget=grant.hourly_budget,
            revoked_at=grant.revoked_at,
            expires_at=grant.expires_at,
        ),
        channel=None
        if channel is None
        else ChannelView(
            host=channel[0].host,
            port=channel[0].port,
            path=channel[0].path,
            revoked_at=channel[0].revoked_at,
        ),
        emergency_stop_active=emergency_active(conn, project_id, application_id),
        unresolved_control=unresolved(conn, application_id),
        actions_last_hour=actions_last_hour(conn, grant.id if grant else "", application_id),
        now=utcnow(),
    )
    return check(inp), grant, channel


def insert_control(conn: Connection, control: LiveControl) -> None:
    conn.execute(
        sa.insert(live_controls).values(
            id=control.id,
            project_id=control.project_id,
            application_id=control.application_id,
            live_policy_decision_id=control.live_policy_decision_id,
            kind=control.kind.value,
            status=control.status.value,
            unresolved=control.unresolved,
            expires_at=control.expires_at,
            binding_sha256=control.binding_sha256,
            doc=control.model_dump(mode="json"),
        )
    )


def enqueue_execute(conn: Connection, control: LiveControl) -> None:
    jobs.enqueue(
        conn,
        "live_defense_execute",
        {"control_id": control.id, "project_id": control.project_id},
        dedupe_key=f"live-control-exec:{control.id}",
        priority=20,
    )


def enqueue_verify(conn: Connection, control_id: str, project_id: str) -> None:
    jobs.enqueue(
        conn,
        "live_defense_verify",
        {"control_id": control_id, "project_id": project_id},
        dedupe_key=f"live-control-verify:{control_id}",
        priority=20,
    )


def enqueue_expire(conn: Connection, control: LiveControl) -> None:
    if control.expires_at is None or control.kind is LiveControlKind.escalate:
        return
    delay = max(1.0, (control.expires_at - utcnow()).total_seconds())
    jobs.enqueue(
        conn,
        "live_defense_expire",
        {"control_id": control.id, "project_id": control.project_id},
        dedupe_key=f"live-control-expire:{control.id}",
        delay_s=delay,
        priority=40,
    )


def enqueue_rollback(conn: Connection, control_id: str, project_id: str) -> None:
    jobs.enqueue(
        conn,
        "live_defense_rollback",
        {"control_id": control_id, "project_id": project_id},
        dedupe_key=f"live-control-rollback:{control_id}",
        priority=10,
    )


def _audit(
    conn: Connection,
    control: LiveControl,
    action: str,
    actor: str,
    extra: dict[str, Any] | None = None,
) -> None:
    audit.record(
        conn,
        org_id=project_org(conn, control.project_id),
        project_id=control.project_id,
        actor_type=ActorType.system if actor.startswith("live_defense") else ActorType.user,
        actor_id=actor,
        action=action,
        subject_type="live_control",
        subject_id=control.id,
        details={
            "status": control.status.value,
            "kind": control.kind.value,
            "unresolved": control.unresolved,
            **(extra or {}),
        },
    )


def advance(
    conn: Connection, control: LiveControl, target: LiveControlStatus, actor: str
) -> LiveControl:
    nxt = transition(control.status, target)
    data = control.model_dump()
    data["status"] = nxt
    now = utcnow()
    if nxt is LiveControlStatus.authorized:
        data["authorized_at"] = now
        data["expires_at"] = now + timedelta(seconds=control.duration_s)
    if nxt is LiveControlStatus.executing:
        data["executing_at"] = now
    if nxt is LiveControlStatus.verified:
        data["verified_at"] = now
        data["unresolved"] = False
    if nxt in (
        LiveControlStatus.failed,
        LiveControlStatus.expired,
        LiveControlStatus.rolled_back,
        LiveControlStatus.escalated,
        LiveControlStatus.rejected,
        LiveControlStatus.emergency_stopped,
    ):
        data["ended_at"] = now
    if nxt is LiveControlStatus.failed:
        data["unresolved"] = True
    if nxt in (LiveControlStatus.rolled_back, LiveControlStatus.emergency_stopped):
        # Success of rollback/stop is recorded only after verify; caller may keep unresolved.
        pass
    updated = LiveControl.model_validate(data)
    persist(conn, updated)
    _audit(conn, updated, f"live_defense.control.{nxt.value}", actor)
    return updated


def execute_control(
    conn: Connection,
    control_id: str,
    effect_guard: Callable[[str], AbstractContextManager[None]] | None = None,
) -> dict[str, Any]:
    """``effect_guard`` (``JobContext.effect`` from the worker) durably records the adapter
    call before it is made, so a crash mid-call is never replayed automatically."""
    control = load_control(conn, control_id, for_update=True)
    if control.status is LiveControlStatus.escalated and control.kind is LiveControlKind.escalate:
        return {"control_id": control.id, "status": control.status.value, "adapter": False}
    if control.status is not LiveControlStatus.authorized:
        return {"control_id": control.id, "status": control.status.value, "skipped": True}
    decision, _grant, channel = gate_for(
        conn,
        session_user_id=control.requested_by,
        claimed_actor_id=None,
        project_id=control.project_id,
        application_id=control.application_id,
        kind=control.kind,
        target=control.target,
        duration_s=control.duration_s,
        max_requests=control.max_requests,
    )
    if not decision.allowed:
        control = control.model_copy(
            update={
                "checks": list(decision.checks),
                "denial_reason": decision.reason,
                "unresolved": False,
            }
        )
        persist(conn, control)
        control = advance(conn, control, LiveControlStatus.rejected, "live_defense.execute")
        return {"control_id": control.id, "status": control.status.value, "denied": True}
    if control.kind is LiveControlKind.escalate:
        from shared.live_defense import LiveControlVerification

        control = advance(conn, control, LiveControlStatus.executing, "live_defense.execute")
        control = advance(conn, control, LiveControlStatus.verifying, "live_defense.execute")
        control = control.model_copy(
            update={
                "verification": LiveControlVerification(
                    observed_effect=True,
                    adapter_http_status=None,
                    probe_http_statuses=[],
                    method="recorded_escalation",
                    detail="Human escalation recorded; no adapter call.",
                    verified_at=utcnow(),
                )
            }
        )
        persist(conn, control)
        control = advance(conn, control, LiveControlStatus.verified, "live_defense.execute")
        return {"control_id": control.id, "status": control.status.value, "adapter": False}

    if channel is None:
        control = advance(conn, control, LiveControlStatus.failed, "live_defense.execute")
        return {"control_id": control.id, "status": "failed", "error": "no channel"}
    view, secret = channel
    try:
        url = registered_control_url(view.host, view.port)
    except AdapterDenied as exc:
        control = control.model_copy(update={"denial_reason": str(exc), "unresolved": True})
        persist(conn, control)
        control = advance(conn, control, LiveControlStatus.failed, "live_defense.execute")
        return {"control_id": control.id, "status": "failed", "error": str(exc)}
    control = advance(conn, control, LiveControlStatus.executing, "live_defense.execute")
    expires_unix = (control.expires_at or utcnow()).timestamp()
    guard = effect_guard or (lambda _key: nullcontext())
    try:
        with guard(f"control:{control.id}:apply"):
            result = call(
                url,
                secret,
                {
                    "op": "apply",
                    "control_id": control.id,
                    "kind": control.kind.value,
                    "target_type": control.target.type.value,
                    "target_value": control.target.value,
                    "max_requests": control.max_requests,
                    "log_level": "debug",
                    "expires_at_unix": expires_unix,
                },
            )
    except AdapterDenied as exc:
        _audit(
            conn,
            control,
            "live_defense.control.adapter_denied",
            "live_defense.execute",
            extra={"error": str(exc)},
        )
        control = control.model_copy(update={"denial_reason": str(exc), "unresolved": True})
        persist(conn, control)
        control = advance(conn, control, LiveControlStatus.failed, "live_defense.execute")
        return {
            "control_id": control.id,
            "status": control.status.value,
            "error": str(exc),
        }
    if not result.ok:
        conflict = result.http_status == 409 or bool(result.body.get("conflict"))
        _audit(
            conn,
            control,
            "live_defense.control.adapter_conflict"
            if conflict
            else "live_defense.control.adapter_failed",
            "live_defense.execute",
            extra={
                "adapter_http_status": result.http_status,
                "error": result.error or result.body.get("error"),
                "conflict": conflict,
            },
        )
        control = control.model_copy(
            update={
                "denial_reason": str(result.body.get("error") or result.error or "adapter failed"),
                "unresolved": True,
            }
        )
        persist(conn, control)
        control = advance(conn, control, LiveControlStatus.failed, "live_defense.execute")
        # A binding conflict means the fixture still holds the original control.
        # Do not roll it back from this failed retry.
        if not conflict:
            enqueue_rollback(conn, control.id, control.project_id)
        return {
            "control_id": control.id,
            "status": control.status.value,
            "adapter_http_status": result.http_status,
            "adapter_ok": False,
            "conflict": conflict,
        }
    control = control.model_copy(update={"verification": None, "denial_reason": result.error})
    persist(conn, control)
    control = advance(conn, control, LiveControlStatus.verifying, "live_defense.execute")
    enqueue_verify(conn, control.id, control.project_id)
    enqueue_expire(conn, control)
    return {
        "control_id": control.id,
        "status": control.status.value,
        "adapter_http_status": result.http_status,
        "adapter_ok": result.ok,
    }


def verify_job(conn: Connection, control_id: str) -> dict[str, Any]:
    control = load_control(conn, control_id, for_update=True)
    if control.status is not LiveControlStatus.verifying:
        return {"control_id": control.id, "status": control.status.value, "skipped": True}
    if control.kind is LiveControlKind.escalate:
        control = advance(conn, control, LiveControlStatus.verified, "live_defense.verify")
        return {"control_id": control.id, "status": control.status.value}
    channel = load_channel(conn, control.application_id)
    if channel is None:
        control = advance(conn, control, LiveControlStatus.failed, "live_defense.verify")
        enqueue_rollback(conn, control.id, control.project_id)
        return {"control_id": control.id, "status": "failed"}
    view, secret = channel
    url = registered_control_url(view.host, view.port)
    apply_status = None
    from backend.live_defense.adapters import AdapterResult

    verification = verify_control(
        kind=control.kind,
        control_url=url,
        secret=secret,
        control_id=control.id,
        target_value=control.target.value,
        adapter_apply=AdapterResult(ok=True, http_status=apply_status or 200, body={}),
    )
    control = control.model_copy(update={"verification": verification})
    persist(conn, control)
    if verification.observed_effect:
        control = advance(conn, control, LiveControlStatus.verified, "live_defense.verify")
        return {"control_id": control.id, "status": control.status.value, "observed": True}
    control = advance(conn, control, LiveControlStatus.failed, "live_defense.verify")
    enqueue_rollback(conn, control.id, control.project_id)
    return {"control_id": control.id, "status": control.status.value, "observed": False}


def rollback_job(conn: Connection, control_id: str, *, emergency: bool = False) -> dict[str, Any]:
    control = load_control(conn, control_id, for_update=True)
    channel = load_channel(conn, control.application_id)
    observed = True
    if channel is not None and control.kind is not LiveControlKind.escalate:
        view, secret = channel
        url = registered_control_url(view.host, view.port)
        result = call(url, secret, {"op": "remove", "control_id": control.id})
        verification = verify_removed(
            control_url=url,
            secret=secret,
            control_id=control.id,
            kind=control.kind,
            target_value=control.target.value,
        )
        control = control.model_copy(update={"verification": verification})
        persist(conn, control)
        observed = verification.observed_effect and result.ok
    target = LiveControlStatus.emergency_stopped if emergency else LiveControlStatus.rolled_back
    if control.status is LiveControlStatus.verified or control.status is LiveControlStatus.failed:
        try:
            control = advance(conn, control, target, "live_defense.rollback")
        except IllegalControlTransition:
            pass
    elif emergency and control.status not in (
        LiveControlStatus.emergency_stopped,
        LiveControlStatus.rejected,
        LiveControlStatus.escalated,
    ):
        try:
            control = advance(
                conn, control, LiveControlStatus.emergency_stopped, "live_defense.rollback"
            )
        except IllegalControlTransition:
            data = control.model_dump()
            data["status"] = LiveControlStatus.emergency_stopped
            data["ended_at"] = utcnow()
            control = LiveControl.model_validate(data)
            persist(conn, control)
    if observed:
        control = control.model_copy(update={"unresolved": False})
        persist(conn, control)
    else:
        control = control.model_copy(update={"unresolved": True})
        persist(conn, control)
        if not emergency:
            try:
                control = advance(
                    conn, control, LiveControlStatus.escalated, "live_defense.rollback"
                )
            except IllegalControlTransition:
                pass
    return {
        "control_id": control.id,
        "status": control.status.value,
        "verified_removed": observed,
        "claimed_success": observed,
    }


def expire_job(conn: Connection, control_id: str) -> dict[str, Any]:
    control = load_control(conn, control_id, for_update=True)
    if control.status is not LiveControlStatus.verified:
        return {"control_id": control.id, "status": control.status.value, "skipped": True}
    channel = load_channel(conn, control.application_id)
    observed = True
    if channel is not None and control.kind is not LiveControlKind.escalate:
        view, secret = channel
        url = registered_control_url(view.host, view.port)
        call(url, secret, {"op": "expire", "control_id": control.id})
        verification = verify_removed(
            control_url=url,
            secret=secret,
            control_id=control.id,
            kind=control.kind,
            target_value=control.target.value,
        )
        observed = verification.observed_effect
        control = control.model_copy(update={"verification": verification})
        persist(conn, control)
    control = advance(conn, control, LiveControlStatus.expired, "live_defense.expire")
    if not observed:
        control = control.model_copy(update={"unresolved": True})
        persist(conn, control)
    return {"control_id": control.id, "status": control.status.value, "verified_removed": observed}


def emergency_stop_job(
    conn: Connection, project_id: str, application_id: str | None
) -> dict[str, Any]:
    stmt = sa.select(live_controls).where(
        live_controls.c.project_id == project_id,
        live_controls.c.status.in_(
            [
                LiveControlStatus.authorized.value,
                LiveControlStatus.executing.value,
                LiveControlStatus.verifying.value,
                LiveControlStatus.verified.value,
                LiveControlStatus.failed.value,
            ]
        ),
    )
    if application_id:
        stmt = stmt.where(live_controls.c.application_id == application_id)
    rows = conn.execute(stmt).mappings().all()
    results = []
    for row in rows:
        results.append(rollback_job(conn, row["id"], emergency=True))
    channel_rows = (
        conn.execute(
            sa.select(control_channels).where(
                control_channels.c.project_id == project_id,
                control_channels.c.revoked_at.is_(None),
            )
        )
        .mappings()
        .all()
    )
    for row in channel_rows:
        if application_id and row["application_id"] != application_id:
            continue
        url = registered_control_url(row["host"], row["port"])
        call(url, row["hmac_secret"], {"op": "emergency_clear", "control_id": "est_all"})
    claimed = all(item.get("verified_removed") for item in results) if results else True
    return {"removed": results, "claimed_success": claimed}


def load_decision(conn: Connection, decision_id: str | None) -> LivePolicyDecision | None:
    if not decision_id:
        return None
    row = (
        conn.execute(
            sa.select(live_policy_decisions).where(live_policy_decisions.c.id == decision_id)
        )
        .mappings()
        .first()
    )
    return LivePolicyDecision.model_validate(row["doc"]) if row else None
