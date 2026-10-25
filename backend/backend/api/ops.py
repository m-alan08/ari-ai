"""Operator endpoints: storage reporting and on-demand maintenance.

Entirely disabled unless OPS_API_TOKEN is set — a Bearer token checked in constant
time, independent of user sessions/CSRF (this surface is for automation and on-call,
not the UI). See docs/deploy/POSTGRES_STORAGE.md.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from backend import maintenance
from backend.db import get_engine
from backend.settings import get_settings

router = APIRouter()


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
