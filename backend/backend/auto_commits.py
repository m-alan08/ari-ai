"""Per-commit scheduling, gated by signed push sender and GitHub commit attribution."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import audit, jobs
from backend.db import (
    assessments,
    monitoring_events,
    projects,
    reassessment_requests,
    repositories,
    transaction,
)
from backend.github import GitHubApp
from backend.services import (
    active_scope_for_repo,
    capped_budget,
    create_assessment,
    project_org,
    project_settings,
)
from backend.settings import get_settings
from backend.worker import JobContext, PermanentJobError, handler
from shared.assessment import AssessmentTrigger
from shared.audit import ActorType
from shared.base import utcnow
from shared.errors import ErrorCode
from shared.ids import IdPrefix, new_id
from shared.monitoring import (
    CommitRange,
    EventSource,
    EventStatus,
    EventType,
    MonitoringEvent,
    ReassessmentRequest,
    ReassessmentScope,
    ReassessmentStatus,
)

_CLIENT: tuple[Any, Any, Any] | None = None


def github_client() -> GitHubApp:
    """Reuse short-lived installation tokens while worker settings remain unchanged."""
    global _CLIENT
    settings = get_settings()
    if _CLIENT is None or _CLIENT[0] is not settings or _CLIENT[1] is not GitHubApp:
        _CLIENT = (settings, GitHubApp, GitHubApp())
    return _CLIENT[2]


def authors() -> set[str]:
    return {
        s.strip().lower() for s in get_settings().github_auto_commit_authors.split(",") if s.strip()
    }


def schedule(
    conn: Connection, repo: Any, delivery: Any, payload: dict[str, Any], change: Any
) -> dict[str, Any]:
    if repo["disconnected_at"] is not None:
        return {"ignored": "repository disconnected"}
    if not repo["automatic_assessments"]:
        return {"ignored": "repository automatic assessments disabled"}
    allowed = authors()
    sender = (change.sender or "").lower()
    if not sender or ("*" not in allowed and sender not in allowed):
        return {"ignored": "push sender is outside the automatic rollout"}
    installation = repo["installation_id"]
    if not installation or (payload.get("installation") or {}).get("id") != installation:
        return {"ignored": "push installation does not match connected repository"}
    commits = payload.get("commits") or []
    if change.event == "pull_request":
        commits = [{"id": change.head_sha}]
    shas = [c.get("id") for c in commits if isinstance(c, dict)]
    # Compare is paginated; webhook payloads can truncate large pushes.
    if change.event == "push" and change.base_sha and (len(commits) >= 20 or not commits):
        shas = github_client().compare_commits(
            installation, repo["full_name"], change.base_sha, change.head_sha
        )
    elif not change.base_sha and len(commits) >= 2048:
        raise PermanentJobError(
            ErrorCode.VALIDATION_FAILED,
            "new-branch push exceeds GitHub payload limit; cannot enumerate safely",
        )
    shas = [*shas, change.head_sha]
    if any(
        not isinstance(s, str) or not re.fullmatch(r"[0-9a-f]{40}", s) or s == "0" * 40
        for s in shas
    ):
        raise PermanentJobError(ErrorCode.VALIDATION_FAILED, "push contains an invalid commit SHA")
    queued = []
    for sha in dict.fromkeys(shas):
        job_id, _ = jobs.enqueue(
            conn,
            "assess_github_commit",
            {
                "repo_id": repo["id"],
                "sha": sha,
                "sender": sender,
                "installation_id": installation,
                "payload_artifact_id": delivery["payload_artifact_id"],
                "ref": change.ref,
                "event_type": change.event,
                "pr_number": change.pr_number,
                "occurred_at": change.occurred_at.isoformat(),
                "received_at": delivery["received_at"].isoformat(),
            },
            dedupe_key=f"github:commit:{repo['id']}:{sha}",
            priority=60,
        )
        queued.append(job_id)
    return {"commit_jobs": queued}


def policy_exit(
    payload: dict[str, Any], reason: str, conn: Connection | None = None
) -> dict[str, Any]:
    """A policy stop finalizes previously deferred/blocked work instead of stranding it."""
    if conn is None:
        with transaction() as connection:
            return policy_exit(payload, reason, connection)
    rows = (
        conn.execute(
            sa.select(reassessment_requests.c.doc)
            .where(
                reassessment_requests.c.repo_id == payload["repo_id"],
                reassessment_requests.c.doc["head_sha"].astext == payload["sha"],
                reassessment_requests.c.status.in_(
                    [ReassessmentStatus.queued.value, ReassessmentStatus.blocked_no_scope.value]
                ),
            )
            .with_for_update()
        )
        .scalars()
        .all()
    )
    for doc in rows:
        request = ReassessmentRequest.model_validate(doc).model_copy(
            update={"status": ReassessmentStatus.skipped, "reason": reason}
        )
        conn.execute(
            sa.update(reassessment_requests)
            .where(reassessment_requests.c.id == request.id)
            .values(status=request.status.value, doc=request.model_dump(mode="json"))
        )
        for event_id in request.event_ids:
            event_doc = conn.execute(
                sa.select(monitoring_events.c.doc).where(monitoring_events.c.id == event_id)
            ).scalar_one()
            event_doc = {**event_doc, "status": EventStatus.ignored.value, "status_reason": reason}
            conn.execute(
                sa.update(monitoring_events)
                .where(monitoring_events.c.id == event_id)
                .values(status=EventStatus.ignored.value, doc=event_doc)
            )
            audit.record(
                conn,
                org_id=project_org(conn, request.project_id),
                project_id=request.project_id,
                actor_type=ActorType.system,
                actor_id="monitoring",
                action="monitoring.event_processed",
                subject_type="monitoring_event",
                subject_id=event_id,
                details={"head_sha": payload["sha"], "status": "skipped", "reason": reason},
            )
    return {"ignored": reason}


@handler("assess_github_commit")
def assess_commit(ctx: JobContext) -> dict[str, Any]:
    p = ctx.job.payload
    allowed = authors()
    if not allowed:
        return policy_exit(p, "automatic commit rollout disabled")
    sender = p.get("sender", "").lower()
    if not sender or ("*" not in allowed and sender not in allowed):
        return policy_exit(p, "push sender is outside the automatic rollout")
    with transaction() as conn:
        repo = (
            conn.execute(sa.select(repositories).where(repositories.c.id == p["repo_id"]))
            .mappings()
            .one()
        )
    if repo["installation_id"] != p["installation_id"]:
        return policy_exit(p, "repository installation changed")
    if repo["disconnected_at"] is not None:
        return policy_exit(p, "repository disconnected")
    commit = github_client().commit(repo["installation_id"], repo["full_name"], p["sha"])
    if commit.get("sha") != p["sha"]:
        raise ValueError("GitHub returned a different commit")
    login = (commit.get("author") or {}).get("login", "").lower()
    # "*" admits any author, including commits whose email maps to no GitHub account.
    if "*" not in allowed and (not login or login not in allowed):
        return policy_exit(p, "commit author is outside the automatic rollout")
    ctx.check_cancelled()
    with transaction() as conn:
        # Serialize quota reservations across all repositories in a project.
        conn.execute(
            sa.select(projects.c.id).where(projects.c.id == repo["project_id"]).with_for_update()
        ).one()
        # Serialize retries, duplicate deliveries, and scope activation for this repo.
        repo = (
            conn.execute(
                sa.select(repositories).where(repositories.c.id == p["repo_id"]).with_for_update()
            )
            .mappings()
            .one()
        )
        if repo["installation_id"] != p["installation_id"]:
            return policy_exit(p, "repository installation changed", conn)
        if not repo["automatic_assessments"]:
            return policy_exit(p, "repository automatic assessments disabled", conn)
        settings = project_settings(conn, repo["project_id"])
        if not settings.monitoring_policy.enabled:
            return policy_exit(p, "monitoring disabled", conn)
        key = f"github:commit:{repo['id']}:{p['sha']}"
        existing = (
            conn.execute(sa.select(monitoring_events).where(monitoring_events.c.dedupe_key == key))
            .mappings()
            .first()
        )
        rrq = None
        if existing:
            row = conn.execute(
                sa.select(reassessment_requests.c.doc).where(
                    reassessment_requests.c.repo_id == repo["id"],
                    reassessment_requests.c.doc["event_ids"].contains([existing["id"]]),
                )
            ).scalar_one()
            rrq = ReassessmentRequest.model_validate(row)
            if rrq.assessment_id:
                return {"assessment_id": rrq.assessment_id, "idempotent_replay": True}
        scope = active_scope_for_repo(conn, repo["project_id"], repo["id"])
        reason = "automatic commit assessment" if scope else "waiting for repository scope approval"
        if existing is None:
            event = MonitoringEvent(
                id=new_id(IdPrefix.evt),
                project_id=repo["project_id"],
                repo_id=repo["id"],
                source=EventSource.github,
                type=EventType(p["event_type"]),
                pr_number=p.get("pr_number"),
                occurred_at=(commit.get("commit") or {}).get("committer", {}).get("date")
                or p["occurred_at"],
                received_at=p["received_at"],
                dedupe_key=key,
                payload_artifact_id=p["payload_artifact_id"],
                # First parent as base: the reviewer reviews this commit's diff, not the repo.
                commit_range=CommitRange(
                    base_sha=next(iter(commit.get("parents") or []), {}).get("sha"),
                    head_sha=p["sha"],
                ),
                ref=p["ref"],
                status=EventStatus.processed,
                status_reason=reason,
            )
            conn.execute(
                sa.insert(monitoring_events).values(
                    id=event.id,
                    project_id=event.project_id,
                    repo_id=event.repo_id,
                    dedupe_key=key,
                    status=event.status.value,
                    doc=event.model_dump(mode="json"),
                )
            )
            rrq = ReassessmentRequest(
                id=new_id(IdPrefix.rrq),
                project_id=repo["project_id"],
                repo_id=repo["id"],
                event_ids=[event.id],
                scope=ReassessmentScope.full,
                head_sha=p["sha"],
                status=ReassessmentStatus.blocked_no_scope,
                reason=reason,
                created_at=utcnow(),
            )
        assert rrq is not None
        delay_s = 0.0
        if scope:
            now = utcnow()
            limits = get_settings()
            for seconds, limit in (
                (3600, limits.automatic_assessments_per_hour),
                (86400, limits.automatic_assessments_per_day),
            ):
                count, oldest = conn.execute(
                    sa.select(sa.func.count(), sa.func.min(assessments.c.created_at)).where(
                        assessments.c.project_id == repo["project_id"],
                        assessments.c.trigger == AssessmentTrigger.monitoring.value,
                        assessments.c.created_at > now - timedelta(seconds=seconds),
                    )
                ).one()
                if count >= limit and oldest:
                    delay_s = max(
                        delay_s, (oldest + timedelta(seconds=seconds + 1) - now).total_seconds()
                    )
            # One ceiling over every project: outside users' repos cannot run up unbounded spend.
            # shortcut: in-flight runs are not counted, so it can overshoot by a few runs.
            spent = conn.execute(
                sa.select(
                    sa.func.coalesce(
                        sa.func.sum(assessments.c.doc["usage"]["usd"].astext.cast(sa.Numeric)), 0
                    )
                ).where(
                    assessments.c.trigger == AssessmentTrigger.monitoring.value,
                    assessments.c.created_at > now - timedelta(days=1),
                )
            ).scalar_one()
            if float(spent) >= limits.automatic_usd_per_day:
                delay_s = max(delay_s, 3600.0)
        if scope and delay_s:
            reason = "queued: waiting for project automatic-run capacity"
            rrq = rrq.model_copy(update={"status": ReassessmentStatus.queued, "reason": reason})
            jobs.enqueue(
                conn,
                "assess_github_commit",
                p,
                delay_s=delay_s,
                priority=60,
                dedupe_key=f"github:deferred:{ctx.job.id}",
            )
        elif scope:
            asm, _ = create_assessment(
                conn,
                project_id=repo["project_id"],
                repo_id=repo["id"],
                scope_id=scope.id,
                commit_sha=p["sha"],
                trigger=AssessmentTrigger.monitoring,
                budget=capped_budget(settings.default_budget),
                created_by=None,
                reassessment_request_id=rrq.id,
            )
            rrq = rrq.model_copy(
                update={
                    "assessment_id": asm.id,
                    "status": ReassessmentStatus.started,
                    "reason": reason,
                }
            )
        event_id = existing["id"] if existing else event.id
        event_doc = {
            **(existing["doc"] if existing else event.model_dump(mode="json")),
            "status_reason": reason,
        }
        conn.execute(
            sa.update(monitoring_events)
            .where(monitoring_events.c.id == event_id)
            .values(doc=event_doc)
        )
        values = dict(status=rrq.status.value, doc=rrq.model_dump(mode="json"))
        if existing:
            conn.execute(
                sa.update(reassessment_requests)
                .where(reassessment_requests.c.id == rrq.id)
                .values(**values)
            )
        else:
            conn.execute(
                sa.insert(reassessment_requests).values(
                    id=rrq.id, project_id=rrq.project_id, repo_id=rrq.repo_id, **values
                )
            )
        audit.record(
            conn,
            org_id=project_org(conn, repo["project_id"]),
            project_id=repo["project_id"],
            actor_type=ActorType.github,
            actor_id=sender,
            action="monitoring.event_processed",
            subject_type="monitoring_event",
            subject_id=event_id,
            details={"head_sha": p["sha"], "status": rrq.status.value, "reason": reason},
        )
        return {
            "reassessment_request_id": rrq.id,
            "assessment_id": rrq.assessment_id,
            "status": rrq.status.value,
        }


def resume_blocked(conn: Connection, project_id: str, scope_id: str) -> None:
    """Scope activation wakes durable blocked commits; eligibility is checked again."""
    from backend.db import jobs as jobs_table

    conn.execute(
        sa.select(repositories.c.id)
        .where(repositories.c.project_id == project_id)
        .order_by(repositories.c.id)
        .with_for_update()
    ).all()
    # Select only blocked requests, then join their original durable job by its unique key.
    source_key = (
        sa.literal("github:commit:")
        + reassessment_requests.c.repo_id
        + ":"
        + reassessment_requests.c.doc["head_sha"].astext
    )
    rows = conn.execute(
        sa.select(jobs_table.c.payload)
        .select_from(reassessment_requests)
        .join(jobs_table, jobs_table.c.dedupe_key == source_key)
        .where(
            reassessment_requests.c.project_id == project_id,
            reassessment_requests.c.status == ReassessmentStatus.blocked_no_scope.value,
        )
    ).scalars()
    for row in rows:
        jobs.enqueue(
            conn,
            "assess_github_commit",
            row,
            dedupe_key=f"github:resume:{scope_id}:{row['repo_id']}:{row['sha']}",
            priority=60,
        )
