"""Execution reliability API: project-scoped operational metrics, stuck executions,
job transition history and operator resolution of ``recovery_required`` jobs.

Authorization reuses ``require_project``: viewers can read their projects' figures;
only owners may resolve recovery. Fleet-wide figures (worker counts) are not exposed
here; operators use ``python -m backend.execution``.
"""

from __future__ import annotations

from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Request
from pydantic import Field

from backend import audit, execution
from backend.api.deps import ApiError, not_found, require_project, require_user
from backend.db import assessments, jobs, transaction
from shared.audit import ActorType
from shared.base import StrictModel
from shared.tenancy import MemberRole

router = APIRouter(tags=["execution"])


class RecoveryResolution(StrictModel):
    resolution: Literal["applied", "not_applied", "retry"]
    note: str = Field(min_length=1, max_length=1000)


def job_project(conn: sa.Connection, row: Any) -> str | None:
    """Project that owns a job; assessment jobs resolve through the assessment row."""
    if row["kind"] == "run_assessment":
        return conn.execute(
            sa.select(assessments.c.project_id).where(
                assessments.c.id == (row["payload"] or {}).get("assessment_id")
            )
        ).scalar_one_or_none()
    return row["project_id"] or (row["payload"] or {}).get("project_id")


def _authorized_job(
    conn: sa.Connection, request: Request, job_id: str, min_role: MemberRole
) -> tuple[Any, str, str, str]:
    user = require_user(conn, request)
    row = conn.execute(sa.select(jobs).where(jobs.c.id == job_id)).mappings().first()
    pid = job_project(conn, row) if row else None
    if row is None or not pid:
        raise not_found("job")
    org_id, _ = require_project(conn, user, pid, min_role)
    return row, pid, org_id, user.id


@router.get("/projects/{project_id}/execution")
def project_execution(project_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, require_user(conn, request), project_id)
        out = execution.stats(conn, project_id)
        out["stuck_jobs"] = execution.stuck_jobs(conn, project_id, limit=50)
        return out


@router.get("/jobs/{job_id}/history")
def job_history(job_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        row, _, _, _ = _authorized_job(conn, request, job_id, MemberRole.viewer)
        return {
            "job_id": row["id"],
            "status": row["status"],
            "events": execution.history(conn, job_id),
        }


@router.post("/jobs/{job_id}/recovery")
def resolve_job_recovery(job_id: str, body: RecoveryResolution, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        _row, pid, org_id, user_id = _authorized_job(conn, request, job_id, MemberRole.owner)
        try:
            out = execution.resolve_recovery(
                conn, job_id, resolution=body.resolution, actor_id=user_id, note=body.note
            )
        except execution.RecoveryError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc
        audit.record(
            conn,
            org_id=org_id,
            actor_type=ActorType.user,
            actor_id=user_id,
            action="job.recovery_resolved",
            subject_type="job",
            subject_id=job_id,
            project_id=pid,
            details={"resolution": body.resolution, "status": out["status"]},
        )
        return out
