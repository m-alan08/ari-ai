"""Domain operations shared by HTTP routes and worker handlers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import audit, jobs
from backend.db import (
    assessments,
    authorized_scopes,
    deploy_commit_mappings,
    findings,
    projects,
    repositories,
)
from backend.host import emit_event, scope_problem
from backend.settings import get_settings
from shared.assessment import TERMINAL_STATES, Assessment, AssessmentState, AssessmentTrigger
from shared.audit import ActorType
from shared.base import utcnow
from shared.errors import ErrorCode
from shared.events import StreamEventType
from shared.findings import FindingStatus
from shared.ids import IdPrefix, new_id
from shared.scope import AuthorizedScope, ScopeStatus
from shared.tenancy import Budget, ProjectSettings


class DomainError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or {}


def project_settings(conn: Connection, project_id: str) -> ProjectSettings:
    raw = conn.execute(
        sa.select(projects.c.settings).where(projects.c.id == project_id)
    ).scalar_one()
    return ProjectSettings.model_validate(raw)


def project_org(conn: Connection, project_id: str) -> str:
    return conn.execute(
        sa.select(projects.c.org_id).where(projects.c.id == project_id)
    ).scalar_one()


def load_scope(conn: Connection, scope_id: str) -> AuthorizedScope | None:
    doc = conn.execute(
        sa.select(authorized_scopes.c.doc).where(authorized_scopes.c.id == scope_id)
    ).scalar()
    return AuthorizedScope.model_validate(doc) if doc else None


def active_scope_for_repo(
    conn: Connection, project_id: str, repo_id: str
) -> AuthorizedScope | None:
    docs = conn.execute(
        sa.select(authorized_scopes.c.doc)
        .where(
            authorized_scopes.c.project_id == project_id,
            authorized_scopes.c.status == ScopeStatus.active.value,
            authorized_scopes.c.expires_at > sa.func.now(),
        )
        .order_by(authorized_scopes.c.created_at.desc())
    ).scalars()
    for doc in docs:
        scope = AuthorizedScope.model_validate(doc)
        if scope_problem(scope, repo_id) is None:
            return scope
    return None


def budget_over_cap(budget: Budget) -> str | None:
    """Why `budget` exceeds the operator's per-assessment caps (NFR-07), or None."""
    s = get_settings()
    if budget.max_usd > s.llm_max_usd_per_assessment:
        return (
            f"max_usd {budget.max_usd} exceeds the operator cap "
            f"LLM_MAX_USD_PER_ASSESSMENT={s.llm_max_usd_per_assessment}"
        )
    if budget.max_tokens > s.llm_max_tokens_per_assessment:
        return (
            f"max_tokens {budget.max_tokens} exceeds the operator cap "
            f"LLM_MAX_TOKENS_PER_ASSESSMENT={s.llm_max_tokens_per_assessment}"
        )
    return None


def capped_budget(budget: Budget) -> Budget:
    """`budget` lowered to the operator's per-assessment caps."""
    s = get_settings()
    return budget.model_copy(
        update={
            "max_usd": min(budget.max_usd, s.llm_max_usd_per_assessment),
            "max_tokens": min(budget.max_tokens, s.llm_max_tokens_per_assessment),
        }
    )


