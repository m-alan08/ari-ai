"""Phase 4 owned-test control API.

Authorization is a separate endpoint from Phase 2/3 analyst review. Nothing here
is a browser-callable /block, /isolate, or /rate-limit that bypasses the gate.
"""

from __future__ import annotations

import secrets
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request

from backend import audit, jobs
from backend.api.deps import ApiError, not_found, require_project, require_user
from backend.db import (
    connected_applications,
    control_channels,
    control_grants,
    emergency_stops,
    live_controls,
    transaction,
)
from backend.live_defense.controls import (
    advance,
    enqueue_execute,
    enqueue_rollback,
    gate_for,
    insert_control,
    load_channel,
    load_control,
    load_decision,
    origin_from_decision,
)
from backend.live_defense.pilot_guard import refuse_control_surface
from shared.audit import ActorType
from shared.base import utcnow
from shared.errors import ErrorCode
from shared.ids import IdPrefix, new_id
from shared.live_defense import (
    CONTROL_PATH,
    ControlAuthorizationCreate,
    ControlChannelCreate,
    ControlChannelCreated,
    ControlChannelView,
    ControlGrant,
    ControlGrantCreate,
    EmergencyStop,
    EmergencyStopCreate,
    LiveControl,
    LiveControlRollbackCreate,
    LiveControlStatus,
)
from shared.tenancy import MemberRole

router = APIRouter(tags=["live-defense-controls"])


def _channel_view(row: sa.RowMapping) -> ControlChannelView:
    return ControlChannelView.model_validate(row["doc"])


@router.post("/projects/{project_id}/control-grants", status_code=201, response_model=ControlGrant)
def create_grant(project_id: str, body: ControlGrantCreate, request: Request) -> ControlGrant:
    with transaction() as conn:
        user = require_user(conn, request)
        org_id, _ = require_project(conn, user, project_id, MemberRole.owner)
        app = (
            conn.execute(
                sa.select(connected_applications).where(
                    connected_applications.c.id == body.application_id
                )
            )
            .mappings()
            .first()
        )
        if app is None or app["project_id"] != project_id:
            raise not_found("application")
        denied = refuse_control_surface(app)
        if denied:
            raise ApiError(ErrorCode.LIVE_CONTROL_DENIED, denied, 403)
        grant = ControlGrant(
            id=new_id(IdPrefix.cgr),
            project_id=project_id,
            application_id=body.application_id,
            allowed_kinds=body.allowed_kinds,
            max_duration_s=body.max_duration_s,
            hourly_budget=body.hourly_budget,
            created_by=user.id,
            created_at=utcnow(),
            expires_at=body.expires_at,
        )
        conn.execute(
            sa.insert(control_grants).values(
                id=grant.id,
                project_id=project_id,
                application_id=grant.application_id,
                revoked_at=None,
                expires_at=grant.expires_at,
                created_by=user.id,
                doc=grant.model_dump(mode="json"),
            )
        )
        audit.record(
            conn,
            org_id=org_id,
            project_id=project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="live_defense.grant.created",
            subject_type="control_grant",
            subject_id=grant.id,
            details={
                "kinds": [k.value for k in grant.allowed_kinds],
                "application_id": grant.application_id,
            },
        )
        return grant


@router.get("/projects/{project_id}/control-grants")
def list_grants(project_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id)
        rows = (
            conn.execute(
                sa.select(control_grants)
                .where(control_grants.c.project_id == project_id)
                .order_by(control_grants.c.created_at.desc())
            )
            .mappings()
            .all()
        )
        return {
            "items": [ControlGrant.model_validate(r["doc"]).model_dump(mode="json") for r in rows],
            "next_cursor": None,
        }


@router.post("/control-grants/{grant_id}/revoke", status_code=200, response_model=ControlGrant)
def revoke_grant(grant_id: str, request: Request) -> ControlGrant:
    with transaction() as conn:
        user = require_user(conn, request)
        row = (
            conn.execute(sa.select(control_grants).where(control_grants.c.id == grant_id))
            .mappings()
            .first()
        )
        if row is None:
            raise not_found("grant")
        org_id, _ = require_project(conn, user, row["project_id"], MemberRole.owner)
        grant = ControlGrant.model_validate(row["doc"])
        grant = grant.model_copy(update={"revoked_at": utcnow()})
        conn.execute(
            sa.update(control_grants)
            .where(control_grants.c.id == grant_id)
            .values(revoked_at=grant.revoked_at, doc=grant.model_dump(mode="json"))
        )
        audit.record(
            conn,
            org_id=org_id,
            project_id=grant.project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="live_defense.grant.revoked",
            subject_type="control_grant",
            subject_id=grant.id,
            details={},
        )
        return grant


