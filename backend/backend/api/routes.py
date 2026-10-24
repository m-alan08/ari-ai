"""/api/v1 resource routes (docs/13). Every state change is authorized in code and audited."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field

from backend import audit, jobs
from backend.api.deps import (
    ApiError,
    CurrentUser,
    idempotent_finish,
    idempotent_lookup,
    idempotent_replay,
    idempotent_store,
    limiter,
    not_found,
    require_project,
    require_user,
)
from backend.api.execution import job_project
from backend.artifacts import ArtifactStore
from backend.db import (
    agent_runs,
    approvals,
    artifacts,
    assessment_events,
    assessments,
    audit_events,
    authorized_scopes,
    defense_recommendations,
    finding_records,
    findings,
    monitoring_events,
    patch_candidates,
    projects,
    reassessment_requests,
    repositories,
    rl_evaluations,
    snapshots,
    transaction,
    verification_results,
)
from backend.integrations import load_factory
from backend.services import (
    DomainError,
    budget_over_cap,
    cancel_queued_assessment,
    create_assessment,
    project_org,
    project_settings,
    request_assessment_cancel,
)
from backend.settings import get_settings
from shared.api import (
    AcceptRisk,
    ApprovalDecision,
    AssessmentCreate,
    AssessmentDetail,
    AssessmentReport,
    FindingDetail,
    PatchView,
    ProjectCreate,
    ProjectUpdate,
    RepositoryConnect,
    RepositoryUpdate,
    ScopeCreate,
    SnapshotCreate,
)
from shared.assessment import (
    TERMINAL_STATES,
    AgentRun,
    Assessment,
    AssessmentState,
    AssessmentTrigger,
)
from shared.audit import ActorType, AuditEvent
from shared.base import ShortText, StrictModel, utcnow
from shared.errors import ErrorCode
from shared.events import StreamEvent
from shared.evidence import ArtifactKind
from shared.findings import Finding, FindingStatus, PatchCandidate, StatusChange, VerificationResult
from shared.ids import IdPrefix, new_id
from shared.intelligence import Snapshot, SnapshotStatus
from shared.monitoring import MonitoringEvent, ReassessmentRequest
from shared.rl import DefenseRecommendation, PlatformFacts, RLEvaluation, RLExplanation
from shared.scope import (
    Approval,
    ApprovalStatus,
    ApprovalSubject,
    AuthorizedScope,
    ScopeStatus,
)
from shared.tenancy import DeliveryMode, MemberRole, Project, ProjectSettings, Repository

router = APIRouter()


class Page(StrictModel):
    items: list[Any]
    next_cursor: str | None = None


def _user(conn: Any, request: Request) -> CurrentUser:
    user = require_user(conn, request)
    limiter.check(f"user:{user.id}", get_settings().rate_limit_per_minute, 60)
    return user


def _audit(
    conn: Any,
    org_id: str,
    project_id: str | None,
    user: CurrentUser,
    action: str,
    subject_type: str,
    subject_id: str,
    **details: Any,
) -> None:
    audit.record(
        conn,
        org_id=org_id,
        project_id=project_id,
        actor_type=ActorType.user,
        actor_id=user.id,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        details=details,
    )


def _keyset_where(table: sa.Table, cursor: str | None) -> list[Any]:
    """Newest-first keyset paging on (created_at, id); the cursor is "<iso>|<id>" from the
    previous page's last row. Rows written in one transaction share created_at, so id breaks ties."""
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
    """``rows`` are (created_at, id, doc) fetched with limit+1."""
    items = rows[:limit]
    if len(rows) > limit and items:
        last = items[-1]
        return [r[2] for r in items], f"{last[0].isoformat()}|{last[1]}"
    return [r[2] for r in items], None


def _page(rows: list[Any], limit: int, key: str = "id") -> dict[str, Any]:
    items = rows[:limit]
    return {"items": items, "next_cursor": items[-1][key] if len(rows) > limit and items else None}


# ------------------------------------------------------------------- projects


@router.get("/projects")
def list_projects(request: Request) -> dict[str, Any]:
    from backend.db import memberships

    with transaction() as conn:
        user = _user(conn, request)
        rows = conn.execute(
            sa.select(projects)
            .join(memberships, memberships.c.org_id == projects.c.org_id)
            .where(memberships.c.user_id == user.id)
            .order_by(projects.c.created_at)
        ).mappings()
        return {"items": [_project(r).model_dump(mode="json") for r in rows], "next_cursor": None}


def _project(r: Any) -> Project:
    return Project(
        id=r["id"],
        org_id=r["org_id"],
        name=r["name"],
        settings=ProjectSettings.model_validate(r["settings"]),
        created_at=r["created_at"],
    )


