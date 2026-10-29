"""Per-commit scheduling, gated by signed push sender and GitHub commit attribution."""

from __future__ import annotations

import fnmatch
import logging
import re
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from backend import audit, jobs
from backend.connection_guard import RepositoryDetached, github_write
from backend.db import (
    assessments,
    findings,
    monitoring_events,
    projects,
    reassessment_requests,
    repositories,
    transaction,
)
from backend.github import GitHubApp
from backend.quota import check_project_quota, skipped_reason
from backend.redaction import redact_text
from backend.services import (
    active_scope_for_repo,
    capped_budget,
    create_assessment,
    project_org,
    project_settings,
)
from backend.settings import get_settings
from backend.worker import JobContext, PermanentJobError, handler
from shared.assessment import Assessment, AssessmentState, AssessmentTrigger
from shared.audit import ActorType
from shared.base import utcnow
from shared.errors import ErrorCode
from shared.findings import SeverityLevel
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

log = logging.getLogger(__name__)

_CLIENT: tuple[Any, Any, Any] | None = None


def github_client() -> GitHubApp:
    """Reuse short-lived installation tokens while worker settings remain unchanged."""
    global _CLIENT
    settings = get_settings()
    if _CLIENT is None or _CLIENT[0] is not settings or _CLIENT[1] is not GitHubApp:
        _CLIENT = (settings, GitHubApp, GitHubApp())
    return _CLIENT[2]


def _configured_authors() -> set[str]:
    def clean(value: str) -> str:
        return value.strip().strip("\"'").strip()

    configured = clean(get_settings().github_auto_commit_authors)
    allowed: set[str] = set()
    for value in configured.split(","):
        entry = clean(value)
        if entry:
            allowed.add(entry.lower())
    return allowed


def authors() -> set[str]:
    configured = _configured_authors()
    return set() if configured == {"*"} else configured


def per_commit_enabled() -> bool:
    return bool(_configured_authors())


def _matches(patterns: list[str], value: str) -> bool:
    normalized = value.casefold()
    return any(
        fnmatch.fnmatchcase(normalized, pattern.casefold())
        or (pattern.casefold() == "*[bot]" and normalized.endswith("[bot]"))
        for pattern in patterns
    )


def reviewer_ignore_reason(
    conn: Connection, repo: Any, payload: dict[str, Any], change: Any
) -> str | None:
    reviewer = project_settings(conn, repo["project_id"]).review
    actor = (change.pr_author if change.event == "pull_request" else change.sender) or ""

    if change.event == "push" and not reviewer.review_pushes:
        return "skipped: push reviews disabled by reviewer settings"
    if change.event == "pull_request" and not reviewer.review_pull_requests:
        return "skipped: pull request reviews disabled by reviewer settings"
    if change.event == "pull_request" and change.draft and not reviewer.review_draft_prs:
        return "skipped: draft pull requests disabled by reviewer settings"

    branch = (
        change.pr_base_ref
        if change.event == "pull_request"
        else (change.ref or "").removeprefix("refs/heads/")
    ) or ""
    # No include list means every branch: each pushed commit is reviewed, not only the
    # default branch's (an owner narrows this with include/exclude patterns).
    if reviewer.include_branches and not _matches(reviewer.include_branches, branch):
        return "skipped: branch excluded by reviewer settings"
    if reviewer.exclude_branches and _matches(reviewer.exclude_branches, branch):
        return "skipped: branch excluded by reviewer settings"

    if reviewer.include_authors and not _matches(reviewer.include_authors, actor):
        return "skipped: author is outside reviewer settings"
    if reviewer.exclude_authors and _matches(reviewer.exclude_authors, actor):
        return "skipped: author excluded by reviewer settings"
    return None


