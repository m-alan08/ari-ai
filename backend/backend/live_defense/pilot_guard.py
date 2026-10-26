"""Server-enforced shadow-only for RL-gated pilot apps.

Does not expand the executable surface. Grant, channel, authorize, and leftover
execute fail closed. Workers re-check independently of the HTTP API.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend.db import (
    connected_applications,
    control_channels,
    control_grants,
    jobs,
    live_controls,
)
from shared.base import utcnow
from shared.live_defense import (
    ConnectedApplication,
    LiveControl,
    LiveControlStatus,
    PilotLeftoverInventory,
    StagingEnvironment,
)

SHADOW_ONLY_REASON = (
    "RL-gated pilot is shadow-only; control authorization, grants, channels, "
    "and execute are denied."
)

_EXECUTE_KINDS = (
    "live_defense_execute",
    "live_defense_verify",
    "live_defense_rollback",
    "live_defense_expire",
    "live_defense_emergency_stop",
)
_PENDING_JOB = ("queued", "running")
_REMOVED = {
    LiveControlStatus.expired,
    LiveControlStatus.rolled_back,
    LiveControlStatus.rejected,
    LiveControlStatus.emergency_stopped,
}


def doc_is_gated(doc: dict[str, Any] | None) -> bool:
    return bool(doc and doc.get("rl_gated_pilot"))


def application_is_gated(row: sa.RowMapping) -> bool:
    return doc_is_gated(row["doc"] if "doc" in row else None)


def control_is_leftover(control: LiveControl) -> bool:
    """Fail closed: a control still in effect blocks enablement."""
    if control.status in _REMOVED:
        return False
    if control.status is LiveControlStatus.escalated:
        return False
    now = utcnow()
    if control.status is LiveControlStatus.verified:
        if control.ended_at is not None:
            return False
        if control.expires_at is not None and control.expires_at <= now:
            return False
        return True
    return True


def leftover_inventory(conn: Connection, application_id: str) -> PilotLeftoverInventory:
    grants = list(
        conn.execute(
            sa.select(control_grants.c.id).where(
                control_grants.c.application_id == application_id,
                control_grants.c.revoked_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    channels = list(
        conn.execute(
            sa.select(control_channels.c.id).where(
                control_channels.c.application_id == application_id,
                control_channels.c.revoked_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    control_rows = (
        conn.execute(
            sa.select(live_controls).where(live_controls.c.application_id == application_id)
        )
        .mappings()
        .all()
    )
    leftover_controls: list[LiveControl] = []
    control_ids: list[str] = []
    for row in control_rows:
        control = LiveControl.model_validate(row["doc"])
        control_ids.append(control.id)
        if control_is_leftover(control):
            leftover_controls.append(control)
    pending_jobs = 0
    if control_ids:
        pending_jobs = int(
            conn.execute(
                sa.select(sa.func.count())
                .select_from(jobs)
                .where(
                    jobs.c.kind.in_(_EXECUTE_KINDS),
                    jobs.c.status.in_(_PENDING_JOB),
                    jobs.c.payload["control_id"].astext.in_(control_ids),
                )
            ).scalar()
            or 0
        )
    details: list[str] = []
    details.extend(f"active grant {gid}" for gid in grants)
    details.extend(f"active channel {cid}" for cid in channels)
    details.extend(f"leftover control {c.id} status={c.status.value}" for c in leftover_controls)
    if pending_jobs:
        details.append(f"pending execute-family jobs {pending_jobs}")
    return PilotLeftoverInventory(
        active_grants=len(grants),
        active_channels=len(channels),
        leftover_controls=len(leftover_controls),
        pending_execute_jobs=pending_jobs,
        details=details,
    )


def leftovers_block_enable(inventory: PilotLeftoverInventory) -> bool:
    return (
        inventory.active_grants > 0
        or inventory.active_channels > 0
        or inventory.leftover_controls > 0
    )


def reconcile_pending_jobs(conn: Connection, application_id: str) -> int:
    """Fail-close queued execute-family jobs for this app. Never run the adapter."""
    control_ids = list(
        conn.execute(
            sa.select(live_controls.c.id).where(live_controls.c.application_id == application_id)
        )
        .scalars()
        .all()
    )
    if not control_ids:
        return 0
    rows = (
        conn.execute(
            sa.select(jobs.c.id).where(
                jobs.c.kind.in_(_EXECUTE_KINDS),
                jobs.c.status.in_(_PENDING_JOB),
                jobs.c.payload["control_id"].astext.in_(control_ids),
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0
    conn.execute(
        sa.update(jobs)
        .where(jobs.c.id.in_(list(rows)))
        .values(
            status="cancelled",
            cancel_requested=True,
            last_error={
                "code": "LIVE_CONTROL_DENIED",
                "message": SHADOW_ONLY_REASON,
            },
            lease_owner=None,
            lease_expires_at=None,
            updated_at=sa.func.now(),
        )
    )
    return len(list(rows))


def refuse_control_surface(row: sa.RowMapping | None) -> str | None:
    """API-layer independent of authorize.check. None means allowed to proceed."""
    if row is None:
        return None
    if application_is_gated(row):
        return SHADOW_ONLY_REASON
    return None


def gated_application_count(conn: Connection, project_id: str) -> int:
    rows = (
        conn.execute(
            sa.select(connected_applications.c.doc).where(
                connected_applications.c.project_id == project_id
            )
        )
        .scalars()
        .all()
    )
    return sum(1 for doc in rows if doc_is_gated(doc))


def persist_gated(conn: Connection, app: ConnectedApplication) -> None:
    conn.execute(
        sa.update(connected_applications)
        .where(connected_applications.c.id == app.id)
        .values(doc=app.model_dump(mode="json"))
    )


def environment_allowed(environment: str | StagingEnvironment) -> bool:
    try:
        env = (
            environment
            if isinstance(environment, StagingEnvironment)
            else StagingEnvironment(environment)
        )
    except ValueError:
        return False
    return env in (StagingEnvironment.sandbox, StagingEnvironment.owned_test)
