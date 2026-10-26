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
import logging
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
log = logging.getLogger("backend.api.ops")

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


def _require_ops(request: Request, authorization: str | None = Header(default=None)) -> None:
    token = get_settings().ops_api_token
    destructive_request = request.method == "POST" and request.url.path.endswith("/ops/maintenance")
    if token is None:
        if destructive_request:
            log.warning("destructive maintenance request refused: ops API is disabled")
        # Disabled ops surface must look like it does not exist.
        raise HTTPException(status_code=404, detail="not found")
    secret = token.get_secret_value()
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(presented, secret):
        if destructive_request:
            log.warning("destructive maintenance request refused: invalid ops authorization")
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/ops/storage", dependencies=[Depends(_require_ops)])
def storage_report() -> Any:
    """Current database/WAL/table/filesystem usage plus reclaimable estimates."""
    return maintenance.storage_report(engine=get_engine())


class MaintenanceRequest(BaseModel):
    dry_run: bool = True
    confirm_plan_digest: str | None = None


@router.post("/ops/maintenance", dependencies=[Depends(_require_ops)])
def run_maintenance(
    body: MaintenanceRequest,
    destructive_token: str | None = Header(default=None, alias="X-Ops-Destructive-Token"),
) -> Any:
    """Run a dry-run report or a separately authorized destructive cycle."""
    engine = get_engine()
    settings = get_settings()
    if body.dry_run:
        return maintenance.run_retention(engine=engine, dry_run=True)
    if settings.retention_dry_run:
        log.warning("destructive maintenance request refused: retention dry-run is enforced")
        return JSONResponse(status_code=409, content={"detail": "retention dry-run is enforced"})
    expected = settings.ops_destructive_token
    if expected is None:
        log.warning("destructive maintenance request refused: destructive maintenance disabled")
        return JSONResponse(status_code=403, content={"detail": "destructive maintenance disabled"})
    if destructive_token is None or not hmac.compare_digest(
        destructive_token, expected.get_secret_value()
    ):
        log.warning("destructive maintenance request refused: invalid destructive token")
        return JSONResponse(status_code=403, content={"detail": "invalid destructive token"})
    plan = maintenance.run_retention(engine=engine, s=settings, dry_run=True)
    if body.confirm_plan_digest is None or not hmac.compare_digest(
        body.confirm_plan_digest, plan["plan_digest"]
    ):
        log.warning("destructive maintenance request refused: plan digest mismatch")
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "message": "maintenance plan digest mismatch",
                    "plan_digest": plan["plan_digest"],
                }
            },
        )
    log.warning("destructive maintenance request accepted plan_digest=%s", plan["plan_digest"])
    return maintenance.run_retention(
        engine=engine,
        s=settings,
        dry_run=False,
        confirmed_snapshot_ids=set(plan["snapshot_ids"]),
    )


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
    data_volume = report["data_volume"]
    data_used = data_volume["used_bytes"]
    if data_used is not None:
        lines += [
            "# HELP ari_data_volume_bytes App data volume usage in bytes",
            "# TYPE ari_data_volume_bytes gauge",
            f"ari_data_volume_bytes {data_used}",
        ]
    data_pct = data_volume["pct_used"]
    if data_pct is not None:
        lines += [
            "# HELP ari_data_volume_pct App data volume utilization percent",
            "# TYPE ari_data_volume_pct gauge",
            f"ari_data_volume_pct {data_pct}",
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