def _record_schedule_ignore(
    conn: Connection, repo: Any, delivery: Any, change: Any, reason: str, actor: str
) -> None:
    dedupe = f"github:delivery:{delivery['delivery_id']}"
    event = MonitoringEvent(
        id=new_id(IdPrefix.evt),
        project_id=repo["project_id"],
        repo_id=repo["id"],
        source=EventSource.github,
        type=EventType(change.event),
        occurred_at=change.occurred_at,
        received_at=delivery["received_at"],
        dedupe_key=dedupe,
        payload_artifact_id=delivery["payload_artifact_id"],
        commit_range=CommitRange(base_sha=change.base_sha, head_sha=change.head_sha),
        ref=change.ref,
        pr_number=change.pr_number,
        status=EventStatus.ignored,
        status_reason=reason[:500],
    )
    inserted = conn.execute(
        pg_insert(monitoring_events)
        .values(
            id=event.id,
            project_id=event.project_id,
            repo_id=event.repo_id,
            dedupe_key=dedupe,
            status=event.status.value,
            doc=event.model_dump(mode="json"),
        )
        .on_conflict_do_nothing(index_elements=[monitoring_events.c.dedupe_key])
        .returning(monitoring_events.c.id)
    ).scalar()
    if inserted is None:
        return
    request = ReassessmentRequest(
        id=new_id(IdPrefix.rrq),
        project_id=repo["project_id"],
        repo_id=repo["id"],
        event_ids=[event.id],
        scope=ReassessmentScope.full,
        head_sha=change.head_sha,
        status=ReassessmentStatus.skipped,
        reason=reason[:500],
        created_at=utcnow(),
    )
    conn.execute(
        sa.insert(reassessment_requests).values(
            id=request.id,
            project_id=request.project_id,
            repo_id=request.repo_id,
            status=request.status.value,
            doc=request.model_dump(mode="json"),
        )
    )
    audit.record(
        conn,
        org_id=project_org(conn, repo["project_id"]),
        project_id=repo["project_id"],
        actor_type=ActorType.github,
        actor_id=actor or "unknown",
        action="monitoring.event_processed",
        subject_type="monitoring_event",
        subject_id=event.id,
        details={"head_sha": change.head_sha, "status": "skipped", "reason": reason},
    )


