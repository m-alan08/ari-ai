"""First-responder job: deterministic decide, persist, dispatch gated agent steps.

Never enqueues ``live_defense_execute``. Patch is worktree-only. Probe needs
AuthorizedScope. Verify is independent of Patch. LLM prose cannot confirm a threat.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from backend import audit
from backend import jobs as jobq
from backend.db import (
    analyst_handoffs,
    approvals,
    connected_applications,
    deploy_commit_mappings,
    findings,
    first_responder_decisions,
    first_responder_invocations,
    ingest_tokens,
    live_policy_decisions,
    patch_candidates,
    runtime_windows,
    snapshots,
    transaction,
    verification_results,
)
from backend.db import jobs as jobs_t
from backend.live_defense import provider_ledger
from backend.live_defense.agent_execution import (
    MIN_PROVIDER_START_S,
    ExecutionOutcome,
    load_scout_output,
    remaining_deadline_s,
    run_investigative,
)
from backend.live_defense.capabilities import load_registry
from backend.live_defense.modes import (
    acquire_first_responder_lock,
    cancel_first_responder_jobs,
    effective_permissions,
    load_intent,
    mapping_fresh,
    telemetry_untrusted,
)
from backend.live_defense.orchestrator import OrchestratorFacts, decide
from backend.services import (
    active_scope_for_repo,
    project_org,
    repository_revocation,
    repository_revocation_locked,
)
from backend.worker import JobCancelled, JobContext, JobDeferred, handler
from shared.assessment import AgentName
from shared.audit import ActorType
from shared.base import utcnow
from shared.ids import IdPrefix, new_id
from shared.live_defense import (
    FIRST_RESPONDER_JOB_KINDS,
    ORCHESTRATOR_VERSION,
    AgentCapabilityClass,
    AgentExecutionRecord,
    AgentInvocationStatus,
    AnalystHandoff,
    AnalystHandoffState,
    ApplicationVerification,
    ConfirmationState,
    ConnectedApplication,
    ExecutionIntegrity,
    FirstResponderDecision,
    FirstResponderIntent,
    FirstResponderInvocation,
    OrchestratorAction,
    StagingEnvironment,
)
from shared.monitoring import AUTHENTICATED_RUNTIME_SOURCES
from shared.scope import ApprovalStatus, ApprovalSubject

log = logging.getLogger("backend.live_defense.first_responder")


def _application(conn: Connection, app_id: str | None) -> ConnectedApplication | None:
    if not app_id:
        return None
    row = (
        conn.execute(sa.select(connected_applications).where(connected_applications.c.id == app_id))
        .mappings()
        .first()
    )
    if row is None:
        return None
    disconnected_at = (row["doc"] or {}).get("disconnected_at")
    last_ingest = conn.execute(
        sa.select(sa.func.max(runtime_windows.c.window_end)).where(
            runtime_windows.c.application_id == app_id
        )
    ).scalar()
    active_token = conn.execute(
        sa.select(ingest_tokens.c.id).where(
            ingest_tokens.c.application_id == app_id, ingest_tokens.c.revoked_at.is_(None)
        )
    ).scalar()
    if disconnected_at:
        verification = ApplicationVerification.disconnected
    elif last_ingest is not None:
        verification = ApplicationVerification.ingest_seen
    else:
        verification = ApplicationVerification.unverified
    return ConnectedApplication(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        environment=StagingEnvironment(row["environment"]),
        ingest_configured=bool(active_token) and disconnected_at is None,
        last_ingest_at=last_ingest,
        verification=verification,
        disconnected_at=disconnected_at,
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


def _as_aware(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    parsed: datetime
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _mapping_deployed_at(mapping: dict[str, Any]) -> datetime | None:
    return _as_aware((mapping.get("doc") or {}).get("deployed_at"))


def _trusted_mapping(
    conn: Connection,
    application_id: str | None,
    *,
    window_end: datetime | None = None,
) -> dict[str, Any] | None:
    """Owner-attested mapping that covers ``window_end``, not the newest row.

    Attribution is UNKNOWN when no non-revoked mapping has ``deployed_at`` at or
    before the telemetry event window. A later deploy cannot explain earlier traffic.
    """
    if not application_id or window_end is None:
        return None
    window = _as_aware(window_end)
    if window is None:
        return None
    rows = (
        conn.execute(
            sa.select(deploy_commit_mappings).where(
                deploy_commit_mappings.c.application_id == application_id,
                deploy_commit_mappings.c.revoked_at.is_(None),
            )
        )
        .mappings()
        .all()
    )
    eligible: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows:
        mapping = dict(row)
        deployed = _mapping_deployed_at(mapping)
        if deployed is None or deployed > window:
            continue
        eligible.append((deployed, mapping))
    if not eligible:
        return None
    eligible.sort(key=lambda item: item[0], reverse=True)
    return eligible[0][1]


def _mapping_by_id(conn: Connection, mapping_id: str | None) -> dict[str, Any] | None:
    if not mapping_id:
        return None
    row = (
        conn.execute(
            sa.select(deploy_commit_mappings).where(
                deploy_commit_mappings.c.id == mapping_id,
                deploy_commit_mappings.c.revoked_at.is_(None),
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _repository_disconnected_at(conn: Connection, repo_id: str) -> Any:
    """Read ``repositories.disconnected_at`` when Devin Hardening's column exists.

    This branch does not add that column or ``POST /repositories/{id}/disconnect``.
    """
    present = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'repositories' AND column_name = 'disconnected_at'"
        )
    ).scalar()
    if not present:
        return None
    return conn.execute(
        sa.text("SELECT disconnected_at FROM repositories WHERE id = :id"),
        {"id": repo_id},
    ).scalar()


def investigation_source_revoked(
    conn: Connection,
    *,
    mapping_id: str | None = None,
    repo_id: str | None = None,
) -> str | None:
    """Reason the mapped repository or deploy mapping no longer authorizes provider work.

    Devin Hardening owns the disconnect endpoint and ``repositories.disconnected_at``.
    7C re-checks mapping revocation and that column (when present) before each
    provider ``complete()`` and before accepting final agent output.
    """
    resolved_repo = repo_id
    if mapping_id:
        row = (
            conn.execute(
                sa.select(deploy_commit_mappings).where(deploy_commit_mappings.c.id == mapping_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            return "deploy mapping is missing"
        if row["revoked_at"] is not None:
            return "deploy mapping was revoked"
        resolved_repo = resolved_repo or str(row["repo_id"])
    if resolved_repo and _repository_disconnected_at(conn, resolved_repo) is not None:
        return "repository is disconnected"
    return None


def apply_repository_disconnect_to_investigations(conn: Connection, repo_id: str) -> int:
    """7C half of repository disconnect: revoke mappings and cancel running investigations.

    Devin Hardening should call this after setting ``repositories.disconnected_at``.
    This function does not implement the disconnect HTTP route or the Alembic column.
    """
    conn.execute(
        sa.update(deploy_commit_mappings)
        .where(
            deploy_commit_mappings.c.repo_id == repo_id,
            deploy_commit_mappings.c.revoked_at.is_(None),
        )
        .values(revoked_at=sa.func.now())
    )
    mapping_ids = set(
        conn.execute(
            sa.select(deploy_commit_mappings.c.id).where(
                deploy_commit_mappings.c.repo_id == repo_id
            )
        ).scalars()
    )
    cancelled = 0
    rows = (
        conn.execute(
            sa.select(jobs_t.c.id, jobs_t.c.payload).where(
                jobs_t.c.kind.in_(FIRST_RESPONDER_JOB_KINDS),
                jobs_t.c.status.in_(("queued", "running")),
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        payload = row["payload"] or {}
        bound = payload.get("repo_id") == repo_id
        decision_id = payload.get("decision_id")
        if not bound and decision_id:
            doc = conn.execute(
                sa.select(first_responder_decisions.c.doc).where(
                    first_responder_decisions.c.id == decision_id
                )
            ).scalar()
            mapping = (doc or {}).get("correlation") or {}
            bound = mapping.get("mapping_id") in mapping_ids or mapping.get("repo_id") == repo_id
        if not bound:
            continue
        jobq.request_cancel(conn, row["id"])
        cancelled += 1
        invocation_id = payload.get("invocation_id")
        if invocation_id:
            _mark_invocation_cancelled(
                conn,
                invocation_id,
                "repository disconnected; running investigation cancelled",
            )
    return cancelled


def _independent_verification(conn: Connection, project_id: str) -> str | None:
    """Return an executed VerificationResult id, not Verify eligibility."""
    return conn.execute(
        sa.select(verification_results.c.id)
        .select_from(
            verification_results.join(
                patch_candidates, patch_candidates.c.id == verification_results.c.patch_id
            ).join(findings, findings.c.id == patch_candidates.c.finding_id)
        )
        .where(findings.c.project_id == project_id)
        .order_by(verification_results.c.created_at.desc())
        .limit(1)
    ).scalar()


def _mark_invocation_cancelled(conn: Connection, invocation_id: str, reason: str) -> None:
    inv = _load_invocation_for_update(conn, invocation_id)
    if inv is None or inv.status is AgentInvocationStatus.executed:
        return
    updated = inv.model_copy(
        update={
            "status": AgentInvocationStatus.cancelled,
            "reason": reason,
            "provider_mode": inv.provider_mode,
        }
    )
    _commit_invocation(conn, updated, status=AgentInvocationStatus.cancelled)


def _abort_if_off_or_cancelled(
    conn: Connection,
    ctx: JobContext,
    *,
    project_id: str,
    invocation_id: str | None = None,
) -> None:
    ctx.check_cancelled()
    if jobq.is_cancel_requested(conn, ctx.job.id):
        if invocation_id:
            _mark_invocation_cancelled(
                conn, invocation_id, "job cancelled; first-responder OFF or cancel requested"
            )
        raise JobCancelled(ctx.job.id)
    if load_intent(conn, project_id).intent is FirstResponderIntent.off:
        if invocation_id:
            _mark_invocation_cancelled(
                conn,
                invocation_id,
                "first-responder OFF is effective; agent step must not continue",
            )
        raise JobCancelled(f"{ctx.job.id}: first-responder OFF")


def _findings_at(conn: Connection, project_id: str, commit_sha: str | None) -> list[str]:
    """Finding ids at a commit are listed only as context, never as proof."""
    _ = conn, project_id, commit_sha
    return []


def _remediation_approved(conn: Connection, project_id: str) -> bool:
    return (
        conn.execute(
            sa.select(approvals.c.id).where(
                approvals.c.project_id == project_id,
                approvals.c.subject_type == ApprovalSubject.remediation.value,
                approvals.c.status == ApprovalStatus.approved.value,
            )
        ).scalar()
        is not None
    )


def _patch_candidate(conn: Connection, project_id: str) -> str | None:
    return conn.execute(
        sa.select(patch_candidates.c.id)
        .select_from(
            patch_candidates.join(findings, findings.c.id == patch_candidates.c.finding_id)
        )
        .where(findings.c.project_id == project_id)
        .order_by(patch_candidates.c.created_at.desc())
        .limit(1)
    ).scalar()


def _snapshot_id(conn: Connection, repo_id: str | None, commit_sha: str | None) -> str | None:
    if not repo_id or not commit_sha:
        return None
    return conn.execute(
        sa.select(snapshots.c.id).where(
            snapshots.c.repo_id == repo_id, snapshots.c.commit_sha == commit_sha
        )
    ).scalar()


def _snapshot_exists(conn: Connection, repo_id: str | None, commit_sha: str | None) -> bool:
    return _snapshot_id(conn, repo_id, commit_sha) is not None


def maybe_enqueue(
    conn: Connection,
    *,
    project_id: str,
    application_id: str | None,
    window_end: datetime,
    source: str,
    live_policy_decision_id: str | None,
    event_ids: list[str],
) -> tuple[str, bool] | None:
    """Enqueue a first-responder job when effective mode is not OFF. Isolated queue."""
    acquire_first_responder_lock(conn, project_id)
    app = _application(conn, application_id)
    mapping = _trusted_mapping(conn, application_id, window_end=window_end)
    perms = effective_permissions(
        conn,
        project_id=project_id,
        application=app,
        repo_id=mapping["repo_id"] if mapping else None,
        trusted_mapping=bool(mapping),
    )
    if perms.effective is FirstResponderIntent.off:
        return None
    dedupe = f"live_defense_first_responder:{project_id}:{application_id or '-'}:{window_end.isoformat()}:{source}"
    return jobq.enqueue(
        conn,
        "live_defense_first_responder",
        {
            "project_id": project_id,
            "application_id": application_id,
            "window_end": window_end.isoformat(),
            "source": source,
            "live_policy_decision_id": live_policy_decision_id,
            "event_ids": event_ids,
        },
        dedupe_key=dedupe,
        priority=80,
    )


def _persist_decision(
    conn: Connection, decision: FirstResponderDecision, dedupe: str
) -> tuple[FirstResponderDecision, bool]:
    inserted = conn.execute(
        pg_insert(first_responder_decisions)
        .values(
            id=decision.id,
            project_id=decision.project_id,
            application_id=decision.application_id,
            dedupe_key=dedupe,
            action=decision.action.value,
            doc=decision.model_dump(mode="json"),
        )
        .on_conflict_do_nothing()
        .returning(first_responder_decisions.c.id, first_responder_decisions.c.doc)
    ).first()
    if inserted:
        return decision, True
    existing = (
        conn.execute(
            sa.select(first_responder_decisions.c.doc).where(
                first_responder_decisions.c.dedupe_key == dedupe
            )
        )
        .mappings()
        .one()
    )
    return FirstResponderDecision.model_validate(existing["doc"]), False


def _invocation_status(
    agent: AgentName,
    decision: FirstResponderDecision,
    *,
    has_snapshot: bool,
    has_scope: bool,
    has_approval: bool,
    has_patch: bool,
) -> AgentInvocationStatus:
    blocked = {item.name: item.reason for item in decision.blocked_agents}
    if agent in blocked:
        reason = blocked[agent]
        if "AuthorizedScope" in reason or "scope" in reason.lower():
            return AgentInvocationStatus.skipped_no_scope
        if "approval" in reason.lower():
            return AgentInvocationStatus.skipped_no_approval
        if "PatchCandidate" in reason or "patch" in reason.lower():
            return AgentInvocationStatus.skipped_no_patch
        if "mapping" in reason.lower():
            return AgentInvocationStatus.skipped_no_mapping
        if "OFF" in reason or "ASSISTED" in reason:
            return AgentInvocationStatus.skipped_mode
        if "Trace follows" in reason or "prerequisite" in reason.lower():
            return AgentInvocationStatus.skipped_no_prerequisite
        return AgentInvocationStatus.blocked
    if agent in {AgentName.scout, AgentName.diagnose, AgentName.fortify, AgentName.trace}:
        if decision.correlation.confidence.value in {"unknown", "none"}:
            return AgentInvocationStatus.skipped_no_mapping
        if not has_snapshot:
            return AgentInvocationStatus.skipped_no_snapshot
        return AgentInvocationStatus.queued
    if agent is AgentName.probe:
        return AgentInvocationStatus.queued if has_scope else AgentInvocationStatus.skipped_no_scope
    if agent is AgentName.patch:
        return (
            AgentInvocationStatus.queued
            if has_approval
            else AgentInvocationStatus.skipped_no_approval
        )
    if agent is AgentName.verify:
        return AgentInvocationStatus.queued if has_patch else AgentInvocationStatus.skipped_no_patch
    return AgentInvocationStatus.blocked


def _record_invocations(
    conn: Connection, decision: FirstResponderDecision, *, has_snapshot: bool, has_scope: bool
) -> list[FirstResponderInvocation]:
    registry = {item.name: item for item in load_registry().agents}
    has_approval = _remediation_approved(conn, decision.project_id)
    has_patch = _patch_candidate(conn, decision.project_id) is not None
    out: list[FirstResponderInvocation] = []
    for agent in [*decision.selected_agents, *[b.name for b in decision.blocked_agents]]:
        cap = registry.get(agent)
        if cap is None:
            continue
        status = _invocation_status(
            agent,
            decision,
            has_snapshot=has_snapshot,
            has_scope=has_scope,
            has_approval=has_approval,
            has_patch=has_patch,
        )
        inv = FirstResponderInvocation(
            id=new_id(IdPrefix.fri),
            decision_id=decision.id,
            project_id=decision.project_id,
            agent=agent,
            capability_class=cap.capability_class,
            status=status,
            provider_mode="none",
            independent_of_patch=agent is AgentName.verify,
            worktree_only=cap.capability_class is AgentCapabilityClass.code_changing,
            authorized_scope_id=None,
            reason=next(
                (b.reason for b in decision.blocked_agents if b.name is agent), status.value
            ),
            deadline_at=decision.deadline_at,
            created_at=utcnow(),
        )
        conn.execute(
            sa.insert(first_responder_invocations).values(
                id=inv.id,
                decision_id=decision.id,
                project_id=decision.project_id,
                agent=agent.value,
                status=status.value,
                doc=inv.model_dump(mode="json"),
            )
        )
        if status is AgentInvocationStatus.queued and agent is not AgentName.diagnose:
            job_id, _ = jobq.enqueue(
                conn,
                "live_defense_agent_step",
                {
                    "project_id": decision.project_id,
                    "decision_id": decision.id,
                    "invocation_id": inv.id,
                    "agent": agent.value,
                    "capability_class": cap.capability_class.value,
                    "deadline_at": (
                        decision.deadline_at.isoformat() if decision.deadline_at else None
                    ),
                },
                dedupe_key=f"live_defense_agent_step:{decision.id}:{agent.value}",
                priority=70 if agent is AgentName.scout else 90,
                deadline_at=decision.deadline_at,
            )
            inv = inv.model_copy(update={"job_id": job_id})
            conn.execute(
                sa.update(first_responder_invocations)
                .where(first_responder_invocations.c.id == inv.id)
                .values(doc=inv.model_dump(mode="json"))
            )
        out.append(inv)
    return out


def _maybe_handoff(conn: Connection, decision: FirstResponderDecision, actor_id: str) -> None:
    if decision.action not in {
        OrchestratorAction.escalate_to_analyst,
        OrchestratorAction.confirm_with_probe,
        OrchestratorAction.propose_worktree_patch,
    }:
        return
    if decision.effective_mode is FirstResponderIntent.off:
        return
    handoff = AnalystHandoff(
        id=new_id(IdPrefix.ahl),
        project_id=decision.project_id,
        application_id=decision.application_id,
        decision_id=decision.id,
        state=AnalystHandoffState.delivery_pending,
        confirmation=ConfirmationState.unverified,
        title=f"First-responder {decision.action.value}",
        summary=(
            f"Orchestrator {ORCHESTRATOR_VERSION} recorded {decision.action.value}. "
            "Not a confirmed threat. Alert is not delivered until the provider confirms."
        ),
        evidence_event_ids=list(decision.trigger_event_ids),
        recommended_actions=[a.value for a in decision.selected_agents],
        created_by=actor_id if actor_id.startswith("usr_") else "usr_system_first_responder",
        created_at=utcnow(),
        delivery_attempted_at=utcnow(),
    )
    # System actor must be a real UserId (FK). Prefer the owner who set the mode.
    mode = load_intent(conn, decision.project_id)
    created_by = mode.updated_by
    if created_by is None or not str(created_by).startswith("usr_"):
        app_row = (
            conn.execute(
                sa.select(connected_applications.c.created_by).where(
                    connected_applications.c.id == decision.application_id
                )
            ).scalar()
            if decision.application_id
            else None
        )
        created_by = app_row
    if created_by is None or not str(created_by).startswith("usr_"):
        return
    handoff = handoff.model_copy(update={"created_by": created_by})
    conn.execute(
        sa.insert(analyst_handoffs).values(
            id=handoff.id,
            project_id=handoff.project_id,
            application_id=handoff.application_id,
            decision_id=handoff.decision_id,
            state=handoff.state.value,
            confirmation=handoff.confirmation.value,
            created_by=handoff.created_by,
            doc=handoff.model_dump(mode="json"),
        )
    )


def process_first_responder(
    project_id: str,
    *,
    application_id: str | None,
    window_end: datetime,
    source: str,
    live_policy_decision_id: str | None,
    event_ids: list[str],
    job_ctx: JobContext | None = None,
) -> dict[str, Any]:
    dedupe = f"{project_id}:{application_id or '-'}:{window_end.isoformat()}:{source}"
    with transaction() as conn:
        acquire_first_responder_lock(conn, project_id)
        if job_ctx is not None:
            _abort_if_off_or_cancelled(conn, job_ctx, project_id=project_id)
        existing = conn.execute(
            sa.select(first_responder_decisions.c.id).where(
                first_responder_decisions.c.dedupe_key == dedupe
            )
        ).scalar()
        app = _application(conn, application_id)
        mapping = _trusted_mapping(conn, application_id, window_end=window_end)
        repo_id = mapping["repo_id"] if mapping else None
        perms = effective_permissions(
            conn,
            project_id=project_id,
            application=app,
            repo_id=repo_id,
            trusted_mapping=bool(mapping),
        )
        shadow = None
        if live_policy_decision_id:
            doc = conn.execute(
                sa.select(live_policy_decisions.c.doc).where(
                    live_policy_decisions.c.id == live_policy_decision_id
                )
            ).scalar()
            shadow = doc
        mapped_commit = mapping["commit_sha"] if mapping else None
        deployed_at = _mapping_deployed_at(mapping) if mapping else None
        facts = OrchestratorFacts(
            project_id=project_id,
            application_id=application_id,
            intent=perms.intent,
            effective=perms.effective,
            application_connected=perms.application_connected,
            telemetry_authenticated=source in AUTHENTICATED_RUNTIME_SOURCES,
            secret_like=bool(telemetry_untrusted(source)),
            trusted_mapping=bool(mapping),
            mapped_commit=mapped_commit,
            mapping_id=mapping["id"] if mapping else None,
            mapping_fresh=mapping_fresh(deployed_at) if deployed_at else False,
            authorized_scope_active=perms.authorized_scope_active,
            remediation_approved=_remediation_approved(conn, project_id),
            patch_candidate_id=_patch_candidate(conn, project_id),
            kill_switch_active=perms.kill_switch_active,
            budget_usd_remaining=3.0,
            deadline_passed=False,
            already_decided_id=existing,
            retry_count=0,
            loop_count=1 if existing else 0,
            simulated=bool(shadow and shadow.get("simulated")),
            trigger_event_ids=tuple(event_ids),
            live_policy_decision_id=live_policy_decision_id,
            finding_ids_at_commit=tuple(_findings_at(conn, project_id, mapped_commit)),
            shadow_action=(shadow or {}).get("action"),
            q_values=None,
            independent_verification_id=_independent_verification(conn, project_id),
        )
        decision = decide(facts)
        stored, created = _persist_decision(conn, decision, dedupe)
        if created:
            has_snapshot = _snapshot_exists(conn, repo_id, mapped_commit)
            _record_invocations(
                conn,
                stored,
                has_snapshot=has_snapshot,
                has_scope=perms.authorized_scope_active,
            )
            _maybe_handoff(conn, stored, actor_id="live_defense.first_responder")
            audit.record(
                conn,
                org_id=project_org(conn, project_id),
                project_id=project_id,
                actor_type=ActorType.system,
                actor_id="live_defense.first_responder",
                action="live_defense.first_responder.decided",
                subject_type="first_responder_decision",
                subject_id=stored.id,
                details={
                    "action": stored.action.value,
                    "effective_mode": stored.effective_mode.value,
                    "executed": False,
                    "auto_execute": False,
                    "confirmation": stored.confirmation.value,
                    "orchestrator_version": stored.orchestrator_version,
                },
            )
        return {"decision_id": stored.id, "created": created, "action": stored.action.value}


@handler("live_defense_first_responder")
def live_defense_first_responder(ctx: JobContext) -> dict[str, Any]:
    payload = ctx.job.payload
    ctx.check_cancelled()
    window_end = datetime.fromisoformat(payload["window_end"])
    return process_first_responder(
        payload["project_id"],
        application_id=payload.get("application_id"),
        window_end=window_end,
        source=payload["source"],
        live_policy_decision_id=payload.get("live_policy_decision_id"),
        event_ids=list(payload.get("event_ids") or []),
        job_ctx=ctx,
    )


_TERMINAL_INVOCATION = frozenset(
    {
        AgentInvocationStatus.executed,
        AgentInvocationStatus.failed,
        AgentInvocationStatus.cancelled,
        AgentInvocationStatus.blocked,
        AgentInvocationStatus.eligible,
        AgentInvocationStatus.completed_unverified,
        AgentInvocationStatus.skipped_no_mapping,
        AgentInvocationStatus.skipped_no_snapshot,
        AgentInvocationStatus.skipped_mode,
        AgentInvocationStatus.skipped_no_scope,
        AgentInvocationStatus.skipped_no_approval,
        AgentInvocationStatus.skipped_no_patch,
        AgentInvocationStatus.skipped_no_prerequisite,
    }
)


def _agent_for_decision(
    conn: Connection, decision_id: str, agent: AgentName
) -> FirstResponderInvocation | None:
    row = (
        conn.execute(
            sa.select(first_responder_invocations)
            .where(
                first_responder_invocations.c.decision_id == decision_id,
                first_responder_invocations.c.agent == agent.value,
            )
            .order_by(first_responder_invocations.c.created_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return FirstResponderInvocation.model_validate(row["doc"])


def _scout_for_decision(conn: Connection, decision_id: str) -> FirstResponderInvocation | None:
    return _agent_for_decision(conn, decision_id, AgentName.scout)


def _load_invocation_for_update(
    conn: Connection, invocation_id: str
) -> FirstResponderInvocation | None:
    row = (
        conn.execute(
            sa.select(first_responder_invocations)
            .where(first_responder_invocations.c.id == invocation_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return FirstResponderInvocation.model_validate(row["doc"])


def _write_invocation(
    conn: Connection, inv: FirstResponderInvocation, *, status: AgentInvocationStatus
) -> None:
    conn.execute(
        sa.update(first_responder_invocations)
        .where(first_responder_invocations.c.id == inv.id)
        .values(status=status.value, doc=inv.model_dump(mode="json"))
    )


def _other_active_agent_claim(conn: Connection, invocation_id: str, owner: str) -> bool:
    """True when another worker still holds a valid lease for this invocation."""
    row = conn.execute(
        sa.select(jobs_t.c.id)
        .where(
            jobs_t.c.kind == "live_defense_agent_step",
            jobs_t.c.status == "running",
            jobs_t.c.lease_owner.is_not(None),
            jobs_t.c.lease_owner != owner,
            jobs_t.c.lease_expires_at > utcnow(),
            jobs_t.c.payload["invocation_id"].astext == invocation_id,
        )
        .limit(1)
    ).first()
    return row is not None


def _uncertain_outcome(inv: FirstResponderInvocation) -> FirstResponderInvocation:
    execution = inv.execution or AgentExecutionRecord(started_at=utcnow())
    return inv.model_copy(
        update={
            "status": AgentInvocationStatus.failed,
            "reason": (
                "UNCERTAIN_PROVIDER_OUTCOME: previous billable request has no recorded "
                "response; refusing automatic replay"
            ),
            "execution": execution.model_copy(
                update={
                    "error_code": "UNCERTAIN_PROVIDER_OUTCOME",
                    "uncertain_provider_outcome": True,
                    "ended_at": utcnow(),
                    "execution_integrity": ExecutionIntegrity.failed,
                }
            ),
        }
    )


def _deadline_outcome(inv: FirstResponderInvocation) -> FirstResponderInvocation:
    execution = inv.execution or AgentExecutionRecord(started_at=utcnow())
    return inv.model_copy(
        update={
            "status": AgentInvocationStatus.failed,
            "reason": (
                "DEADLINE_EXCEEDED: overall investigation deadline refuses another provider call"
            ),
            "execution": execution.model_copy(
                update={
                    "error_code": "BUDGET_EXHAUSTED",
                    "ended_at": utcnow(),
                    "execution_integrity": ExecutionIntegrity.failed,
                }
            ),
        }
    )


def _merge_cancelled_evidence(
    current: FirstResponderInvocation, incoming: FirstResponderInvocation
) -> FirstResponderInvocation:
    """Keep a cancelled invocation cancelled, but persist later transcript/cost."""
    incoming_exec = incoming.execution
    if incoming_exec is None:
        return current
    current_exec = current.execution
    richer = (
        current_exec is None
        or (
            incoming_exec.transcript_artifact_id
            and not (current_exec.transcript_artifact_id if current_exec else None)
        )
        or bool(incoming_exec.provider_attempts)
    )
    if not richer:
        return current
    merged = current.model_copy(
        update={
            "execution": incoming_exec,
            "provider_mode": incoming.provider_mode or current.provider_mode,
        }
    )
    return merged


def _commit_invocation(
    conn: Connection, inv: FirstResponderInvocation, *, status: AgentInvocationStatus
) -> FirstResponderInvocation:
    """Persist an invocation without replacing a successful or cancelled result."""
    current = _load_invocation_for_update(conn, inv.id)
    if current is None:
        _write_invocation(conn, inv, status=status)
        return inv
    if current.status is AgentInvocationStatus.executed:
        return current
    if current.status is AgentInvocationStatus.cancelled:
        merged = _merge_cancelled_evidence(current, inv)
        if merged is not current:
            _write_invocation(conn, merged, status=AgentInvocationStatus.cancelled)
        return merged
    if current.status in _TERMINAL_INVOCATION and status is not AgentInvocationStatus.executed:
        return current
    updated = inv.model_copy(update={"status": status})
    _write_invocation(conn, updated, status=status)
    return updated


def _invocation_result(inv: FirstResponderInvocation) -> dict[str, Any]:
    execution = inv.execution
    return {
        "status": inv.status.value,
        "agent": inv.agent.value,
        "confirmed": False,
        "execution_integrity": (
            execution.execution_integrity.value if execution is not None else None
        ),
        "predicted_probability": None,
        "idempotent_replay": inv.status in _TERMINAL_INVOCATION,
    }


def _enqueue_agent_step(
    conn: Connection,
    *,
    decision: FirstResponderDecision,
    inv: FirstResponderInvocation,
    agent: AgentName,
    capability_class: AgentCapabilityClass,
) -> FirstResponderInvocation:
    job_id, _ = jobq.enqueue(
        conn,
        "live_defense_agent_step",
        {
            "project_id": decision.project_id,
            "decision_id": decision.id,
            "invocation_id": inv.id,
            "agent": agent.value,
            "capability_class": capability_class.value,
            "deadline_at": decision.deadline_at.isoformat() if decision.deadline_at else None,
        },
        dedupe_key=f"live_defense_agent_step:{decision.id}:{agent.value}",
        priority=90 if agent is AgentName.diagnose else 70,
        deadline_at=decision.deadline_at,
    )
    updated = inv.model_copy(update={"job_id": job_id})
    conn.execute(
        sa.update(first_responder_invocations)
        .where(first_responder_invocations.c.id == inv.id)
        .values(doc=updated.model_dump(mode="json"))
    )
    return updated


def _schedule_diagnose_after_scout(
    conn: Connection, decision: FirstResponderDecision, scout: FirstResponderInvocation
) -> None:
    diagnose = _agent_for_decision(conn, decision.id, AgentName.diagnose)
    if diagnose is None or diagnose.status in _TERMINAL_INVOCATION:
        return
    hypotheses = None
    if scout.status is AgentInvocationStatus.executed and scout.execution:
        hypotheses = load_scout_output(conn, scout.execution.output_artifact_id)
    if scout.status is not AgentInvocationStatus.executed or not (
        hypotheses and hypotheses.get("hypotheses")
    ):
        skipped = diagnose.model_copy(
            update={
                "status": AgentInvocationStatus.skipped_no_prerequisite,
                "reason": "Diagnose skipped: Scout did not produce executed hypotheses",
                "provider_mode": "none",
            }
        )
        _commit_invocation(conn, skipped, status=AgentInvocationStatus.skipped_no_prerequisite)
        return
    registry = {item.name: item for item in load_registry().agents}
    cap = registry.get(AgentName.diagnose)
    if cap is None:
        return
    _enqueue_agent_step(
        conn,
        decision=decision,
        inv=diagnose,
        agent=AgentName.diagnose,
        capability_class=cap.capability_class,
    )


@handler("live_defense_agent_step")
def live_defense_agent_step(ctx: JobContext) -> dict[str, Any]:
    """Run a gated agent step without faking success or deploying Patch.

    Scout (and Diagnose after Scout) execute the real security-agent loop when a
    trusted mapping and snapshot exist. Probe still requires AuthorizedScope and
    is not auto-run. Patch never deploys. Verify eligibility is not verification.
    Trace waits for confirmed Probe evidence. LLM prose cannot confirm a threat.
    """
    ctx.check_cancelled()
    payload = ctx.job.payload
    agent = AgentName(payload["agent"])
    run_live = False
    repo_id: str | None = None
    mapping_id: str | None = None
    commit_sha: str | None = None
    snapshot_id: str | None = None
    scout_output: dict[str, Any] | None = None
    with transaction() as conn:
        acquire_first_responder_lock(conn, payload["project_id"])
        _abort_if_off_or_cancelled(
            conn,
            ctx,
            project_id=payload["project_id"],
            invocation_id=payload["invocation_id"],
        )
        inv = _load_invocation_for_update(conn, payload["invocation_id"])
        if inv is None:
            return {"status": "missing_invocation"}
        if inv.status in _TERMINAL_INVOCATION:
            return _invocation_result(inv)
        if provider_ledger.should_refuse_replay(inv):
            failed = _uncertain_outcome(inv)
            committed = _commit_invocation(conn, failed, status=AgentInvocationStatus.failed)
            return _invocation_result(committed)
        if provider_ledger.has_in_flight_attempt(inv):
            raise JobDeferred(
                delay_s=2.0,
                message="billable request in flight; refusing automatic replay",
            )
        remain = remaining_deadline_s(inv.deadline_at)
        if remain is None:
            remain = remaining_deadline_s(payload.get("deadline_at"))
        if remain is not None and remain < MIN_PROVIDER_START_S:
            expired = _deadline_outcome(inv)
            committed = _commit_invocation(conn, expired, status=AgentInvocationStatus.failed)
            return _invocation_result(committed)
        if _other_active_agent_claim(conn, inv.id, ctx.owner):
            raise JobDeferred(delay_s=2.0, message="another worker holds the agent-step lease")
        decision_doc = conn.execute(
            sa.select(first_responder_decisions.c.doc).where(
                first_responder_decisions.c.id == payload["decision_id"]
            )
        ).scalar()
        decision = FirstResponderDecision.model_validate(decision_doc) if decision_doc else None
        status: AgentInvocationStatus
        reason: str
        provider_mode = "none"
        execution = inv.execution
        if agent is AgentName.patch:
            status = AgentInvocationStatus.skipped_no_approval
            reason = "Patch remains worktree-only after human approval; silent deploy is refused"
            if payload.get("deploy"):
                status = AgentInvocationStatus.blocked
                reason = "silent customer deploy is refused"
        elif agent is AgentName.probe:
            mapping = _mapping_by_id(conn, decision.correlation.mapping_id) if decision else None
            repo_id = mapping["repo_id"] if mapping else None
            scope = active_scope_for_repo(conn, payload["project_id"], repo_id) if repo_id else None
            if scope is None:
                status = AgentInvocationStatus.skipped_no_scope
                reason = "Probe denied: no active AuthorizedScope (no external probes)"
            else:
                status = AgentInvocationStatus.completed_unverified
                reason = (
                    "Probe eligible under AuthorizedScope; Phase 7C does not expand Probe "
                    "execution. Confirmation stays unverified until executed template evidence exists."
                )
        elif agent is AgentName.verify:
            status = AgentInvocationStatus.completed_unverified
            reason = (
                "Verify eligibility is not verification. Independently executed "
                "verification evidence is required; this invocation stays unverified."
            )
            if not _patch_candidate(conn, payload["project_id"]):
                status = AgentInvocationStatus.skipped_no_patch
                reason = "Verify skipped: no PatchCandidate"
            elif not _independent_verification(conn, payload["project_id"]):
                status = AgentInvocationStatus.completed_unverified
                reason = (
                    "Verify ran without independently executed verification evidence; "
                    "evidence_state stays unverified"
                )
        elif agent is AgentName.trace:
            status = AgentInvocationStatus.skipped_no_prerequisite
            reason = "Trace follows a confirmed Probe result with executed template evidence; none exists"
        elif agent is AgentName.fortify:
            status = AgentInvocationStatus.skipped_no_prerequisite
            reason = (
                "Fortify requires independently executed verification_results. "
                "Scout hypotheses are not a verified fix."
            )
        elif agent in {AgentName.scout, AgentName.diagnose} and decision is not None:
            mapping_id = decision.correlation.mapping_id
            mapping = _mapping_by_id(conn, mapping_id)
            repo_id = mapping["repo_id"] if mapping else None
            commit_sha = mapping["commit_sha"] if mapping else None
            snapshot_id = _snapshot_id(conn, repo_id, commit_sha)
            if not mapping or not commit_sha:
                status = AgentInvocationStatus.skipped_no_mapping
                reason = "no trusted deploy-to-commit mapping"
            elif snapshot_id is None:
                status = AgentInvocationStatus.skipped_no_snapshot
                reason = "no snapshot for the mapped commit"
            elif agent is AgentName.diagnose:
                scout = _scout_for_decision(conn, decision.id)
                if scout is None or scout.status is AgentInvocationStatus.queued:
                    raise JobDeferred(delay_s=2.0, message="Diagnose waiting for Scout to finish")
                if scout.status is not AgentInvocationStatus.executed:
                    status = AgentInvocationStatus.skipped_no_prerequisite
                    reason = "Diagnose skipped: Scout did not produce executed hypotheses"
                else:
                    scout_output = load_scout_output(
                        conn,
                        scout.execution.output_artifact_id if scout.execution else None,
                    )
                    if not scout_output or not scout_output.get("hypotheses"):
                        status = AgentInvocationStatus.skipped_no_prerequisite
                        reason = "Diagnose skipped: Scout produced no validated hypotheses"
                    else:
                        run_live = True
                        status = AgentInvocationStatus.queued
                        reason = "running"
            else:
                run_live = True
                status = AgentInvocationStatus.queued
                reason = "running"
        else:
            status = AgentInvocationStatus.completed_unverified
            reason = (
                f"{agent.value} investigative step recorded as unverified. "
                "LLM prose is not confirmation."
            )
        if not run_live:
            _abort_if_off_or_cancelled(
                conn,
                ctx,
                project_id=payload["project_id"],
                invocation_id=inv.id,
            )
            updated = inv.model_copy(
                update={
                    "status": status,
                    "reason": reason,
                    "provider_mode": provider_mode,
                    "execution": execution,
                }
            )
            committed = _commit_invocation(conn, updated, status=status)
            if agent is AgentName.scout and decision is not None:
                _schedule_diagnose_after_scout(conn, decision, committed)
            return _invocation_result(committed)

        started = inv.model_copy(
            update={
                "reason": "running",
                "deadline_at": inv.deadline_at or (decision.deadline_at if decision else None),
                "execution": AgentExecutionRecord(
                    started_at=utcnow(),
                    execution_integrity=ExecutionIntegrity.not_run,
                    provider_attempts=(inv.execution.provider_attempts if inv.execution else []),
                ),
            }
        )
        _write_invocation(conn, started, status=AgentInvocationStatus.queued)
        inv = started

    def _cancelled() -> bool:
        try:
            ctx.check_cancelled()
        except JobCancelled:
            return True
        with transaction() as check:
            if load_intent(check, payload["project_id"]).intent is FirstResponderIntent.off:
                return True
            if jobq.is_cancel_requested(check, ctx.job.id):
                return True
            if (
                repository_revocation(
                    check,
                    repo_id=repo_id,
                    application_id=decision.application_id if decision else None,
                    mapping_id=decision.correlation.mapping_id if decision else None,
                )
                is not None
                or investigation_source_revoked(check, mapping_id=mapping_id, repo_id=repo_id)
                is not None
            ):
                return True
        return False

    assert decision is not None and repo_id and commit_sha and snapshot_id
    result: ExecutionOutcome | None = None
    run_error: BaseException | None = None
    try:
        result = run_investigative(
            agent=agent,
            decision=decision,
            invocation=inv,
            repo_id=repo_id,
            commit_sha=commit_sha,
            snapshot_id=snapshot_id,
            scout_output=scout_output,
            cancelled=_cancelled,
        )
    except JobCancelled:
        result = ExecutionOutcome(
            status=AgentInvocationStatus.cancelled,
            reason="job cancelled during agent loop; late output is not executed",
            provider_mode=inv.provider_mode or "none",
            execution=inv.execution or AgentExecutionRecord(started_at=utcnow()),
        )
    except BaseException as exc:
        run_error = exc
        result = ExecutionOutcome(
            status=AgentInvocationStatus.failed,
            reason=f"{agent.value} interrupted: {exc}"[:2000],
            provider_mode=inv.provider_mode or "none",
            execution=inv.execution or AgentExecutionRecord(started_at=utcnow()),
        )
    with transaction() as conn:
        acquire_first_responder_lock(conn, payload["project_id"])
        current = _load_invocation_for_update(conn, inv.id)
        execution = result.execution if result is not None else None
        if current is not None and current.execution is not None and execution is not None:
            execution = execution.model_copy(
                update={
                    "provider_attempts": current.execution.provider_attempts,
                    "uncertain_provider_outcome": current.execution.uncertain_provider_outcome,
                }
            )
        elif current is not None and current.execution is not None:
            execution = current.execution
        status = result.status if result is not None else AgentInvocationStatus.failed
        reason = result.reason if result is not None else "agent step did not persist a result"
        if execution is not None and (
            execution.uncertain_provider_outcome
            or any(item.status.value == "uncertain" for item in execution.provider_attempts)
        ):
            if status is AgentInvocationStatus.executed:
                status = AgentInvocationStatus.failed
                reason = (
                    "UNCERTAIN_PROVIDER_OUTCOME: previous billable request has no recorded "
                    "response; refusing automatic replay"
                )
            if not execution.error_code:
                execution = execution.model_copy(
                    update={
                        "error_code": "UNCERTAIN_PROVIDER_OUTCOME",
                        "uncertain_provider_outcome": True,
                        "ended_at": execution.ended_at or utcnow(),
                        "execution_integrity": ExecutionIntegrity.failed,
                    }
                )
        if jobq.is_cancel_requested(conn, ctx.job.id):
            status = AgentInvocationStatus.cancelled
            reason = "job cancellation requested before final persist; late output is not executed"
        if load_intent(conn, payload["project_id"]).intent is FirstResponderIntent.off:
            status = AgentInvocationStatus.cancelled
            reason = "first-responder OFF is effective; late output is not executed"
        if (
            repository_revocation(
                conn,
                repo_id=repo_id,
                application_id=decision.application_id if decision else None,
                mapping_id=decision.correlation.mapping_id if decision else None,
            )
            is not None
            or repository_revocation_locked(
                conn,
                repo_id=repo_id,
                application_id=decision.application_id if decision else None,
                mapping_id=decision.correlation.mapping_id if decision else None,
            )
            is not None
            or investigation_source_revoked(conn, mapping_id=mapping_id, repo_id=repo_id)
            is not None
        ):
            status = AgentInvocationStatus.cancelled
            reason = "repository revoked before final persist; late output is not executed"
        updated = inv.model_copy(
            update={
                "status": status,
                "reason": reason,
                "provider_mode": (
                    result.provider_mode if result is not None else inv.provider_mode
                ),
                "execution": execution,
            }
        )
        committed = _commit_invocation(conn, updated, status=status)
        if committed.agent is AgentName.scout and decision is not None:
            _schedule_diagnose_after_scout(conn, decision, committed)
        payload_out = _invocation_result(committed)
        persist_cancelled = committed.status is AgentInvocationStatus.cancelled
    if persist_cancelled:
        raise JobCancelled(f"{ctx.job.id}: cancelled before final persist")
    if run_error is not None:
        raise run_error
    return payload_out


def reconcile_off(conn: Connection, project_id: str) -> dict[str, int]:
    cancelled = cancel_first_responder_jobs(conn, project_id)
    queued = (
        conn.execute(
            sa.select(first_responder_invocations.c.id).where(
                first_responder_invocations.c.project_id == project_id,
                first_responder_invocations.c.status == AgentInvocationStatus.queued.value,
            )
        )
        .scalars()
        .all()
    )
    for invocation_id in queued:
        _mark_invocation_cancelled(
            conn,
            invocation_id,
            "first-responder OFF is effective; queued step will not run",
        )
    return {"cancelled_first_responder_jobs": cancelled}