@router.post("/projects", status_code=201, response_model=Project)
def create_project(
    body: ProjectCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    from backend.api.deps import org_role
    from backend.db import users

    with transaction() as conn:
        user = _user(conn, request)
        org_id = (
            body.org_id
            or conn.execute(
                sa.select(users.c.personal_org_id).where(users.c.id == user.id)
            ).scalar_one()
        )
        role = org_role(conn, user.id, org_id)
        if role is None:
            raise not_found("organization")
        if role is MemberRole.viewer:
            raise ApiError(ErrorCode.FORBIDDEN, "viewers cannot create projects", 403)
        retry = {"route": "create_project", "org_id": org_id, "body": body.model_dump(mode="json")}
        if replay := idempotent_replay(conn, user.id, idempotency_key, retry):
            return replay
        pid = new_id(IdPrefix.prj)
        settings = body.settings or ProjectSettings()
        if role is not MemberRole.owner:  # only owners may authorize Ari to deliver code
            settings = settings.model_copy(update={"delivery_mode": DeliveryMode.manual})
        settings = _stamp_delivery(settings, ProjectSettings(), user.id)
        _check_budgets(settings, ProjectSettings())
        conn.execute(
            sa.insert(projects).values(
                id=pid, org_id=org_id, name=body.name, settings=settings.model_dump(mode="json")
            )
        )
        _audit(conn, org_id, pid, user, "project.created", "project", pid, name=body.name)
        created = _project(
            conn.execute(sa.select(projects).where(projects.c.id == pid)).mappings().one()
        )
        return idempotent_finish(conn, user.id, idempotency_key, retry, 201, created)


@router.get("/projects/{project_id}", response_model=Project)
def get_project(project_id: str, request: Request) -> Project:
    with transaction() as conn:
        require_project(conn, _user(conn, request), project_id)
        return _project(
            conn.execute(sa.select(projects).where(projects.c.id == project_id)).mappings().one()
        )


def _check_budgets(settings: ProjectSettings, before: ProjectSettings) -> None:
    """A budget the caller changes may not exceed the operator's per-assessment caps
    (NFR-07). Unchanged ones (e.g. the defaults under a lowered cap) are clamped on use."""
    pairs = (
        (settings.default_budget, before.default_budget),
        (settings.monitoring_policy.monitoring_budget, before.monitoring_policy.monitoring_budget),
    )
    for b, old in pairs:
        if b != old and (over := budget_over_cap(b)):
            raise ApiError(ErrorCode.VALIDATION_FAILED, over, 422)


def _stamp_delivery(
    settings: ProjectSettings, before: ProjectSettings, user_id: str
) -> ProjectSettings:
    """The server alone records who authorized delivery; a client value is ignored. The
    authorizer is kept while the mode is unchanged, and is the saving owner otherwise."""
    same = before.delivery_mode is settings.delivery_mode
    kept = before.delivery_authorized_by if same else None
    return settings.model_copy(update={"delivery_authorized_by": kept or user_id})


@router.patch("/projects/{project_id}", response_model=Project)
def update_project(project_id: str, body: ProjectUpdate, request: Request) -> Project:
    with transaction() as conn:
        user = _user(conn, request)
        org_id, _ = require_project(conn, user, project_id, MemberRole.owner)
        values: dict[str, Any] = {}
        changed_delivery = False
        if body.name is not None:
            values["name"] = body.name
        if body.settings is not None:
            before = project_settings(conn, project_id)
            _check_budgets(body.settings, before)
            settings = _stamp_delivery(body.settings, before, user.id)
            values["settings"] = settings.model_dump(mode="json")
            if not before.delivery_authorized_by and settings.delivery_authorized_by:
                changed_delivery = True  # legacy project: authority just became effective
            if settings.delivery_mode is not before.delivery_mode:
                changed_delivery = True
                _audit(
                    conn,
                    org_id,
                    project_id,
                    user,
                    f"delivery.{settings.delivery_mode.value}",
                    "project",
                    project_id,
                )
        if values:
            conn.execute(sa.update(projects).where(projects.c.id == project_id).values(**values))
            if changed_delivery:  # runs already waiting must not stay stuck behind the old mode
                from backend.delivery import approve_waiting_runs

                approve_waiting_runs(conn, project_id)
            _audit(
                conn,
                org_id,
                project_id,
                user,
                "project.updated",
                "project",
                project_id,
                fields=sorted(values),
            )
        return _project(
            conn.execute(sa.select(projects).where(projects.c.id == project_id)).mappings().one()
        )


def _repo(r: Any) -> Repository:
    return Repository(
        id=r["id"],
        project_id=r["project_id"],
        full_name=r["full_name"],
        default_branch=r["default_branch"],
        installation_id=r["installation_id"],
        automatic_assessments=r["automatic_assessments"],
        created_at=r["created_at"],
    )


@router.get("/projects/{project_id}/repositories")
def list_repositories(project_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, _user(conn, request), project_id)
        rows = conn.execute(
            sa.select(repositories).where(repositories.c.project_id == project_id)
        ).mappings()
        return {"items": [_repo(r).model_dump(mode="json") for r in rows], "next_cursor": None}


@router.post("/projects/{project_id}/repositories", status_code=201, response_model=Repository)
def connect_repository(project_id: str, body: RepositoryConnect, request: Request) -> Repository:
    with transaction() as conn:
        user = _user(conn, request)
        org_id, _ = require_project(conn, user, project_id, MemberRole.owner)
        # The worker fetches App-connected repositories with an installation token, so the
        # caller must have reached this exact repository through that installation at sign-in.
        if body.installation_id is not None:
            from backend.api.github_repos import can_connect

            if not can_connect(conn, user.id, body.full_name, body.installation_id):
                raise ApiError(
                    ErrorCode.FORBIDDEN,
                    "this GitHub App installation does not give you access to that repository",
                    403,
                )
        if conn.execute(
            sa.select(repositories.c.id).where(repositories.c.full_name == body.full_name)
        ).scalar():
            raise ApiError(
                ErrorCode.CONFLICT_STATE, "repository already connected to a project", 409
            )
        rid = new_id(IdPrefix.rep)
        conn.execute(
            sa.insert(repositories).values(
                id=rid,
                project_id=project_id,
                full_name=body.full_name,
                default_branch=body.default_branch,
                installation_id=body.installation_id,
            )
        )
        _audit(
            conn,
            org_id,
            project_id,
            user,
            "repository.connected",
            "repository",
            rid,
            full_name=body.full_name,
        )
        if body.installation_id is not None:  # propose a build_recipe.json PR (backend.recipes)
            from backend.recipes import enqueue

            enqueue(conn, rid, "connected")
        return _repo(
            conn.execute(sa.select(repositories).where(repositories.c.id == rid)).mappings().one()
        )


def _repo_access(
    conn: Any, user: CurrentUser, repo_id: str, role: MemberRole = MemberRole.viewer
) -> dict[str, Any]:
    r = conn.execute(sa.select(repositories).where(repositories.c.id == repo_id)).mappings().first()
    if r is None:
        raise not_found("repository")
    require_project(conn, user, r["project_id"], role)
    return dict(r)


@router.get("/repositories/{repo_id}", response_model=Repository)
def get_repository(repo_id: str, request: Request) -> Repository:
    with transaction() as conn:
        return _repo(_repo_access(conn, _user(conn, request), repo_id))


@router.patch("/repositories/{repo_id}", response_model=Repository)
def update_repository(repo_id: str, body: RepositoryUpdate, request: Request) -> Repository:
    with transaction() as conn:
        user = _user(conn, request)
        repo = _repo_access(conn, user, repo_id, MemberRole.owner)
        conn.execute(
            sa.update(repositories)
            .where(repositories.c.id == repo_id)
            .values(automatic_assessments=body.automatic_assessments)
        )
        _audit(
            conn,
            project_org(conn, repo["project_id"]),
            repo["project_id"],
            user,
            "repository.automation_updated",
            "repository",
            repo_id,
            automatic_assessments=body.automatic_assessments,
        )
        return _repo(
            conn.execute(sa.select(repositories).where(repositories.c.id == repo_id))
            .mappings()
            .one()
        )


@router.post("/repositories/{repo_id}/snapshots", status_code=202, response_model=Snapshot)
def request_snapshot(repo_id: str, body: SnapshotCreate, request: Request) -> Snapshot:
    with transaction() as conn:
        user = _user(conn, request)
        repo = _repo_access(conn, user, repo_id, MemberRole.member)
        existing = conn.execute(
            sa.select(snapshots.c.doc)
            .where(
                snapshots.c.repo_id == repo_id,
                snapshots.c.commit_sha == body.commit_sha,
                snapshots.c.status != SnapshotStatus.failed.value,
            )
            .order_by(snapshots.c.created_at.desc())
        ).scalar()
        if existing and existing["status"] in (
            SnapshotStatus.pending.value,
            SnapshotStatus.indexing.value,
        ):
            return Snapshot.model_validate(existing)
        if existing and existing["status"] == SnapshotStatus.ready.value:
            # Historical ready snapshots remain readable, but must not prevent a
            # parser/profile upgrade from indexing this SHA through the normal API.
            loaded = load_factory(get_settings().code_intelligence_factory)
            version_for = getattr(loaded.obj, "current_index_version", None)
            if not callable(version_for):
                raise ApiError(
                    ErrorCode.SUBSYSTEM_UNAVAILABLE, "Indexer version is unavailable", 503
                )
            try:
                version = version_for(repo["project_id"], repo_id)
            except ValueError as exc:
                raise ApiError(
                    ErrorCode.SUBSYSTEM_UNAVAILABLE, "Indexer version is unavailable", 503
                ) from exc
            current = conn.execute(
                sa.select(snapshots.c.doc).where(
                    snapshots.c.repo_id == repo_id,
                    snapshots.c.commit_sha == body.commit_sha,
                    snapshots.c.index_version == version,
                    snapshots.c.status == SnapshotStatus.ready.value,
                )
            ).scalar()
            if current:
                return Snapshot.model_validate(current)
        snap = Snapshot(
            id=new_id(IdPrefix.snp),
            repo_id=repo_id,
            commit_sha=body.commit_sha,
            status=SnapshotStatus.pending,
            index_version="pending",
            created_at=utcnow(),
        )
        conn.execute(
            sa.insert(snapshots).values(
                id=snap.id,
                repo_id=repo_id,
                commit_sha=snap.commit_sha,
                index_version=f"pending:{snap.id}",
                status=snap.status.value,
                doc=snap.model_dump(mode="json"),
            )
        )
        jobs.enqueue(
            conn,
            "index_snapshot",
            {"snapshot_id": snap.id, "project_id": repo["project_id"]},
            dedupe_key=f"snp:{snap.id}",
        )
        return snap


@router.get("/repositories/{repo_id}/snapshots")
def list_snapshots(
    repo_id: str,
    request: Request,
    status: SnapshotStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> dict[str, Any]:
    """Newest first. ``status=ready`` lists the snapshots intelligence reads can use."""
    with transaction() as conn:
        _repo_access(conn, _user(conn, request), repo_id)
        q = sa.select(snapshots.c.id, snapshots.c.doc).where(snapshots.c.repo_id == repo_id)
        if status is not None:
            q = q.where(snapshots.c.status == status.value)
        if cursor:
            q = q.where(snapshots.c.id < cursor)
        rows = [
            {"id": r.id, "doc": r.doc}
            for r in conn.execute(q.order_by(snapshots.c.id.desc()).limit(limit + 1))
        ]
        page = _page(rows, limit)
        return {
            "items": [
                Snapshot.model_validate(r["doc"]).model_dump(mode="json") for r in page["items"]
            ],
            "next_cursor": page["next_cursor"],
        }


@router.get("/snapshots/{snapshot_id}", response_model=Snapshot)
def get_snapshot(snapshot_id: str, request: Request) -> Snapshot:
    with transaction() as conn:
        user = _user(conn, request)
        row = (
            conn.execute(sa.select(snapshots).where(snapshots.c.id == snapshot_id))
            .mappings()
            .first()
        )
        if row is None:
            raise not_found("snapshot")
        _repo_access(conn, user, row["repo_id"])
        return Snapshot.model_validate(row["doc"])


# ------------------------------------------------------------ scope & approvals


@router.get("/projects/{project_id}/scopes")
def list_scopes(project_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, _user(conn, request), project_id)
        docs = conn.execute(
            sa.select(authorized_scopes.c.doc)
            .where(authorized_scopes.c.project_id == project_id)
            .order_by(authorized_scopes.c.created_at.desc())
        ).scalars()
        return {"items": list(docs), "next_cursor": None}


@router.post("/projects/{project_id}/scopes", status_code=201, response_model=AuthorizedScope)
def request_scope(
    project_id: str,
    body: ScopeCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    with transaction() as conn:
        user = _user(conn, request)
        org_id, _ = require_project(conn, user, project_id, MemberRole.member)
        retry = {
            "route": "request_scope",
            "project_id": project_id,
            "body": body.model_dump(mode="json"),
        }
        if replay := idempotent_replay(conn, user.id, idempotency_key, retry):
            return replay
        if body.expires_at <= utcnow() or body.expires_at > utcnow() + timedelta(days=90):
            raise ApiError(
                ErrorCode.VALIDATION_FAILED,
                "expires_at must be in the future and within 90 days",
                422,
            )
        for t in body.targets:
            owner = conn.execute(
                sa.select(repositories.c.project_id).where(repositories.c.id == t.repo_id)
            ).scalar()
            if owner != project_id:
                raise ApiError(
                    ErrorCode.SCOPE_DENIED,
                    f"target repository {t.repo_id} is not in this project",
                    403,
                )
        scope = AuthorizedScope(
            id=new_id(IdPrefix.scp),
            project_id=project_id,
            name=body.name,
            targets=body.targets,
            allowed_check_categories=body.allowed_check_categories,
            status=ScopeStatus.pending,
            requested_by=user.id,
            expires_at=body.expires_at,
            notes=body.notes,
        )
        conn.execute(
            sa.insert(authorized_scopes).values(
                id=scope.id,
                project_id=project_id,
                status=scope.status.value,
                expires_at=scope.expires_at,
                doc=scope.model_dump(mode="json"),
            )
        )
        ap = Approval(
            id=new_id(IdPrefix.apv),
            project_id=project_id,
            subject_type=ApprovalSubject.scope,
            subject_id=scope.id,
            status=ApprovalStatus.pending,
            requested_by=user.id,
            requested_at=utcnow(),
        )
        conn.execute(
            sa.insert(approvals).values(
                id=ap.id,
                project_id=project_id,
                subject_type="scope",
                subject_id=scope.id,
                status="pending",
                doc=ap.model_dump(mode="json"),
            )
        )
        _audit(
            conn,
            org_id,
            project_id,
            user,
            "scope.requested",
            "scope",
            scope.id,
            targets=[t.model_dump(mode="json") for t in body.targets],
        )
        return idempotent_finish(conn, user.id, idempotency_key, retry, 201, scope)


def _scope_row(conn: Any, scope_id: str) -> AuthorizedScope:
    doc = conn.execute(
        sa.select(authorized_scopes.c.doc)
        .where(authorized_scopes.c.id == scope_id)
        .with_for_update()
    ).scalar()
    if doc is None:
        raise not_found("scope")
    return AuthorizedScope.model_validate(doc)


def _save_scope(conn: Any, scope: AuthorizedScope) -> None:
    conn.execute(
        sa.update(authorized_scopes)
        .where(authorized_scopes.c.id == scope.id)
        .values(
            status=scope.status.value,
            expires_at=scope.expires_at,
            doc=scope.model_dump(mode="json"),
        )
    )


@router.post("/scopes/{scope_id}/approve", response_model=AuthorizedScope)
def approve_scope(scope_id: str, request: Request) -> AuthorizedScope:
    """Activate a pending scope; owners may self-approve sandbox-only targets."""
    with transaction() as conn:
        user = _user(conn, request)
        scope = _scope_row(conn, scope_id)
        org_id, _ = require_project(conn, user, scope.project_id, MemberRole.owner)
        if scope.status is not ScopeStatus.pending:
            raise ApiError(ErrorCode.CONFLICT_STATE, f"scope is {scope.status.value}", 409)
        sandbox_only = bool(scope.targets) and all(
            target.kind == "sandbox_build" for target in scope.targets
        )
        if scope.requested_by == user.id and not sandbox_only:
            raise ApiError(
                ErrorCode.FORBIDDEN, "self-approval is limited to sandbox-only scopes", 403
            )
        if scope.expires_at <= utcnow():
            raise ApiError(ErrorCode.CONFLICT_STATE, "scope already expired", 409)
        now = utcnow()
        scope = scope.model_copy(
            update={"status": ScopeStatus.active, "approved_by": user.id, "approved_at": now}
        )
        _save_scope(conn, scope)
        from backend.auto_commits import resume_blocked

        resume_blocked(conn, scope.project_id, scope.id)
        _decide_subject_approval(conn, "scope", scope.id, user.id, ApprovalStatus.approved, None)
        _audit(
            conn,
            org_id,
            scope.project_id,
            user,
            "scope.approved",
            "scope",
            scope.id,
            requested_by=scope.requested_by,
            self_approved=scope.requested_by == user.id,
            sandbox_only=sandbox_only,
        )
        return scope


@router.post("/scopes/{scope_id}/revoke", response_model=AuthorizedScope)
def revoke_scope(scope_id: str, request: Request) -> AuthorizedScope:
    with transaction() as conn:
        user = _user(conn, request)
        scope = _scope_row(conn, scope_id)
        org_id, _ = require_project(conn, user, scope.project_id, MemberRole.member)
        if scope.status in (ScopeStatus.revoked, ScopeStatus.expired):
            return scope
        scope = scope.model_copy(update={"status": ScopeStatus.revoked})
        _save_scope(conn, scope)
        _decide_subject_approval(
            conn, "scope", scope.id, user.id, ApprovalStatus.declined, "revoked"
        )
        running = (
            conn.execute(
                sa.select(assessments.c.id).where(
                    assessments.c.scope_id == scope_id,
                    assessments.c.state.notin_([s.value for s in TERMINAL_STATES]),
                )
            )
            .scalars()
            .all()
        )
        for asm_id in running:  # running assessments stop (docs/13)
            if not cancel_queued_assessment(conn, asm_id):
                request_assessment_cancel(conn, asm_id)
        _audit(
            conn,
            org_id,
            scope.project_id,
            user,
            "scope.revoked",
            "scope",
            scope.id,
            stopped=running,
        )
        return scope


def _decide_subject_approval(
    conn: Any,
    subject_type: str,
    subject_id: str,
    user_id: str,
    status: ApprovalStatus,
    reason: str | None,
) -> None:
    for doc in conn.execute(
        sa.select(approvals.c.doc).where(
            approvals.c.subject_type == subject_type,
            approvals.c.subject_id == subject_id,
            approvals.c.status == "pending",
        )
    ).scalars():
        ap = Approval.model_validate(doc).model_copy(
            update={
                "status": status,
                "decided_by": user_id,
                "decided_at": utcnow(),
                "reason": reason,
            }
        )
        conn.execute(
            sa.update(approvals)
            .where(approvals.c.id == ap.id)
            .values(status=status.value, doc=ap.model_dump(mode="json"))
        )


@router.get("/projects/{project_id}/approvals")
def list_approvals(
    project_id: str, request: Request, status: ApprovalStatus | None = None
) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, _user(conn, request), project_id)
        q = sa.select(approvals.c.doc).where(approvals.c.project_id == project_id)
        if status:
            q = q.where(approvals.c.status == status.value)
        return {
            "items": list(conn.execute(q.order_by(approvals.c.created_at.desc())).scalars()),
            "next_cursor": None,
        }


@router.post("/approvals/{approval_id}/decision", response_model=Approval)
def decide_approval(
    approval_id: str,
    body: ApprovalDecision,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    with transaction() as conn:
        user = _user(conn, request)
        row = (
            conn.execute(
                sa.select(approvals).where(approvals.c.id == approval_id).with_for_update()
            )
            .mappings()
            .first()
        )
        if row is None:
            raise not_found("approval")
        org_id, _ = require_project(conn, user, row["project_id"], MemberRole.owner)
        retry = {
            "route": "decide_approval",
            "approval_id": approval_id,
            "body": body.model_dump(mode="json"),
        }
        if replay := idempotent_replay(conn, user.id, idempotency_key, retry):
            return replay
        ap = Approval.model_validate(row["doc"])
        if ap.subject_type is ApprovalSubject.scope:
            raise ApiError(
                ErrorCode.CONFLICT_STATE,
                "use /scopes/{id}/approve or /revoke for scope approvals",
                409,
            )
        if ap.status is not ApprovalStatus.pending:
            raise ApiError(ErrorCode.CONFLICT_STATE, f"approval already {ap.status.value}", 409)
        if ap.requested_by == user.id:
            raise ApiError(ErrorCode.FORBIDDEN, "approver must differ from requester", 403)
        ap = ap.model_copy(
            update={
                "status": body.decision,
                "decided_by": user.id,
                "decided_at": utcnow(),
                "reason": body.reason,
            }
        )
        conn.execute(
            sa.update(approvals)
            .where(approvals.c.id == ap.id)
            .values(status=ap.status.value, doc=ap.model_dump(mode="json"))
        )
        _audit(
            conn,
            org_id,
            ap.project_id,
            user,
            f"approval.{ap.status.value}",
            "approval",
            ap.id,
            approval_subject_type=ap.subject_type.value,
            approval_subject_id=ap.subject_id,
        )
        if row["assessment_id"]:
            jobs.enqueue(
                conn,
                "run_assessment",
                {"assessment_id": row["assessment_id"]},
                dedupe_key=f"asm:{row['assessment_id']}:resume:{ap.id}",
            )
        return idempotent_finish(conn, user.id, idempotency_key, retry, 200, ap)


# ------------------------------------------------------------------ assessments


@router.post("/projects/{project_id}/assessments", status_code=202)
def start_assessment(
    project_id: str,
    body: AssessmentCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    with transaction() as conn:
        user = _user(conn, request)
        require_project(conn, user, project_id, MemberRole.member)
        retry_request = {"project_id": project_id, "body": body.model_dump(mode="json")}
        replay = idempotent_lookup(conn, user.id, idempotency_key, retry_request)
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        limiter.check(f"asm:{user.id}", get_settings().assessment_starts_per_hour, 3600)
        commit_sha = body.commit_sha
        if body.snapshot_id:
            snap = (
                conn.execute(sa.select(snapshots).where(snapshots.c.id == body.snapshot_id))
                .mappings()
                .first()
            )
            if snap is None or snap["repo_id"] != body.repo_id:
                raise not_found("snapshot")
            commit_sha = snap["commit_sha"]
        try:
            a, job_id = create_assessment(
                conn,
                project_id=project_id,
                repo_id=body.repo_id,
                scope_id=body.scope_id,
                commit_sha=commit_sha or "",
                trigger=AssessmentTrigger.manual,
                budget=body.budget,
                created_by=user.id,
                snapshot_id=body.snapshot_id,
            )
        except DomainError as e:
            raise ApiError(e.code, str(e), e.status) from e
        payload = {"assessment": a.model_dump(mode="json"), "job_id": job_id}
        idempotent_store(conn, user.id, idempotency_key, retry_request, 202, payload)
        return JSONResponse(payload, status_code=202)


@router.get("/projects/{project_id}/assessments")
def list_assessments(
    project_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, _user(conn, request), project_id)
        q = sa.select(assessments.c.id, assessments.c.doc).where(
            assessments.c.project_id == project_id
        )
        if cursor:
            q = q.where(assessments.c.id < cursor)
        rows = [
            {"id": r.id, "doc": r.doc}
            for r in conn.execute(q.order_by(assessments.c.id.desc()).limit(limit + 1))
        ]
        page = _page(rows, limit)
        return {"items": [r["doc"] for r in page["items"]], "next_cursor": page["next_cursor"]}


def _assessment_access(
    conn: Any, user: CurrentUser, asm_id: str, role: MemberRole = MemberRole.viewer
) -> dict[str, Any]:
    row = conn.execute(sa.select(assessments).where(assessments.c.id == asm_id)).mappings().first()
    if row is None:
        raise not_found("assessment")
    org_id, _ = require_project(conn, user, row["project_id"], role)
    return {**row, "org_id": org_id}


@router.get("/assessments/{assessment_id}", response_model=AssessmentDetail)
def get_assessment(assessment_id: str, request: Request) -> AssessmentDetail:
    with transaction() as conn:
        row = _assessment_access(conn, _user(conn, request), assessment_id)
        runs = conn.execute(
            sa.select(agent_runs.c.doc)
            .where(agent_runs.c.assessment_id == assessment_id)
            .order_by(agent_runs.c.created_at)
        ).scalars()
        fnds = conn.execute(
            sa.select(findings.c.doc)
            .where(findings.c.assessment_id == assessment_id)
            .order_by(findings.c.created_at)
        ).scalars()
        return AssessmentDetail(
            assessment=Assessment.model_validate(row["doc"]),
            agent_runs=[AgentRun.model_validate(d) for d in runs],
            findings=[Finding.model_validate(d) for d in fnds],
        )


@router.get("/assessments/{assessment_id}/runs")
def list_runs(assessment_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        _assessment_access(conn, _user(conn, request), assessment_id)
        docs = conn.execute(
            sa.select(agent_runs.c.doc)
            .where(agent_runs.c.assessment_id == assessment_id)
            .order_by(agent_runs.c.created_at)
        ).scalars()
        return {"items": list(docs), "next_cursor": None}


def _events_after(assessment_id: str, after: int, limit: int = 500) -> list[dict[str, Any]]:
    with transaction() as conn:
        return list(
            conn.execute(
                sa.select(assessment_events.c.doc)
                .where(
                    assessment_events.c.assessment_id == assessment_id,
                    assessment_events.c.seq > after,
                )
                .order_by(assessment_events.c.seq)
                .limit(limit)
            ).scalars()
        )


def _is_terminal(assessment_id: str) -> bool:
    with transaction() as conn:
        state = conn.execute(
            sa.select(assessments.c.state).where(assessments.c.id == assessment_id)
        ).scalar()
    return state is None or AssessmentState(state) in TERMINAL_STATES


@router.get("/assessments/{assessment_id}/events/log")
def events_log(
    assessment_id: str,
    request: Request,
    after: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=500),
) -> dict[str, Any]:
    """Durable JSON event log (same records as the SSE stream), oldest first.

    ``next_cursor`` is the ``seq`` of the last event returned when more events exist; pass
    it back as ``after`` for the next page. ``null`` means the log was read to its end.
    """
    with transaction() as conn:
        _assessment_access(conn, _user(conn, request), assessment_id)
    rows = _events_after(assessment_id, after, limit + 1)
    items = rows[:limit]
    more = len(rows) > limit and items
    return {"items": items, "next_cursor": str(items[-1]["seq"]) if more else None}


@router.get("/assessments/{assessment_id}/events")
async def stream_events(
    assessment_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after: int | None = Query(default=None, ge=0),
) -> StreamingResponse:
    """SSE stream. Resume with ``Last-Event-ID`` (or ``?after=``); events are replayed from
    the durable log, never synthesized. Closes after the terminal event is delivered."""

    def _check() -> None:
        with transaction() as conn:
            _assessment_access(conn, _user(conn, request), assessment_id)

    await run_in_threadpool(_check)
    try:
        cursor = int(last_event_id) if last_event_id else (after or 0)
    except ValueError:
        cursor = after or 0

    async def gen():  # type: ignore[no-untyped-def]
        nonlocal cursor
        idle = 0.0
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                return
            batch = await run_in_threadpool(_events_after, assessment_id, cursor)
            for doc in batch:
                ev = StreamEvent.model_validate(doc)
                cursor = ev.seq
                yield f"id: {ev.seq}\nevent: {ev.type.value}\ndata: {json.dumps(doc, separators=(',', ':'))}\n\n"
                if ev.type.value in ("assessment.completed", "assessment.failed"):
                    return
            if batch:
                idle = 0.0
                continue
            # Nothing newer than the cursor: if the run is over, the stream is complete.
            if await run_in_threadpool(_is_terminal, assessment_id):
                return
            await asyncio.sleep(0.5)
            idle += 0.5
            if idle >= 15:
                idle = 0.0
                yield ": keepalive\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/assessments/{assessment_id}/cancel")
def cancel_assessment(assessment_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        user = _user(conn, request)
        row = _assessment_access(conn, user, assessment_id, MemberRole.member)
        if AssessmentState(row["state"]) in TERMINAL_STATES:
            raise ApiError(ErrorCode.CONFLICT_STATE, f"assessment already {row['state']}", 409)
        immediate = cancel_queued_assessment(conn, assessment_id)
        if not immediate:
            request_assessment_cancel(conn, assessment_id)
        _audit(
            conn,
            row["org_id"],
            row["project_id"],
            user,
            "assessment.cancel_requested",
            "assessment",
            assessment_id,
            immediate=immediate,
        )
        return {"assessment_id": assessment_id, "cancelled": immediate, "cancel_requested": True}


@router.get("/assessments/{assessment_id}/rules-of-engagement")
def rules_of_engagement(assessment_id: str, request: Request) -> dict[str, Any]:
    """The RoE document issued for this assessment (404 when none was issued), served from the
    artifact named by the hash-chained ``roe.recorded`` audit event; its digest is re-checked."""
    import hashlib

    with transaction() as conn:
        row = _assessment_access(conn, _user(conn, request), assessment_id)
        event = conn.execute(
            sa.select(audit_events.c.doc)
            .where(
                audit_events.c.project_id == row["project_id"],
                audit_events.c.doc["action"].astext == "roe.recorded",
                audit_events.c.doc["subject_id"].astext == assessment_id,
            )
            .order_by(audit_events.c.seq.desc())
            .limit(1)
        ).scalar()
        if event is None:
            raise not_found("rules of engagement")
        details = event.get("details") or {}
        _, data = ArtifactStore().get(conn, details["artifact_id"])
    doc = json.loads(data)
    body = {k: v for k, v in doc.items() if k != "digest"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    if digest != doc.get("digest") or digest != details.get("digest"):
        raise ApiError(ErrorCode.INTERNAL, "stored rules of engagement failed integrity check", 500)
    return doc


@router.get("/assessments/{assessment_id}/report", response_model=AssessmentReport)
def assessment_report(assessment_id: str, request: Request) -> dict[str, Any]:
    """Report assembled only from persisted records (no generated prose)."""
    with transaction() as conn:
        row = _assessment_access(conn, _user(conn, request), assessment_id)
        fnds = [
            Finding.model_validate(d)
            for d in conn.execute(
                sa.select(findings.c.doc).where(findings.c.assessment_id == assessment_id)
            ).scalars()
        ]
        out = []
        for f in fnds:
            pats = [
                PatchCandidate.model_validate(d)
                for d in conn.execute(
                    sa.select(patch_candidates.c.doc).where(patch_candidates.c.finding_id == f.id)
                ).scalars()
            ]
            vers = {
                d["patch_id"]: d
                for d in conn.execute(
                    sa.select(verification_results.c.doc).where(
                        verification_results.c.patch_id.in_([p.id for p in pats] or [""])
                    )
                ).scalars()
            }
            out.append(
                {
                    "finding": f.model_dump(mode="json"),
                    "patches": [
                        {"patch": p.model_dump(mode="json"), "verification": vers.get(p.id)}
                        for p in pats
                    ],
                }
            )
        runs = list(
            conn.execute(
                sa.select(agent_runs.c.doc).where(agent_runs.c.assessment_id == assessment_id)
            ).scalars()
        )
        report = {
            "assessment": row["doc"],
            "agent_runs": runs,
            "findings": out,
            "generated_at": utcnow().isoformat(),
            "source": "persisted records",
            "standards": {
                "classification_only": True,
                "findings": [
                    {
                        "finding_id": f.id,
                        "standards": f.standards.model_dump(mode="json") if f.standards else None,
                    }
                    for f in fnds
                ],
                "hardening": list(
                    conn.execute(
                        sa.select(finding_records.c.doc).where(
                            finding_records.c.finding_id.in_([f.id for f in fnds] or [""]),
                            finding_records.c.record_type == "hardening",
                        )
                    ).scalars()
                ),
                "unscored_reason": "CVSS requires a complete explicit metric assessment; absent scores are not zero.",
            },
        }
        return AssessmentReport.model_validate(report).model_dump(mode="json")


# ----------------------------------------------------------- findings & patches


@router.get("/projects/{project_id}/findings")
def list_findings(
    project_id: str, request: Request, status: FindingStatus | None = None, kind: str | None = None
) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, _user(conn, request), project_id)
        q = sa.select(findings.c.doc).where(findings.c.project_id == project_id)
        if status:
            q = q.where(findings.c.status == status.value)
        if kind:
            q = q.where(findings.c.kind == kind)
        return {
            "items": list(conn.execute(q.order_by(findings.c.created_at.desc())).scalars()),
            "next_cursor": None,
        }


def _finding_access(
    conn: Any, user: CurrentUser, finding_id: str, role: MemberRole = MemberRole.viewer
) -> dict[str, Any]:
    row = conn.execute(sa.select(findings).where(findings.c.id == finding_id)).mappings().first()
    if row is None:
        raise not_found("finding")
    org_id, _ = require_project(conn, user, row["project_id"], role)
    return {**row, "org_id": org_id}


@router.get("/findings/{finding_id}", response_model=FindingDetail)
def get_finding(finding_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        row = _finding_access(conn, _user(conn, request), finding_id)
        recs = conn.execute(
            sa.select(finding_records.c.record_type, finding_records.c.doc)
            .where(finding_records.c.finding_id == finding_id)
            .order_by(finding_records.c.created_at)
        ).all()
        return FindingDetail.model_validate(
            {"finding": row["doc"], "records": [{"type": t, "record": d} for t, d in recs]}
        ).model_dump(mode="json")


@router.get("/findings/{finding_id}/patches")
def get_patches(finding_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        _finding_access(conn, _user(conn, request), finding_id)
        items = []
        for d in conn.execute(
            sa.select(patch_candidates.c.doc)
            .where(patch_candidates.c.finding_id == finding_id)
            .order_by(patch_candidates.c.created_at)
        ).scalars():
            v = conn.execute(
                sa.select(verification_results.c.doc)
                .where(verification_results.c.patch_id == d["id"])
                .order_by(verification_results.c.created_at.desc())
            ).scalar()
            items.append(
                PatchView(
                    patch=PatchCandidate.model_validate(d),
                    verification=VerificationResult.model_validate(v) if v else None,
                ).model_dump(mode="json")
            )
        return {"items": items, "next_cursor": None}


@router.post("/findings/{finding_id}/accept-risk")
def accept_risk(finding_id: str, body: AcceptRisk, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        user = _user(conn, request)
        row = _finding_access(conn, user, finding_id, MemberRole.owner)
        f = Finding.model_validate(row["doc"])
        if f.status in (FindingStatus.closed, FindingStatus.accepted_risk):
            raise ApiError(ErrorCode.CONFLICT_STATE, f"finding already {f.status.value}", 409)
        now = utcnow()
        f = f.model_copy(
            update={
                "status": FindingStatus.accepted_risk,
                "updated_at": now,
                "history": [
                    *f.history,
                    StatusChange(
                        from_status=f.status,
                        to_status=FindingStatus.accepted_risk,
                        at=now,
                        actor=f"user:{user.id}",
                        reason=body.reason,
                    ),
                ],
            }
        )
        conn.execute(
            sa.update(findings)
            .where(findings.c.id == finding_id)
            .values(status=f.status.value, doc=f.model_dump(mode="json"))
        )
        _audit(
            conn,
            row["org_id"],
            row["project_id"],
            user,
            "finding.accepted_risk",
            "finding",
            finding_id,
            reason=body.reason,
        )
        return f.model_dump(mode="json")


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, request: Request) -> Response:
    with transaction() as conn:
        user = _user(conn, request)
        row = (
            conn.execute(sa.select(artifacts).where(artifacts.c.id == artifact_id))
            .mappings()
            .first()
        )
        if row is None or row["project_id"] is None:
            raise not_found("artifact")
        require_project(conn, user, row["project_id"])
        meta, data = ArtifactStore().get(conn, artifact_id)
    return Response(
        data,
        media_type=meta["media_type"],
        headers={
            "X-Artifact-SHA256": meta["sha256"],
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{artifact_id}"',
        },
    )


# ------------------------------------------------------------- monitoring & RL


@router.get("/projects/{project_id}/events")
def monitoring_feed(
    project_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> dict[str, Any]:
    """Newest first; ``next_cursor`` is set when older events exist (pass it as ``cursor``)."""
    t = monitoring_events
    with transaction() as conn:
        require_project(conn, _user(conn, request), project_id)
        rows = conn.execute(
            sa.select(t.c.created_at, t.c.id, t.c.doc)
            .where(t.c.project_id == project_id, *_keyset_where(t, cursor))
            .order_by(t.c.created_at.desc(), t.c.id.desc())
            .limit(limit + 1)
        ).all()
        docs, nxt = _keyset_page(rows, limit)
        return {
            "items": [MonitoringEvent.model_validate(d).model_dump(mode="json") for d in docs],
            "next_cursor": nxt,
        }


@router.get("/projects/{project_id}/reassessments")
def reassessments(project_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, _user(conn, request), project_id)
        docs = conn.execute(
            sa.select(reassessment_requests.c.doc)
            .where(reassessment_requests.c.project_id == project_id)
            .order_by(reassessment_requests.c.created_at.desc())
        ).scalars()
        return {
            "items": [ReassessmentRequest.model_validate(d).model_dump(mode="json") for d in docs],
            "next_cursor": None,
        }


@router.get("/projects/{project_id}/defense-recommendations")
def list_recommendations(
    project_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> dict[str, Any]:
    """Newest first; ``next_cursor`` is set when older recommendations exist."""
    t = defense_recommendations
    with transaction() as conn:
        require_project(conn, _user(conn, request), project_id)
        rows = conn.execute(
            sa.select(t.c.created_at, t.c.id, t.c.doc)
            .where(t.c.project_id == project_id, *_keyset_where(t, cursor))
            .order_by(t.c.created_at.desc(), t.c.id.desc())
            .limit(limit + 1)
        ).all()
        docs, nxt = _keyset_page(rows, limit)
        return {
            "items": [
                DefenseRecommendation.model_validate(d).model_dump(mode="json") for d in docs
            ],
            "next_cursor": nxt,
            "label": "simulation-derived decision support; never executed automatically",
        }


@router.get("/defense-recommendations/{rec_id}/explanation", response_model=RLExplanation)
def recommendation_explanation(rec_id: str, request: Request) -> RLExplanation:
    with transaction() as conn:
        user = _user(conn, request)
        row = (
            conn.execute(
                sa.select(defense_recommendations).where(defense_recommendations.c.id == rec_id)
            )
            .mappings()
            .first()
        )
        if row is None or row["project_id"] is None:
            raise not_found("recommendation")
        require_project(conn, user, row["project_id"])
        if row["explanation"] is None:
            raise not_found("explanation")
        return RLExplanation.model_validate(row["explanation"])


class RecommendRequest(StrictModel):
    facts: PlatformFacts | None = None


@router.post("/projects/{project_id}/defense-recommendations", status_code=202)
def request_recommendation(
    project_id: str, body: RecommendRequest, request: Request
) -> dict[str, Any]:
    with transaction() as conn:
        user = _user(conn, request)
        require_project(conn, user, project_id, MemberRole.member)
        job_id, _ = jobs.enqueue(
            conn,
            "rl_recommend",
            {
                "project_id": project_id,
                "facts": body.facts.model_dump(mode="json") if body.facts else None,
            },
        )
        return {"job_id": job_id}


class RLEvaluationImport(StrictModel):
    protocol_version: ShortText
    checkpoint_ids: list[str] = Field(default_factory=list)
    summary: dict[str, Any]


@router.post("/projects/{project_id}/rl-evaluations", status_code=201, response_model=RLEvaluation)
def import_rl_evaluation(
    project_id: str, body: RLEvaluationImport, request: Request
) -> RLEvaluation:
    """Persist a protocol-17 eval_summary.json verbatim (as produced by rl_defense.evaluate)."""
    with transaction() as conn:
        user = _user(conn, request)
        org_id, _ = require_project(conn, user, project_id, MemberRole.owner)
        if len(json.dumps(body.summary)) > 5_000_000:
            raise ApiError(ErrorCode.PAYLOAD_TOO_LARGE, "summary too large", 413)
        ref = ArtifactStore().put_json(
            conn, body.summary, kind=ArtifactKind.rl_eval_summary, project_id=project_id
        )
        ev = RLEvaluation(
            id=new_id(IdPrefix.rle),
            protocol_version=body.protocol_version,
            checkpoint_ids=body.checkpoint_ids,
            summary_artifact_id=ref.id,
            summary_sha256=ref.sha256,
            created_at=utcnow(),
        )
        conn.execute(
            sa.insert(rl_evaluations).values(
                id=ev.id, doc={**ev.model_dump(mode="json"), "project_id": project_id}
            )
        )
        _audit(
            conn,
            org_id,
            project_id,
            user,
            "rl.evaluation_imported",
            "rl_evaluation",
            ev.id,
            sha256=ref.sha256,
        )
        return ev


@router.get("/projects/{project_id}/rl-evaluations")
def list_rl_evaluations(project_id: str, request: Request) -> dict[str, Any]:
    with transaction() as conn:
        require_project(conn, _user(conn, request), project_id)
        docs = conn.execute(
            sa.select(rl_evaluations.c.doc)
            .where(rl_evaluations.c.doc["project_id"].astext == project_id)
            .order_by(rl_evaluations.c.created_at.desc())
        ).scalars()
        return {
            "items": [
                RLEvaluation.model_validate(
                    {k: v for k, v in d.items() if k != "project_id"}
                ).model_dump(mode="json")
                for d in docs
            ],
            "next_cursor": None,
        }


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    from backend.db import jobs as jobs_t

    with transaction() as conn:
        user = _user(conn, request)
        row = conn.execute(sa.select(jobs_t).where(jobs_t.c.id == job_id)).mappings().first()
        # Assessment jobs (including approval resumes) retain only assessment_id
        # in their payload; job_project resolves them through the persisted assessment.
        pid = job_project(conn, row) if row else None
        if row is None or not pid:
            raise not_found("job")
        require_project(conn, user, pid)
        return {
            "id": row["id"],
            "kind": row["kind"],
            "status": row["status"],
            "attempts": row["attempts"],
            "result": row["result"],
            "last_error": {k: v for k, v in (row["last_error"] or {}).items() if k != "trace"}
            or None,
            "error_category": row["error_category"],
            "deadline_at": row["deadline_at"].isoformat() if row["deadline_at"] else None,
            "effect_state": row["effect_state"],
            "recovery": row["recovery"],
        }


@router.get("/audit")
def audit_log(
    request: Request,
    project_id: str,
    limit: int = Query(500, ge=1, le=500),
    cursor: str | None = Query(None, pattern=r"^\d{1,18}$"),
) -> dict[str, Any]:
    """Newest first. ``next_cursor`` is the last returned ``seq`` when older events exist;
    pass it as ``cursor`` to continue. Chain verification always covers the whole org chain."""
    with transaction() as conn:
        user = _user(conn, request)
        org_id, _ = require_project(conn, user, project_id, MemberRole.owner)
        q = sa.select(audit_events.c.seq, audit_events.c.doc).where(
            audit_events.c.project_id == project_id
        )
        if cursor:
            q = q.where(audit_events.c.seq < int(cursor))
        rows = conn.execute(q.order_by(audit_events.c.seq.desc()).limit(limit + 1)).all()
        page = rows[:limit]
        ok, n = audit.verify_chain(conn, org_id)
        return {
            "items": [AuditEvent.model_validate(r.doc).model_dump(mode="json") for r in page],
            "chain_verified": ok,
            "chain_events_checked": n,
            "next_cursor": str(page[-1].seq) if len(rows) > limit and page else None,
        }