def schedule(
    conn: Connection, repo: Any, delivery: Any, payload: dict[str, Any], change: Any
) -> dict[str, Any]:
    actor = (change.pr_author if change.event == "pull_request" else change.sender) or ""

    def ignored(reason: str) -> dict[str, Any]:
        _record_schedule_ignore(conn, repo, delivery, change, reason, actor)
        return {"ignored": reason}

    if repo["disconnected_at"] is not None:
        return ignored("repository disconnected")

    if not repo["automatic_assessments"]:
        return ignored("skipped: repository automatic assessments disabled")
    reason = reviewer_ignore_reason(conn, repo, payload, change)
    if reason:
        return ignored(reason)

    actor = actor.lower()
    allowed = authors()
    if allowed and "*" not in allowed and actor not in allowed:
        return ignored("skipped: author is outside the global automatic-review allowlist")
    sender = actor
    installation = repo["installation_id"]
    if not installation or (payload.get("installation") or {}).get("id") != installation:
        return ignored("skipped: push installation does not match connected repository")
    commits = payload.get("commits") or []
    if change.event == "pull_request":
        commits = [{"id": change.head_sha}]
    shas = [c.get("id") for c in commits if isinstance(c, dict)]
    # Compare is paginated; webhook payloads can truncate large pushes. When the API cannot
    # enumerate the range (force-pushed `before`, installation revoked, GitHub down), fall
    # back to what the signed payload names rather than lose the push after retries.
    if change.event == "push" and change.base_sha and (len(commits) >= 20 or not commits):
        try:
            shas = github_client().compare_commits(
                installation, repo["full_name"], change.base_sha, change.head_sha
            )
        except Exception as exc:
            log.warning(
                "compare %s %s...%s failed (%s); assessing payload commits",
                repo["full_name"],
                change.base_sha[:12],
                change.head_sha[:12],
                type(exc).__name__,
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


def _permanent_failure(exc: httpx.HTTPStatusError) -> str | None:
    """Classify a GitHub API failure as permanent (record it) or transient (retry it)."""
    code = exc.response.status_code
    if code in (401, 404, 410, 422):
        return (
            f"GitHub answered {code} for the commit lookup — the commit is gone, the "
            "repository moved, or the installation can no longer read it"
        )
    if code == 403 and "rate limit" not in (exc.response.text or "").lower():
        return (
            "GitHub denied the commit lookup — the installation was removed or permissions revoked"
        )
    return None


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
    if not rows:
        # Nothing was recorded yet (e.g. a GitHub read failed before the event existed, or a
        # replayed/deferred job outlived its event): the stop must still surface, not strand
        # the commit silently.
        _record_exit(conn, payload, reason)
    return {"ignored": reason}


def _record_exit(conn: Connection, payload: dict[str, Any], reason: str) -> None:
    """Persist an ignored monitoring event + skipped reassessment for `payload` if absent."""
    repo = (
        conn.execute(sa.select(repositories).where(repositories.c.id == payload["repo_id"]))
        .mappings()
        .first()
    )
    if repo is None:
        return
    key = f"github:commit:{repo['id']}:{payload['sha']}"
    event_id = conn.execute(
        sa.select(monitoring_events.c.id).where(monitoring_events.c.dedupe_key == key)
    ).scalar()
    if event_id is None:
        event = MonitoringEvent(
            id=new_id(IdPrefix.evt),
            project_id=repo["project_id"],
            repo_id=repo["id"],
            source=EventSource.github,
            type=EventType(payload["event_type"]),
            pr_number=payload.get("pr_number"),
            occurred_at=utcnow(),
            received_at=payload["received_at"],
            dedupe_key=key,
            payload_artifact_id=payload["payload_artifact_id"],
            commit_range=CommitRange(base_sha=None, head_sha=payload["sha"]),
            ref=payload["ref"],
            status=EventStatus.ignored,
            status_reason=reason,
        )
        inserted = conn.execute(
            pg_insert(monitoring_events)
            .values(
                id=event.id,
                project_id=event.project_id,
                repo_id=event.repo_id,
                dedupe_key=key,
                status=event.status.value,
                doc=event.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(index_elements=[monitoring_events.c.dedupe_key])
            .returning(monitoring_events.c.id)
        ).scalar()
        if inserted is None:  # a concurrent commit job recorded it first
            return
        event_id = event.id
    else:
        conn.execute(
            sa.update(monitoring_events)
            .where(monitoring_events.c.id == event_id)
            .values(status=EventStatus.ignored.value)
        )
    rrq = ReassessmentRequest(
        id=new_id(IdPrefix.rrq),
        project_id=repo["project_id"],
        repo_id=repo["id"],
        event_ids=[event_id],
        scope=ReassessmentScope.full,
        head_sha=payload["sha"],
        status=ReassessmentStatus.skipped,
        reason=reason,
        created_at=utcnow(),
    )
    conn.execute(
        sa.insert(reassessment_requests).values(
            id=rrq.id,
            project_id=rrq.project_id,
            repo_id=rrq.repo_id,
            status=rrq.status.value,
            doc=rrq.model_dump(mode="json"),
        )
    )
    audit.record(
        conn,
        org_id=project_org(conn, repo["project_id"]),
        project_id=repo["project_id"],
        actor_type=ActorType.system,
        actor_id="monitoring",
        action="monitoring.event_processed",
        subject_type="monitoring_event",
        subject_id=event_id,
        details={"head_sha": payload["sha"], "status": "skipped", "reason": reason},
    )


@handler("assess_github_commit")
def assess_commit(ctx: JobContext) -> dict[str, Any]:
    p = ctx.job.payload
    allowed = authors()
    sender = p.get("sender", "").lower()
    if allowed and "*" not in allowed and (not sender or sender not in allowed):
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
    try:
        commit = github_client().commit(repo["installation_id"], repo["full_name"], p["sha"])
    except httpx.HTTPStatusError as exc:
        reason = _permanent_failure(exc)
        if reason is None:
            raise  # transient GitHub errors keep the normal retry path
        return policy_exit(p, reason)
    if commit.get("sha") != p["sha"]:
        raise ValueError("GitHub returned a different commit")
    login = (commit.get("author") or {}).get("login", "").lower()
    # "*" admits any author, including commits whose email maps to no GitHub account.
    if allowed and "*" not in allowed and (not login or login not in allowed):
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
            if rrq.status == ReassessmentStatus.skipped:
                return {
                    "reassessment_request_id": rrq.id,
                    "assessment_id": None,
                    "status": rrq.status.value,
                    "idempotent_replay": True,
                }
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
        quota = None
        if scope:
            quota = check_project_quota(conn, repo["project_id"], utcnow())
        if quota is not None:
            reason = skipped_reason(quota)
            rrq = rrq.model_copy(update={"status": ReassessmentStatus.skipped, "reason": reason})
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
        if rrq.status == ReassessmentStatus.skipped:
            event_doc["status"] = EventStatus.ignored.value
        conn.execute(
            sa.update(monitoring_events)
            .where(monitoring_events.c.id == event_id)
            .values(status=event_doc["status"], doc=event_doc)
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


def _pr_review_body(
    assessment_id: str,
    project_id: str,
    finding_docs: list[dict[str, Any]],
    strictness: int,
) -> tuple[str, int]:
    thresholds = {
        1: {level.value for level in SeverityLevel},
        2: {"medium", "high", "critical"},
        3: {"high", "critical"},
    }
    included = [
        finding
        for finding in finding_docs
        if strictness == 1 or (finding.get("severity") or {}).get("level") in thresholds[strictness]
    ]
    origin = get_settings().frontend_origin.rstrip("/")
    lines = [
        "## Ari security review",
        "",
        f"{len(included)} findings meet strictness level {strictness}.",
        "",
    ]
    if not included:
        lines.append("No findings at this strictness.")
    else:
        lines.extend(
            [
                "| Title | Severity | Status | Evidence location |",
                "| --- | --- | --- | --- |",
            ]
        )
        for finding in included:
            evidence = (finding.get("evidence") or [{}])[0]
            path = evidence.get("path")
            span = evidence.get("span") or {}
            location = f"{path}:{span['start_line']}" if path and span.get("start_line") else "—"
            cells = (
                finding.get("title", "Untitled finding"),
                (finding.get("severity") or {}).get("level", "unrated"),
                finding.get("status", "unknown"),
                location,
            )
            lines.append(
                "| "
                + " | ".join(
                    str(cell).replace("\r", " ").replace("\n", " ").replace("|", "\\|")
                    for cell in cells
                )
                + " |"
            )
    lines.extend(
        [
            "",
            f"[View assessment]({origin}/projects/{project_id}/assessments/{assessment_id})",
        ]
    )
    return redact_text("\n".join(lines)), len(included)


@handler("post_pr_review")
def post_pr_review(ctx: JobContext) -> dict[str, Any]:
    assessment_id = ctx.job.payload["assessment_id"]
    with transaction() as conn:
        assessment_doc = conn.execute(
            sa.select(assessments.c.doc).where(assessments.c.id == assessment_id)
        ).scalar_one_or_none()
        if assessment_doc is None:
            raise PermanentJobError(ErrorCode.NOT_FOUND, "assessment not found")
        assessment = Assessment.model_validate(assessment_doc)
        if assessment.state not in (
            AssessmentState.COMPLETED,
            AssessmentState.AWAITING_APPROVAL,
        ):
            return {"noop": "assessment is not ready for a PR review comment"}
        if assessment.reassessment_request_id is None or assessment.repo_id is None:
            return {"noop": "assessment is not linked to a GitHub event"}
        request_doc = conn.execute(
            sa.select(reassessment_requests.c.doc).where(
                reassessment_requests.c.id == assessment.reassessment_request_id
            )
        ).scalar_one_or_none()
        if request_doc is None:
            return {"noop": "reassessment request not found"}
        event_docs = (
            conn.execute(
                sa.select(monitoring_events.c.doc).where(
                    monitoring_events.c.id.in_(request_doc.get("event_ids", []) or [""])
                )
            )
            .scalars()
            .all()
        )
        pr_event = next((event for event in event_docs if event.get("pr_number")), None)
        if pr_event is None:
            return {"noop": "assessment is not linked to a pull request"}
        settings = project_settings(conn, assessment.project_id)
        if not settings.review.comment_on_pull_requests:
            return {"noop": "pull request comments are disabled"}
        repo = (
            conn.execute(sa.select(repositories).where(repositories.c.id == assessment.repo_id))
            .mappings()
            .first()
        )
        if repo is not None and repo["disconnected_at"] is not None:
            return {"noop": "repository is disconnected"}
        if repo is None or not repo["installation_id"]:
            raise PermanentJobError(
                ErrorCode.VALIDATION_FAILED, "connected GitHub installation is unavailable"
            )
        finding_docs = list(
            conn.execute(
                sa.select(findings.c.doc)
                .where(
                    findings.c.assessment_id == assessment_id,
                    findings.c.project_id == assessment.project_id,
                )
                .order_by(findings.c.created_at, findings.c.id)
            ).scalars()
        )
        body, finding_count = _pr_review_body(
            assessment_id, assessment.project_id, finding_docs, settings.review.strictness
        )
        pr_number = int(pr_event["pr_number"])
        project_id = assessment.project_id
        repo_full_name = repo["full_name"]
        installation_id = repo["installation_id"]
        org_id = project_org(conn, project_id)

    ctx.check_cancelled()
    try:
        # The owner may have disconnected, or GitHub detached the installation, since the read
        # above; the write lease re-checks and keeps a concurrent disconnect out until the POST is done.
        with github_write(str(assessment.repo_id), int(installation_id)):
            github_client().post_pull_request_review(
                installation_id, repo_full_name, pr_number, body
            )
    except RepositoryDetached as exc:
        return {"noop": exc.reason}
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if 400 <= status_code < 500:
            with transaction() as conn:
                audit.record(
                    conn,
                    org_id=org_id,
                    project_id=project_id,
                    actor_type=ActorType.system,
                    actor_id="monitoring",
                    action="monitoring.pr_review_failed",
                    subject_type="assessment",
                    subject_id=assessment_id,
                    details={"http_status": status_code, "reason": "GitHub rejected PR review"},
                )
            raise PermanentJobError(
                ErrorCode.VALIDATION_FAILED,
                f"GitHub rejected PR review (HTTP {status_code})",
            ) from exc
        raise

    with transaction() as conn:
        audit.record(
            conn,
            org_id=org_id,
            project_id=project_id,
            actor_type=ActorType.system,
            actor_id="monitoring",
            action="monitoring.pr_review_posted",
            subject_type="assessment",
            subject_id=assessment_id,
            details={"pr_number": pr_number, "finding_count": finding_count},
        )
    return {"posted": True, "pr_number": pr_number, "finding_count": finding_count}


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