def create_assessment(
    conn: Connection,
    *,
    project_id: str,
    repo_id: str,
    scope_id: str,
    commit_sha: str,
    trigger: AssessmentTrigger,
    budget: Budget | None,
    created_by: str | None,
    snapshot_id: str | None = None,
    reassessment_request_id: str | None = None,
) -> tuple[Assessment, str]:
    """Create an assessment in CREATED, emit its first event, audit, and enqueue the run job."""
    repo_row = (
        conn.execute(
            sa.select(repositories.c.project_id, repositories.c.disconnected_at)
            .where(repositories.c.id == repo_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if repo_row is None or repo_row["project_id"] != project_id:
        raise DomainError(ErrorCode.NOT_FOUND, f"repository {repo_id} not found in project", 404)
    if repo_row["disconnected_at"] is not None:
        raise DomainError(ErrorCode.CONFLICT_STATE, "repository is disconnected", 409)
    scope = load_scope(conn, scope_id)
    if scope is None or scope.project_id != project_id:
        raise DomainError(ErrorCode.NOT_FOUND, f"scope {scope_id} not found in project", 404)
    problem = scope_problem(scope, repo_id)
    if problem:
        raise DomainError(ErrorCode.SCOPE_DENIED, problem, 403)
    if budget is not None:
        over = budget_over_cap(budget)
        if over:
            raise DomainError(ErrorCode.VALIDATION_FAILED, over, 422)
    # Manual/API and automatic starts serialize on the repository row above. Reuse
    # an in-flight assessment when either start is automatic, rather than spending twice.
    reusable = conn.execute(
        sa.select(assessments.c.doc)
        .where(
            assessments.c.repo_id == repo_id,
            assessments.c.commit_sha == commit_sha,
            assessments.c.scope_id == scope_id,
            assessments.c.state.notin_([state.value for state in TERMINAL_STATES]),
            sa.or_(
                sa.literal(trigger is AssessmentTrigger.monitoring),
                assessments.c.trigger == AssessmentTrigger.monitoring.value,
            ),
        )
        .order_by(assessments.c.created_at.desc())
        .limit(1)
    ).scalar()
    if reusable:
        existing = Assessment.model_validate(reusable)
        job_id, _ = jobs.enqueue(
            conn,
            "run_assessment",
            {"assessment_id": existing.id},
            dedupe_key=f"asm:{existing.id}:start",
        )
        return existing, job_id
    settings = project_settings(conn, project_id)
    # Project budgets are checked when saved; one saved before the operator lowered the
    # cap is clamped rather than failing every push-triggered run.
    budget = budget or capped_budget(
        settings.monitoring_policy.monitoring_budget
        if trigger is AssessmentTrigger.monitoring
        else settings.default_budget
    )
    now = utcnow()
    a = Assessment(
        id=new_id(IdPrefix.asm),
        project_id=project_id,
        snapshot_id=snapshot_id,
        commit_sha=commit_sha,
        repo_id=repo_id,
        scope_id=scope_id,
        trigger=trigger,
        reassessment_request_id=reassessment_request_id,
        state=AssessmentState.CREATED,
        budget=budget,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    conn.execute(
        sa.insert(assessments).values(
            id=a.id,
            project_id=project_id,
            repo_id=repo_id,
            scope_id=scope_id,
            commit_sha=commit_sha,
            trigger=trigger.value,
            reassessment_request_id=reassessment_request_id,
            state=a.state.value,
            doc=a.model_dump(mode="json"),
        )
    )
    emit_event(
        conn,
        a.id,
        StreamEventType.state_changed,
        "assessment created",
        state=a.state,
        data={"trigger": trigger.value, "commit_sha": commit_sha},
    )
    job_id, _ = jobs.enqueue(
        conn, "run_assessment", {"assessment_id": a.id}, dedupe_key=f"asm:{a.id}:start"
    )
    audit.record(
        conn,
        org_id=project_org(conn, project_id),
        project_id=project_id,
        actor_type=ActorType.user if created_by else ActorType.system,
        actor_id=created_by or "monitoring",
        action="assessment.created",
        subject_type="assessment",
        subject_id=a.id,
        details={
            "trigger": trigger.value,
            "commit_sha": commit_sha,
            "scope_id": scope_id,
            "job_id": job_id,
        },
    )
    return a, job_id


def request_assessment_cancel(conn: Connection, assessment_id: str) -> None:
    conn.execute(
        sa.update(assessments)
        .where(assessments.c.id == assessment_id)
        .values(cancel_requested=True)
    )
    from backend.db import jobs as jobs_t

    for jid in conn.execute(
        sa.select(jobs_t.c.id).where(
            jobs_t.c.payload["assessment_id"].astext == assessment_id,
            jobs_t.c.status.in_(["queued", "running"]),
        )
    ).scalars():
        jobs.request_cancel(conn, jid)


def cancel_queued_assessment(conn: Connection, assessment_id: str) -> bool:
    """If no worker has started it yet, move straight to CANCELLED."""
    row = conn.execute(
        sa.select(assessments.c.state, assessments.c.doc)
        .where(assessments.c.id == assessment_id)
        .with_for_update()
    ).first()
    if row is None or row.state != AssessmentState.CREATED.value:
        return False
    a = Assessment.model_validate(row.doc)
    now = utcnow()
    a = a.model_copy(
        update={"state": AssessmentState.CANCELLED, "updated_at": now, "completed_at": now}
    )
    conn.execute(
        sa.update(assessments)
        .where(assessments.c.id == assessment_id)
        .values(state=a.state.value, doc=a.model_dump(mode="json"))
    )
    emit_event(
        conn,
        assessment_id,
        StreamEventType.state_changed,
        "cancelled before start",
        state=AssessmentState.CANCELLED,
        data={"from": "CREATED"},
    )
    emit_event(
        conn, assessment_id, StreamEventType.failed, "cancelled", state=AssessmentState.CANCELLED
    )
    return True


def verified_finding_paths(conn: Connection, project_id: str) -> dict[str, list[str]]:
    rows = conn.execute(
        sa.select(findings.c.id, findings.c.paths).where(
            findings.c.project_id == project_id, findings.c.status == FindingStatus.verified.value
        )
    ).all()
    return {r.id: list(r.paths or []) for r in rows}


def repository_revocation(
    conn: Connection,
    *,
    repo_id: str | None,
    application_id: str | None = None,
    mapping_id: str | None = None,
) -> str | None:
    """Canonical revocation check for provider/agent callbacks and persists.

    ``repositories.disconnected_at`` is the single source of truth — never infer
    revocation from ``installation_id`` being null (it is the access record, not
    the signal). Returns ``'repository_disconnected'`` or
    ``'deploy_mapping_revoked'`` when the repo context is revoked, else ``None``.
    Callers must run this inside their transaction before every billable
    provider turn and before persisting output; any non-None value means stop
    without spending or writing. Pair the read with a lock on the repo/mapping
    row when a caller needs serialization against a concurrent disconnect."""
    if repo_id:
        disconnected = conn.execute(
            sa.select(repositories.c.disconnected_at).where(repositories.c.id == repo_id)
        ).scalar_one_or_none()
        if disconnected is not None:
            return "repository_disconnected"
    clauses = []
    if mapping_id:
        clauses.append(deploy_commit_mappings.c.id == mapping_id)
    elif application_id and repo_id:
        clauses.extend(
            [
                deploy_commit_mappings.c.application_id == application_id,
                deploy_commit_mappings.c.repo_id == repo_id,
            ]
        )
    if clauses:
        active = conn.execute(
            sa.select(deploy_commit_mappings.c.id).where(
                *clauses, deploy_commit_mappings.c.revoked_at.is_(None)
            )
        ).first()
        if active is None:
            return "deploy_mapping_revoked"
    return None


def repository_revocation_locked(
    conn: Connection,
    *,
    repo_id: str | None,
    application_id: str | None = None,
    mapping_id: str | None = None,
) -> str | None:
    """Serializable revocation check for transactional finalization.

    Canonical lock order — shared with ``POST /repositories/{id}/disconnect``:
    the ``repositories`` row first, then ``deploy_commit_mappings`` rows, both
    ``FOR UPDATE``. A finalizer that locks in this order before writing a
    success outcome serializes against a concurrent disconnect: whichever side
    commits first wins deterministically — a persister that waited on the lock
    re-reads revocation and sees the revoked state, so no ``executed`` row can
    land after disconnect commits. Never hold these locks across a provider
    call: lock only at the finalization boundary, and use the unlocked
    ``repository_revocation`` for cheap per-turn pre-checks.
    """
    if repo_id:
        conn.execute(
            sa.select(repositories.c.id).where(repositories.c.id == repo_id).with_for_update()
        )
    clauses = []
    if mapping_id:
        clauses.append(deploy_commit_mappings.c.id == mapping_id)
    elif application_id and repo_id:
        clauses.extend(
            [
                deploy_commit_mappings.c.application_id == application_id,
                deploy_commit_mappings.c.repo_id == repo_id,
            ]
        )
    if clauses:
        conn.execute(sa.select(deploy_commit_mappings.c.id).where(*clauses).with_for_update())
    return repository_revocation(
        conn, repo_id=repo_id, application_id=application_id, mapping_id=mapping_id
    )


def monitoring_assessment_state(conn: Connection, repo_id: str) -> tuple[bool, float | None]:
    """(open monitoring assessment exists, seconds since the latest one was created)."""
    rows = conn.execute(
        sa.select(assessments.c.state, assessments.c.created_at)
        .where(
            assessments.c.repo_id == repo_id,
            assessments.c.trigger == AssessmentTrigger.monitoring.value,
        )
        .order_by(assessments.c.created_at.desc())
        .limit(20)
    ).all()
    terminal = {s.value for s in TERMINAL_STATES}
    open_ = any(r.state not in terminal for r in rows)
    last: datetime | None = rows[0].created_at if rows else None
    return open_, (utcnow() - last).total_seconds() if last else None
