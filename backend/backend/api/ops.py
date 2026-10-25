"""Operator surface.

GET /api/v1/ops/status — deploy, migration and queue visibility; token-gated by
the X-Ops-Token header (OPS_TOKEN). When OPS_TOKEN is unset the endpoint answers
404 outside development/test — an unconfigured token must not open an ops view
on the internet. It returns a plain JSON object: operator tooling, not a
versioned client contract.

Storage reporting and on-demand maintenance (upstream) — disabled entirely
unless OPS_API_TOKEN is set; a Bearer token checked in constant time, independent
of user sessions/CSRF (this surface is for automation and on-call, not the UI).
See docs/deploy/POSTGRES_STORAGE.md.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend import maintenance
from backend.api.deps import ApiError
from backend.db import first_responder_modes, get_engine, jobs
from backend.llm import llm_mode
from backend.preflight import migration_status
from backend.settings import get_settings
from shared import SCHEMA_VERSION
from shared.errors import ErrorCode

router = APIRouter()

_HEADER = "X-Ops-Token"


def _require_ops_header(request: Request) -> None:
    s = get_settings()
    token = s.ops_token.get_secret_value() if s.ops_token else ""
    if not token:
        if s.is_dev:
            return
        raise ApiError(ErrorCode.NOT_FOUND, "not found", 404)
    given = request.headers.get(_HEADER, "")
    if not (given and secrets.compare_digest(given, token)):
        raise ApiError(ErrorCode.FORBIDDEN, "invalid or missing ops token", 403)


def _queue_snapshot(conn: sa.Connection) -> dict:
    counts: dict[str, int] = dict(
        conn.execute(sa.select(jobs.c.status, sa.func.count()).group_by(jobs.c.status)).all()
    )
    running = []
    for row in conn.execute(
        sa.select(
            jobs.c.id,
            jobs.c.kind,
            jobs.c.lease_owner,
            jobs.c.attempts,
            jobs.c.heartbeat_at,
            sa.func.now().label("now"),
        ).where(jobs.c.status == "running")
    ).mappings():
        hb = row["heartbeat_at"]
        age = (row["now"] - hb).total_seconds() if hb else None
        running.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "owner": row["lease_owner"],
                "attempts": row["attempts"],
                "heartbeat_age_s": round(age, 1) if age is not None else None,
            }
        )
    oldest_queued = conn.execute(
        sa.select(sa.func.min(jobs.c.run_after)).where(
            jobs.c.status == "queued", jobs.c.run_after <= sa.func.now()
        )
    ).scalar()
    next_due = conn.execute(
        sa.select(sa.func.min(jobs.c.run_after)).where(jobs.c.status == "queued")
    ).scalar()
    now = conn.execute(sa.select(sa.func.now())).scalar()
    workers = [
        {"owner": row["lease_owner"], "last_heartbeat": row["last_heartbeat"].isoformat()}
        for row in conn.execute(
            sa.select(jobs.c.lease_owner, sa.func.max(jobs.c.heartbeat_at).label("last_heartbeat"))
            .where(jobs.c.status == "running", jobs.c.lease_owner.is_not(None))
            .group_by(jobs.c.lease_owner)
        ).mappings()
    ]
    return {
        "counts": counts,
        # Age of the oldest claimable job (run_after in the past); deferred jobs
        # report separately as next_due_in_s so backlog never reads negative.
        "oldest_queued_age_s": (
            round((now - oldest_queued).total_seconds(), 1) if oldest_queued and now else None
        ),
        "next_due_in_s": (round((next_due - now).total_seconds(), 1) if next_due and now else None),
        "running": running,
        # A worker heartbeats only while it owns a job; an idle worker leaves no trace.
        # This is the honest bound on "worker visibility" without a registry table.
        "workers_with_leases": workers,
    }


@router.get("/ops/status")
def ops_status(request: Request) -> JSONResponse:
    _require_ops_header(request)
    from backend.main import VERSION  # deferred: main imports this router

    s = get_settings()
    with get_engine().begin() as conn:
        mig = migration_status(conn)
        queue = _queue_snapshot(conn)
        non_off = conn.execute(
            sa.select(sa.func.count())
            .select_from(first_responder_modes)
            .where(first_responder_modes.c.intent != "off")
        ).scalar_one()
    body = {
        "version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "app_env": s.app_env,
        "llm_mode": llm_mode(),
        "migrations": {
            "current": mig.current,
            "expected": mig.expected,
            "at_head": mig.at_head,
        },
        "queue": queue,
        "first_responder": {
            "default_mode": "off",
            "projects_with_non_default_mode": non_off,
        },
        "alerting_configured": bool(s.alert_webhook_url),
        "feature_flags": sorted(s.flags()),
    }
    return JSONResponse(body)


# --- Storage reporting + on-demand maintenance (upstream; OPS_API_TOKEN Bearer) ---


def _require_ops(authorization: str | None = Header(default=None)) -> None:
    token = get_settings().ops_api_token
    if token is None:
        # Disabled ops surface must look like it does not exist.
        raise HTTPException(status_code=404, detail="not found")
    secret = token.get_secret_value()
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(presented, secret):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/ops/storage", dependencies=[Depends(_require_ops)])
def storage_report() -> Any:
    """Current database/WAL/table/filesystem usage plus reclaimable estimates."""
    return maintenance.storage_report(engine=get_engine())


class MaintenanceRequest(BaseModel):
    dry_run: bool = True


@router.post("/ops/maintenance", dependencies=[Depends(_require_ops)])
def run_maintenance(body: MaintenanceRequest) -> Any:
    """Run one retention cycle. Defaults to dry-run; pass {"dry_run": false} to delete."""
    return maintenance.run_retention(engine=get_engine(), dry_run=body.dry_run)


@router.get("/ops/storage/prometheus", dependencies=[Depends(_require_ops)])
def storage_prometheus() -> Any:
    """Same numbers in Prometheus text exposition format for external scrapers."""
    report = maintenance.storage_report(engine=get_engine())
    lines = [
        "# HELP ari_postgres_bytes PostgreSQL database size in bytes",
        "# TYPE ari_postgres_bytes gauge",
        f"ari_postgres_bytes {report['database']['size_bytes']}",
        "# HELP ari_postgres_wal_bytes WAL directory size in bytes",
        "# TYPE ari_postgres_wal_bytes gauge",
        f"ari_postgres_wal_bytes {report['wal_bytes'] or 0}",
        "# HELP ari_postgres_table_bytes Total relation size by table",
        "# TYPE ari_postgres_table_bytes gauge",
    ]
    for t in report["tables"]:
        lines.append(f'ari_postgres_table_bytes{{table="{t["table"]}"}} {t["total_bytes"]}')
    pct = report["volume"]["pct_used"]
    if pct is not None:
        lines += [
            "# HELP ari_postgres_volume_pct Volume utilization percent",
            "# TYPE ari_postgres_volume_pct gauge",
            f"ari_postgres_volume_pct {pct}",
        ]
    fs = report.get("filesystem", {})
    if "artifact_bytes" in fs:
        lines.append(f"ari_artifact_store_bytes {fs['artifact_bytes']}")
    if "workdir_bytes" in fs:
        lines.append(f"ari_security_workdir_bytes {fs['workdir_bytes']}")
    from fastapi import Response

    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@router.post("/ops/maintenance/dry-run-report", dependencies=[Depends(_require_ops)])
def dry_run_report() -> Any:
    """What the next real cycle would delete, without deleting it."""
    return maintenance.run_retention(engine=get_engine(), dry_run=True)