@router.post(
    "/applications/{app_id}/control-channel",
    status_code=201,
    response_model=ControlChannelCreated,
)
def create_channel(
    app_id: str, body: ControlChannelCreate, request: Request
) -> ControlChannelCreated:
    with transaction() as conn:
        user = require_user(conn, request)
        app = (
            conn.execute(
                sa.select(connected_applications).where(connected_applications.c.id == app_id)
            )
            .mappings()
            .first()
        )
        if app is None:
            raise not_found("application")
        org_id, _ = require_project(conn, user, app["project_id"], MemberRole.owner)
        denied = refuse_control_surface(app)
        if denied:
            raise ApiError(ErrorCode.LIVE_CONTROL_DENIED, denied, 403)
        existing = conn.execute(
            sa.select(control_channels.c.id).where(
                control_channels.c.application_id == app_id,
                control_channels.c.revoked_at.is_(None),
            )
        ).scalar()
        if existing:
            raise ApiError(ErrorCode.CONFLICT_STATE, "control channel already registered", 409)
        secret = "hkt_ctl_" + secrets.token_urlsafe(32)
        now = utcnow()
        view = ControlChannelView(
            id=new_id(IdPrefix.cch),
            project_id=app["project_id"],
            application_id=app_id,
            host="127.0.0.1",
            port=body.port,
            path=CONTROL_PATH,
            created_at=now,
        )
        conn.execute(
            sa.insert(control_channels).values(
                id=view.id,
                project_id=view.project_id,
                application_id=app_id,
                host="127.0.0.1",
                port=body.port,
                hmac_secret=secret,
                created_by=user.id,
                doc=view.model_dump(mode="json"),
            )
        )
        audit.record(
            conn,
            org_id=org_id,
            project_id=view.project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="live_defense.channel.created",
            subject_type="control_channel",
            subject_id=view.id,
            details={"host": "127.0.0.1", "port": body.port, "shown_once": True},
        )
        return ControlChannelCreated(**view.model_dump(), hmac_secret=secret)


@router.get("/applications/{app_id}/control-channel", response_model=ControlChannelView)
def get_channel(app_id: str, request: Request) -> ControlChannelView:
    with transaction() as conn:
        user = require_user(conn, request)
        app = (
            conn.execute(
                sa.select(connected_applications).where(connected_applications.c.id == app_id)
            )
            .mappings()
            .first()
        )
        if app is None:
            raise not_found("application")
        require_project(conn, user, app["project_id"])
        loaded = load_channel(conn, app_id)
        if loaded is None:
            raise not_found("control channel")
        return loaded[0]


@router.post(
    "/applications/{app_id}/control-authorizations",
    status_code=201,
    response_model=LiveControl,
)
def authorize_control(
    app_id: str, body: ControlAuthorizationCreate, request: Request
) -> LiveControl:
    """Separate from analyst review. Session identity only; fail closed."""
    with transaction() as conn:
        user = require_user(conn, request)
        app = (
            conn.execute(
                sa.select(connected_applications).where(connected_applications.c.id == app_id)
            )
            .mappings()
            .first()
        )
        if app is None:
            raise not_found("application")
        org_id, _ = require_project(conn, user, app["project_id"], MemberRole.owner)
        denied = refuse_control_surface(app)
        if denied:
            raise ApiError(ErrorCode.LIVE_CONTROL_DENIED, denied, 403)
        decision = load_decision(conn, body.live_policy_decision_id)
        if body.live_policy_decision_id and decision is None:
            raise not_found("live policy decision")
        if decision is not None and decision.project_id != app["project_id"]:
            raise not_found("live policy decision")
        origin = origin_from_decision(decision)
        gate, grant, _channel = gate_for(
            conn,
            session_user_id=user.id,
            claimed_actor_id=None,
            project_id=app["project_id"],
            application_id=app_id,
            kind=body.kind,
            target=body.target,
            duration_s=body.duration_s,
            max_requests=body.max_requests,
        )
        control = LiveControl(
            id=new_id(IdPrefix.lct),
            project_id=app["project_id"],
            application_id=app_id,
            live_policy_decision_id=body.live_policy_decision_id,
            grant_id=None if grant is None else grant.id,
            kind=body.kind,
            target=body.target,
            duration_s=body.duration_s,
            status=LiveControlStatus.pending_authorization,
            origin=origin,
            binding_sha256=gate.binding_sha256,
            requested_by=user.id,
            requested_at=utcnow(),
            checks=list(gate.checks),
            max_requests=body.max_requests,
            rationale=body.rationale,
            denial_reason=None if gate.allowed else gate.reason,
        )
        insert_control(conn, control)
        audit.record(
            conn,
            org_id=org_id,
            project_id=control.project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="live_defense.control.requested",
            subject_type="live_control",
            subject_id=control.id,
            details={
                "origin": origin.value,
                "kind": body.kind.value,
                "gate_allowed": gate.allowed,
                "abstain": gate.abstain,
            },
        )
        if gate.allowed:
            control = advance(conn, control, LiveControlStatus.authorized, user.id)
            enqueue_execute(conn, control)
            return control
        target = LiveControlStatus.escalated if gate.abstain else LiveControlStatus.rejected
        control = advance(conn, control, target, user.id)
        denied = control
    raise ApiError(
        ErrorCode.LIVE_CONTROL_DENIED,
        denied.denial_reason or "denied",
        403,
        details={
            "control_id": denied.id,
            "status": denied.status.value,
            "checks": [c.model_dump(mode="json") for c in denied.checks],
        },
    )


