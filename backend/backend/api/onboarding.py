"""Customer onboarding: computed journey progress and the authorized test event.

``GET /projects/{id}/onboarding`` derives every step from the tables the product
already writes — connected repositories, snapshots, scopes, staging apps, deploy
mappings, monitoring settings, ingest windows and investigation records — so the
wizard cannot claim progress that did not happen. No state of its own.

``POST /projects/{id}/onboarding/test-event`` sends synthetic counters through
the real ``owned_staging`` ingest channel for a registered application. It
requires an active ingest token bound to that app (the same credential the
customer's reporter would use); it never executes enforcement.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request

from backend import audit
from backend.api.deps import (
    ApiError,
    limiter,
    not_found,
    require_project,
    require_user,
)
from backend.api.runtime import ingest
from backend.db import (
    authorized_scopes,
    connected_applications,
    deploy_commit_mappings,
    incidents,
    ingest_tokens,
    live_policy_decisions,
    monitoring_events,
    repositories,
    runtime_windows,
    snapshots,
    transaction,
)
from shared.audit import ActorType
from shared.base import utcnow
from shared.errors import ErrorCode
from shared.intelligence import SnapshotStatus
from shared.monitoring import RouteWindow, RuntimeSignalBatch
from shared.onboarding import (
    OnboardingStatus,
    OnboardingStep,
    OnboardingStepId,
    OnboardingStepState,
    OnboardingTestEventCreate,
    OnboardingTestEventResult,
)
from shared.scope import ScopeStatus
from shared.tenancy import MemberRole

router = APIRouter(tags=["onboarding"])


def _active_apps(conn: sa.Connection, project_id: str) -> list[dict[str, Any]]:
    rows = (
        conn.execute(
            sa.select(connected_applications.c.id, connected_applications.c.doc)
            .where(connected_applications.c.project_id == project_id)
            .order_by(connected_applications.c.created_at)
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows if not (r["doc"] or {}).get("disconnected_at")]


def _app_tokens(conn: sa.Connection, app_id: str) -> list[str]:
    return list(
        conn.execute(
            sa.select(ingest_tokens.c.id).where(
                ingest_tokens.c.application_id == app_id,
                ingest_tokens.c.revoked_at.is_(None),
            )
        ).scalars()
    )


def _snapshot_step(conn: sa.Connection, repo_ids: list[str]) -> OnboardingStep:
    rows = (
        conn.execute(
            sa.select(snapshots.c.id, snapshots.c.status, snapshots.c.commit_sha)
            .where(snapshots.c.repo_id.in_(repo_ids))
            .order_by(snapshots.c.created_at.desc())
        )
        .mappings()
        .all()
    )
    latest = rows[0] if rows else None
    ready = next((r for r in rows if r["status"] == SnapshotStatus.ready.value), None)
    if ready is not None:
        return OnboardingStep(
            id=OnboardingStepId.snapshot,
            state=OnboardingStepState.done,
            detail=f"Snapshot of commit {ready['commit_sha'][:7]} is indexed and readable.",
            subject_id=ready["id"],
        )
    if latest is not None and latest["status"] in (
        SnapshotStatus.pending.value,
        SnapshotStatus.indexing.value,
    ):
        return OnboardingStep(
            id=OnboardingStepId.snapshot,
            state=OnboardingStepState.in_progress,
            detail=f"Indexing commit {latest['commit_sha'][:7]}; this can take a few minutes.",
            subject_id=latest["id"],
        )
    if latest is not None and latest["status"] == SnapshotStatus.failed.value:
        return OnboardingStep(
            id=OnboardingStepId.snapshot,
            state=OnboardingStepState.failed,
            detail=f"Indexing commit {latest['commit_sha'][:7]} failed. Request a new snapshot to retry.",
            subject_id=latest["id"],
        )
    return OnboardingStep(
        id=OnboardingStepId.snapshot,
        state=OnboardingStepState.todo,
        detail="No snapshot yet. Request one so Ari reads the exact code you deploy.",
    )


def _status(
    conn: sa.Connection, project_id: str, org_id: str, project_name: str
) -> OnboardingStatus:
    # Full rows so repositories.disconnected_at (security-hardening PR #4's canonical
    # park flag) is honored automatically once it lands: a disconnected repository
    # drops out of every "connected" step below without further code changes.
    repos = (
        conn.execute(sa.select(repositories).where(repositories.c.project_id == project_id))
        .mappings()
        .all()
    )
    repos = [r for r in repos if r.get("disconnected_at") is None]
    scope_docs = (
        conn.execute(
            sa.select(authorized_scopes.c.doc).where(authorized_scopes.c.project_id == project_id)
        )
        .scalars()
        .all()
    )
    apps = _active_apps(conn, project_id)
    token_app_ids = {
        row.application_id
        for row in conn.execute(
            sa.select(ingest_tokens.c.application_id).where(
                ingest_tokens.c.project_id == project_id,
                ingest_tokens.c.revoked_at.is_(None),
                ingest_tokens.c.application_id.isnot(None),
            )
        ).mappings()
    }
    mapping = conn.execute(
        sa.select(deploy_commit_mappings.c.id).where(
            deploy_commit_mappings.c.project_id == project_id,
            deploy_commit_mappings.c.revoked_at.is_(None),
        )
    ).first()
    monitoring_enabled = bool(
        conn.execute(
            sa.select(repositories.c.id)
            .where(
                repositories.c.project_id == project_id,
                repositories.c.automatic_assessments.is_(True),
            )
            .limit(1)
        ).first()
    )
    from backend.services import project_settings

    policy_enabled = project_settings(conn, project_id).monitoring_policy.enabled
    windows_seen = conn.execute(
        sa.select(runtime_windows.c.id)
        .where(
            runtime_windows.c.project_id == project_id,
            runtime_windows.c.source == "owned_staging",
        )
        .limit(1)
    ).first()
    evidence_seen = any(
        conn.execute(sa.select(table.c.id).where(table.c.project_id == project_id).limit(1)).first()
        for table in (monitoring_events, live_policy_decisions, incidents)
    )

    steps: list[OnboardingStep] = [
        OnboardingStep(
            id=OnboardingStepId.sign_in,
            state=OnboardingStepState.done,
            detail="Signed in; session authorized.",
        ),
        OnboardingStep(
            id=OnboardingStepId.organization,
            state=OnboardingStepState.done,
            detail=f"Organization and project '{project_name}' are set up.",
            subject_id=project_id,
        ),
    ]

    repo_done = bool(repos)
    installed = [r for r in repos if r["installation_id"] is not None]
    steps.append(
        OnboardingStep(
            id=OnboardingStepId.repository,
            state=OnboardingStepState.done if repo_done else OnboardingStepState.todo,
            detail=(
                f"{repos[0]['full_name']} is connected."
                if repo_done
                else "Connect a repository you are allowed to give Ari access to."
            ),
            subject_id=repos[0]["id"] if repos else None,
        )
    )

    if installed:
        gh = OnboardingStep(
            id=OnboardingStepId.github_access,
            state=OnboardingStepState.done,
            detail=(
                "GitHub App installed: Ari may read repository contents and metadata. "
                "Write access is used only to open a fix pull request when you choose PR delivery."
            ),
        )
    elif repo_done:
        gh = OnboardingStep(
            id=OnboardingStepId.github_access,
            state=OnboardingStepState.todo,
            detail=(
                "Repository is connected by name only. Install the Ari GitHub App on the "
                "organization so Ari can read commits; it requests contents:read and metadata:read."
            ),
        )
    else:
        gh = OnboardingStep(
            id=OnboardingStepId.github_access,
            state=OnboardingStepState.blocked,
            detail="Connect a repository first; the App grants Ari read-only contents and metadata.",
        )
    steps.append(gh)

    repo_ids = [r["id"] for r in repos]
    steps.append(
        _snapshot_step(conn, repo_ids)
        if repo_done
        else OnboardingStep(
            id=OnboardingStepId.snapshot,
            state=OnboardingStepState.blocked,
            detail="Connect a repository first.",
        )
    )

    statuses = [d.get("status") for d in scope_docs]
    if ScopeStatus.active.value in statuses:
        steps.append(
            OnboardingStep(
                id=OnboardingStepId.scope,
                state=OnboardingStepState.done,
                detail="An authorized scope is active; testing stays inside it.",
            )
        )
    elif ScopeStatus.pending.value in statuses:
        steps.append(
            OnboardingStep(
                id=OnboardingStepId.scope,
                state=OnboardingStepState.blocked,
                detail="Scope approval is pending; another org member must approve it.",
            )
        )
    elif repo_done:
        steps.append(
            OnboardingStep(
                id=OnboardingStepId.scope,
                state=OnboardingStepState.todo,
                detail="Request an authorized scope so testing has an explicit boundary.",
            )
        )
    else:
        steps.append(
            OnboardingStep(
                id=OnboardingStepId.scope,
                state=OnboardingStepState.blocked,
                detail="Connect a repository first.",
            )
        )

    steps.append(
        OnboardingStep(
            id=OnboardingStepId.application,
            state=OnboardingStepState.done if apps else OnboardingStepState.todo,
            detail=(
                f"Staging app '{apps[0]['doc']['name']}' is registered (shadow mode, never production)."
                if apps
                else "Register a staging application you own. Only sandbox and owned-test are allowed."
            ),
            subject_id=apps[0]["id"] if apps else None,
        )
    )

    if mapping is not None:
        steps.append(
            OnboardingStep(
                id=OnboardingStepId.deploy_mapping,
                state=OnboardingStepState.done,
                detail="A deployment-to-commit mapping is recorded; runtime events can be tied to code.",
                subject_id=mapping[0],
            )
        )
    elif apps:
        steps.append(
            OnboardingStep(
                id=OnboardingStepId.deploy_mapping,
                state=OnboardingStepState.todo,
                detail="Attest which commit is deployed to your staging app so events map to code.",
            )
        )
    else:
        steps.append(
            OnboardingStep(
                id=OnboardingStepId.deploy_mapping,
                state=OnboardingStepState.blocked,
                detail="Register a staging application first.",
            )
        )

    shadow_ready = any(a["id"] in token_app_ids for a in apps)
    mon_on = monitoring_enabled or policy_enabled
    steps.append(
        OnboardingStep(
            id=OnboardingStepId.monitoring,
            state=OnboardingStepState.done
            if (mon_on or shadow_ready)
            else OnboardingStepState.todo,
            detail=(
                "Read-only monitoring is enabled."
                if mon_on
                else (
                    "Shadow investigation channel is live (ingest token active; read-only, nothing executes)."
                    if shadow_ready
                    else "Enable push-triggered monitoring or mint an ingest token for shadow investigation."
                )
            ),
        )
    )

    if windows_seen is not None:
        steps.append(
            OnboardingStep(
                id=OnboardingStepId.test_event,
                state=OnboardingStepState.done,
                detail="A runtime event was ingested through the owned-staging channel.",
            )
        )
    elif apps and shadow_ready:
        steps.append(
            OnboardingStep(
                id=OnboardingStepId.test_event,
                state=OnboardingStepState.todo,
                detail="Send an authorized test event to prove the ingest-to-investigation path.",
            )
        )
    else:
        steps.append(
            OnboardingStep(
                id=OnboardingStepId.test_event,
                state=OnboardingStepState.blocked,
                detail="Register a staging application and mint its ingest token first.",
            )
        )

    steps.append(
        OnboardingStep(
            id=OnboardingStepId.evidence,
            state=OnboardingStepState.done if evidence_seen else OnboardingStepState.todo,
            detail=(
                "Investigation records exist: open Incidents or Live Defense to review them."
                if evidence_seen
                else "No investigation records yet; they appear after monitoring or a test event."
            ),
        )
    )

    return OnboardingStatus(
        project_id=project_id,
        org_id=org_id,
        project_name=project_name,
        steps=steps,
        complete=all(s.state == OnboardingStepState.done for s in steps),
        checked_at=utcnow(),
    )


@router.get("/projects/{project_id}/onboarding", response_model=OnboardingStatus)
def onboarding_status(project_id: str, request: Request) -> OnboardingStatus:
    from backend.db import projects

    with transaction() as conn:
        user = require_user(conn, request)
        org_id, _ = require_project(conn, user, project_id)
        name = conn.execute(
            sa.select(projects.c.name).where(projects.c.id == project_id)
        ).scalar_one()
        return _status(conn, project_id, org_id, name)


@router.post(
    "/projects/{project_id}/onboarding/test-event",
    status_code=202,
    response_model=OnboardingTestEventResult,
)
def onboarding_test_event(
    project_id: str, body: OnboardingTestEventCreate, request: Request
) -> OnboardingTestEventResult:
    """Authorized test event: the project owner injects a synthetic auth-failure
    spike through the registered application's real ingest channel. It is scored,
    recorded, and shadow-decided like any other owned_staging window. Nothing
    executes on the customer's system."""
    with transaction() as conn:
        user = require_user(conn, request)
        org_id, _ = require_project(conn, user, project_id, MemberRole.owner)
        limiter.check(f"onboarding_test:{project_id}", 20, 3600)
        apps = _active_apps(conn, project_id)
        if body.application_id is not None:
            app = next((a for a in apps if a["id"] == body.application_id), None)
            if app is None:
                raise not_found("connected application")
        elif len(apps) == 1:
            app = apps[0]
        elif not apps:
            raise ApiError(
                ErrorCode.CONFLICT_STATE,
                "register a staging application before sending a test event",
                409,
            )
        else:
            # Several apps: the test event can only travel a channel that exists,
            # so prefer the (usually just-onboarded) app that has a live token.
            eligible = [a for a in apps if _app_tokens(conn, a["id"])]
            if len(eligible) == 1:
                app = eligible[0]
            elif not eligible:
                raise ApiError(
                    ErrorCode.CONFLICT_STATE,
                    "mint an ingest token for the application before sending a test event",
                    409,
                )
            else:
                raise ApiError(
                    ErrorCode.VALIDATION_FAILED,
                    "application_id is required when several applications have ingest tokens",
                    422,
                )
        token_id = next(iter(_app_tokens(conn, app["id"])), None)
        if token_id is None:
            raise ApiError(
                ErrorCode.CONFLICT_STATE,
                "mint an ingest token for the application before sending a test event",
                409,
            )
        app_id = app["id"]

    if body.auth_failures > body.status_4xx or body.status_4xx > body.requests:
        raise ApiError(
            ErrorCode.VALIDATION_FAILED,
            "auth_failures must be <= status_4xx and status_4xx must be <= requests",
            422,
        )
    window_end = utcnow().replace(microsecond=0)
    batch = RuntimeSignalBatch(
        source="owned_staging",
        window_start=window_end - timedelta(seconds=60),
        window_end=window_end,
        routes=[
            RouteWindow(
                route=body.route,
                requests=body.requests,
                status_4xx=body.status_4xx,
                auth_failures=body.auth_failures,
            )
        ],
    )
    # test_event marks the produced monitoring event unmistakably as an authorized
    # test in customer-facing views while it traverses the real authenticated channel.
    ack = ingest(project_id, token_id, batch, app_id, test_event=True)
    with transaction() as conn:
        audit.record(
            conn,
            org_id=org_id,
            project_id=project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="onboarding.test_event",
            subject_type="connected_application",
            subject_id=app_id,
            details={
                "route": body.route,
                "synthetic_counters": True,
                "anomalies": ack.anomalies,
            },
        )
    return OnboardingTestEventResult(
        application_id=app_id,
        route=body.route,
        anomalies=ack.anomalies,
        monitoring_event_ids=ack.monitoring_event_ids,
        window_end=window_end,
    )