@router.get("/projects/{project_id}/live-controls")
def list_controls(project_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id)
        rows = (
            conn.execute(
                sa.select(live_controls)
                .where(live_controls.c.project_id == project_id)
                .order_by(live_controls.c.created_at.desc())
            )
            .mappings()
            .all()
        )
        items = [LiveControl.model_validate(r["doc"]).model_dump(mode="json") for r in rows]
        return {
            "items": items,
            "next_cursor": None,
            "label": "OWNED TEST ENVIRONMENT controls; adapter HTTP 200 is not effectiveness",
        }


@router.get("/live-controls/{control_id}", response_model=LiveControl)
def get_control(control_id: str, request: Request) -> LiveControl:
    with transaction() as conn:
        user = require_user(conn, request)
        try:
            control = load_control(conn, control_id)
        except LookupError as exc:
            raise not_found("live control") from exc
        require_project(conn, user, control.project_id)
        return control


@router.post("/live-controls/{control_id}/rollback", response_model=LiveControl)
def rollback_control(
    control_id: str, body: LiveControlRollbackCreate, request: Request
) -> LiveControl:
    with transaction() as conn:
        user = require_user(conn, request)
        try:
            control = load_control(conn, control_id, for_update=True)
        except LookupError as exc:
            raise not_found("live control") from exc
        org_id, _ = require_project(conn, user, control.project_id, MemberRole.owner)
        enqueue_rollback(conn, control.id, control.project_id)
        audit.record(
            conn,
            org_id=org_id,
            project_id=control.project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="live_defense.control.rollback_requested",
            subject_type="live_control",
            subject_id=control.id,
            details={"reason": body.reason},
        )
        return control


@router.get("/projects/{project_id}/emergency-stop")
def get_emergency_stop(project_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id)
        rows = (
            conn.execute(
                sa.select(emergency_stops)
                .where(emergency_stops.c.project_id == project_id)
                .order_by(emergency_stops.c.created_at.desc())
            )
            .mappings()
            .all()
        )
        return {
            "items": [EmergencyStop.model_validate(r["doc"]).model_dump(mode="json") for r in rows],
            "next_cursor": None,
            "active": any(r["active"] for r in rows),
        }


@router.post("/projects/{project_id}/emergency-stop", status_code=201, response_model=EmergencyStop)
def create_emergency_stop(
    project_id: str, body: EmergencyStopCreate, request: Request
) -> EmergencyStop:
    with transaction() as conn:
        user = require_user(conn, request)
        org_id, _ = require_project(conn, user, project_id, MemberRole.owner)
        stop = EmergencyStop(
            id=new_id(IdPrefix.est),
            project_id=project_id,
            application_id=body.application_id,
            active=True,
            requested_by=user.id,
            requested_at=utcnow(),
            verified_cleared=False,
            note=body.note,
        )
        conn.execute(
            sa.insert(emergency_stops).values(
                id=stop.id,
                project_id=project_id,
                application_id=body.application_id,
                active=True,
                requested_by=user.id,
                doc=stop.model_dump(mode="json"),
            )
        )
        jobs.enqueue(
            conn,
            "live_defense_emergency_stop",
            {"project_id": project_id, "application_id": body.application_id, "stop_id": stop.id},
            dedupe_key=f"live-emergency:{stop.id}",
            priority=1,
        )
        audit.record(
            conn,
            org_id=org_id,
            project_id=project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="live_defense.emergency_stop",
            subject_type="emergency_stop",
            subject_id=stop.id,
            details={"application_id": body.application_id, "claimed_success": False},
        )
        return stop


@router.post("/emergency-stop/{stop_id}/clear", response_model=EmergencyStop)
def clear_emergency_stop(stop_id: str, request: Request) -> EmergencyStop:
    with transaction() as conn:
        user = require_user(conn, request)
        row = (
            conn.execute(sa.select(emergency_stops).where(emergency_stops.c.id == stop_id))
            .mappings()
            .first()
        )
        if row is None:
            raise not_found("emergency stop")
        org_id, _ = require_project(conn, user, row["project_id"], MemberRole.owner)
        stop = EmergencyStop.model_validate(row["doc"])
        stop = stop.model_copy(update={"active": False, "cleared_at": utcnow()})
        conn.execute(
            sa.update(emergency_stops)
            .where(emergency_stops.c.id == stop_id)
            .values(active=False, doc=stop.model_dump(mode="json"))
        )
        audit.record(
            conn,
            org_id=org_id,
            project_id=stop.project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="live_defense.emergency_stop.cleared",
            subject_type="emergency_stop",
            subject_id=stop.id,
            details={},
        )
        return stop
