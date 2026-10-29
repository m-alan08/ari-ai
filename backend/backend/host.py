"""AssessmentHost — the persistence + gate API that runtime agents use.

The security-agents orchestrator (Alan) and the code reviewer (Sai) never write the
database directly. They receive an ``AssessmentHost`` and call it to transition
state, open/close AgentRuns, record findings, patches, Verify verdicts and
approvals, store artifacts and charge LLM usage. Deterministic gates live here,
independent of any prompt:

* state transitions must be legal (``shared.assessment.ALLOWED_TRANSITIONS``);
* the scope must be active/unexpired before work and at every transition;
* budget (tokens / USD / wall clock) is enforced on every charge;
* cancellation is observed at every call;
* a VerificationResult must come from a ``verify`` AgentRun that is not the run
  that produced the patch (Verify independence);
* REMEDIATION is only reachable through an approved ``remediation`` approval.
* approval-pause checkpoints are hashed artifacts, keyed per assessment + namespace;
  ``claim_checkpoint`` consumes the latest payload under an assessment row lock.

Integration contract (published V0, see coordination message):

    class Orchestrator(Protocol):
        def run(self, host: AssessmentHost) -> None: ...

``settings.orchestrator_factory`` names a ``module:callable`` returning one. The
worker calls ``run`` for a fresh assessment and again after an approval decision
(``host.assessment.state == AWAITING_APPROVAL``).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.engine import Connection

from backend import audit
from backend.artifacts import ArtifactStore
from backend.db import (
    agent_runs,
    approvals,
    assessment_events,
    assessments,
    authorized_scopes,
    finding_records,
    findings,
    jobs,
    monitoring_events,
    patch_candidates,
    projects,
    reassessment_requests,
    transaction,
    verification_results,
)
from shared.assessment import (
    TERMINAL_STATES,
    AgentName,
    AgentRun,
    AgentRunStatus,
    Assessment,
    AssessmentState,
    ToolCallRecord,
    can_transition,
)
from shared.audit import ActorType
from shared.base import utcnow
from shared.errors import ErrorBody, ErrorCode
from shared.events import StreamEvent, StreamEventType
from shared.evidence import ArtifactKind, ArtifactRef, EvidenceKind
from shared.findings import (
    AttackPath,
    Finding,
    FindingStatus,
    HardeningRecommendation,
    Hypothesis,
    PatchCandidate,
    RemediationPlan,
    RootCause,
    SeverityAssessment,
    StatusChange,
    ValidationResult,
    VerificationResult,
)
from shared.ids import IdPrefix, new_id
from shared.intelligence import SnapshotRef
from shared.scope import Approval, ApprovalStatus, ApprovalSubject, AuthorizedScope, ScopeStatus
from shared.standards import FindingStandards
from shared.tenancy import Usage


class HostError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class Orchestrator(Protocol):
    def run(self, host: AssessmentHost) -> None: ...


_RECORD_TYPES: dict[type[BaseModel], str] = {
    Hypothesis: "hypothesis",
    ValidationResult: "validation_result",
    AttackPath: "attack_path",
    RootCause: "root_cause",
    RemediationPlan: "remediation_plan",
    HardeningRecommendation: "hardening",
}

# Finding lifecycle (docs/12). Agents report a status; the host applies it only along these
# edges and only when the persisted evidence supports it (see _STATUS_EVIDENCE).
_S = FindingStatus
_STATUS_EDGES: dict[FindingStatus, frozenset[FindingStatus]] = {
    _S.hypothesis: frozenset({_S.validated, _S.refuted, _S.inconclusive}),
    _S.inconclusive: frozenset({_S.validated, _S.refuted}),
    _S.validated: frozenset({_S.diagnosed, _S.inconclusive}),
    _S.diagnosed: frozenset({_S.patch_proposed, _S.unresolved}),
    _S.patch_proposed: frozenset({_S.verified, _S.patch_rejected, _S.unresolved}),
    _S.patch_rejected: frozenset({_S.patch_proposed, _S.unresolved}),
    _S.verified: frozenset({_S.hardened, _S.closed}),
    _S.hardened: frozenset({_S.closed}),
}
_ANYTIME = frozenset({_S.needs_recheck, _S.accepted_risk, _S.closed})


def linked_pull_requests(conn: Connection, assessment: Assessment) -> list[int]:
    """Every pull request the assessment's GitHub events were linked to, in link order."""
    request_id = assessment.reassessment_request_id
    if request_id is None:
        return []
    request = conn.execute(
        sa.select(reassessment_requests.c.doc).where(reassessment_requests.c.id == request_id)
    ).scalar_one_or_none()
    if not request:
        return []
    event_ids = list(request.get("event_ids", []) or [])
    docs = {
        doc["id"]: doc
        for doc in conn.execute(
            sa.select(monitoring_events.c.doc).where(monitoring_events.c.id.in_(event_ids or [""]))
        ).scalars()
    }
    linked: list[int] = []
    for event_id in event_ids:
        number = (docs.get(event_id) or {}).get("pr_number")
        if number and int(number) not in linked:
            linked.append(int(number))
    return linked


def _enqueue_pr_review(conn: Connection, assessment: Assessment) -> None:
    """One review job per linked pull request. ``repo_id`` in the payload lets a
    repository disconnect cancel queued and running review jobs. A job queued under the
    older ``prreview:{assessment}`` key already covers the first linked PR."""
    from backend import jobs
    from backend.db import jobs as jobs_table

    linked = linked_pull_requests(conn, assessment)
    if not linked:
        return
    legacy = conn.execute(
        sa.select(jobs_table.c.id).where(jobs_table.c.dedupe_key == f"prreview:{assessment.id}")
    ).scalar_one_or_none()
    for pr_number in linked:
        if legacy is not None and pr_number == linked[0]:
            continue
        jobs.enqueue(
            conn,
            "post_pr_review",
            {"assessment_id": assessment.id, "repo_id": assessment.repo_id, "pr_number": pr_number},
            dedupe_key=f"prreview:{assessment.id}:pr:{pr_number}",
            priority=80,
        )


def _emit(
    conn: Connection, assessment_id: str, etype: StreamEventType, message: str, **fields: Any
) -> StreamEvent:
    seq = conn.execute(
        sa.update(assessments)
        .where(assessments.c.id == assessment_id)
        .values(event_seq=assessments.c.event_seq + 1)
        .returning(assessments.c.event_seq)
    ).scalar_one()
    ev = StreamEvent(
        seq=seq,
        assessment_id=assessment_id,
        type=etype,
        message=message[:500],
        at=utcnow(),
        **fields,
    )
    conn.execute(
        sa.insert(assessment_events).values(
            assessment_id=assessment_id, seq=seq, type=etype.value, doc=ev.model_dump(mode="json")
        )
    )
    conn.execute(sa.text("SELECT pg_notify('assessment_events', :id)"), {"id": assessment_id})
    return ev


def emit_event(
    conn: Connection, assessment_id: str, etype: StreamEventType, message: str, **fields: Any
) -> StreamEvent:
    return _emit(conn, assessment_id, etype, message, **fields)


def scope_problem(scope: AuthorizedScope, repo_id: str) -> str | None:
    if scope.status is not ScopeStatus.active:
        return f"scope {scope.id} is {scope.status.value}"
    if scope.expires_at <= utcnow():
        return f"scope {scope.id} expired at {scope.expires_at.isoformat()}"
    if not any(t.repo_id == repo_id for t in scope.targets):
        return f"scope {scope.id} does not cover repository {repo_id}"
    return None


class AssessmentHost:
    def __init__(
        self, assessment_id: str, *, job_ctx: Any | None = None, store: ArtifactStore | None = None
    ) -> None:
        self.assessment_id = assessment_id
        self._job_ctx = job_ctx
        self.store = store or ArtifactStore()
        self._started_monotonic = time.monotonic()
        with transaction() as conn:
            row = self._row(conn)
            self.project_id: str = row["project_id"]
            self.repo_id: str = row["repo_id"]
            self.org_id: str = conn.execute(
                sa.select(projects.c.org_id).where(projects.c.id == self.project_id)
            ).scalar_one()

    # ------------------------------------------------------------------ reads
    def intelligence_service(self, *, include_prefix: str = "") -> Any:
        from backend.intelligence import repository_service

        return repository_service(self.project_id, self.repo_id, include_prefix=include_prefix)

    def attach_snapshot(self, ref: SnapshotRef) -> bool:
        from backend.intel_store import lock_ready_snapshot

        ref = SnapshotRef.model_validate(ref.model_dump())
        if (
            ref.project_id != self.project_id
            or ref.repo_id != self.repo_id
            or ref.commit_sha != self.assessment.commit_sha
        ):
            raise HostError(ErrorCode.SCHEMA_INVALID, "Snapshot does not match assessment")
        with transaction() as conn:
            if not lock_ready_snapshot(conn, ref):
                return False
            current = Assessment.model_validate(self._row(conn, lock=True)["doc"])
            if current.snapshot_id and current.snapshot_id != ref.snapshot_id:
                raise HostError(ErrorCode.CONFLICT_STATE, "Assessment already has another snapshot")
            current.snapshot_id = ref.snapshot_id
            current.updated_at = utcnow()
            conn.execute(
                sa.update(assessments)
                .where(assessments.c.id == self.assessment_id)
                .values(doc=current.model_dump(mode="json"))
            )
        return True

    def _row(self, conn: Connection, *, lock: bool = False) -> dict[str, Any]:
        q = sa.select(assessments).where(assessments.c.id == self.assessment_id)
        if lock:
            q = q.with_for_update()
        row = conn.execute(q).mappings().first()
        if row is None:
            raise HostError(ErrorCode.NOT_FOUND, f"assessment {self.assessment_id} not found")
        return dict(row)

    @property
    def assessment(self) -> Assessment:
        with transaction() as conn:
            return Assessment.model_validate(self._row(conn)["doc"])

    @property
    def scope(self) -> AuthorizedScope:
        with transaction() as conn:
            doc = conn.execute(
                sa.select(authorized_scopes.c.doc).where(
                    authorized_scopes.c.id == self.assessment.scope_id
                )
            ).scalar_one()
        return AuthorizedScope.model_validate(doc)

    def approvals(self) -> list[Approval]:
        with transaction() as conn:
            docs = conn.execute(
                sa.select(approvals.c.doc)
                .where(approvals.c.assessment_id == self.assessment_id)
                .order_by(approvals.c.created_at)
            ).scalars()
            return [Approval.model_validate(d) for d in docs]

    def findings(self) -> list[Finding]:
        with transaction() as conn:
            docs = conn.execute(
                sa.select(findings.c.doc)
                .where(findings.c.assessment_id == self.assessment_id)
                .order_by(findings.c.created_at)
            ).scalars()
            return [Finding.model_validate(d) for d in docs]

    # ------------------------------------------------------------------ gates
    def check_cancelled(self) -> None:
        if self._job_ctx is not None:
            self._job_ctx.check_cancelled()
        with transaction() as conn:
            if self._row(conn)["cancel_requested"]:
                raise HostError(ErrorCode.CANCELLED, "assessment cancellation requested")

    def require_active_scope(self) -> AuthorizedScope:
        scope = self.scope
        problem = scope_problem(scope, self.repo_id)
        if problem:
            raise HostError(ErrorCode.SCOPE_DENIED, problem)
        return scope

    def _budget_problem(self, a: Assessment) -> str | None:
        b, u = a.budget, a.usage
        if u.input_tokens + u.output_tokens > b.max_tokens:
            return f"token budget {b.max_tokens} exceeded"
        if u.usd > b.max_usd:
            return f"USD budget {b.max_usd} exceeded"
        if time.monotonic() - self._started_monotonic > b.max_wall_clock_s:
            return f"wall-clock budget {b.max_wall_clock_s}s exceeded"
        return None

    def ensure_budget(self) -> None:
        problem = self._budget_problem(self.assessment)
        if problem:
            self.terminate(AssessmentState.BUDGET_EXHAUSTED, ErrorCode.BUDGET_EXHAUSTED, problem)
            raise HostError(ErrorCode.BUDGET_EXHAUSTED, problem)

    def charge(self, usage: Usage) -> None:
        """Add usage to the assessment total; enforces the budget afterwards."""
        with transaction() as conn:
            self._add_usage(conn, usage)
        self.ensure_budget()

    def _add_usage(self, conn: Connection, usage: Usage) -> None:
        row = self._row(conn, lock=True)
        a = Assessment.model_validate(row["doc"])
        total = a.usage.model_copy(
            update={k: getattr(a.usage, k) + getattr(usage, k) for k in Usage.model_fields}
        )
        a = a.model_copy(update={"usage": total, "updated_at": utcnow()})
        conn.execute(
            sa.update(assessments)
            .where(assessments.c.id == self.assessment_id)
            .values(doc=a.model_dump(mode="json"), updated_at=sa.func.now())
        )

    # ------------------------------------------------------------- transitions
    def transition(self, target: AssessmentState, message: str = "") -> Assessment:
        self.check_cancelled()
        if target not in TERMINAL_STATES:
            self.require_active_scope()
        if target is AssessmentState.REMEDIATION and not any(
            ap.subject_type is ApprovalSubject.remediation and ap.status is ApprovalStatus.approved
            for ap in self.approvals()
        ):
            raise HostError(
                ErrorCode.APPROVAL_REQUIRED, "REMEDIATION requires an approved remediation approval"
            )
        return self._set_state(target, message or f"state -> {target.value}")

    def terminate(
        self, target: AssessmentState, code: ErrorCode | None, message: str
    ) -> Assessment:
        if target not in TERMINAL_STATES:
            raise ValueError("terminate() requires a terminal state")
        error = ErrorBody(code=code, message=message[:500]) if code else None
        return self._set_state(target, message, error=error)

    def _set_state(
        self, target: AssessmentState, message: str, error: ErrorBody | None = None
    ) -> Assessment:
        with transaction() as conn:
            row = self._row(conn, lock=True)
            a = Assessment.model_validate(row["doc"])
            if a.state is target:
                return a
            if not can_transition(a.state, target):
                raise HostError(
                    ErrorCode.CONFLICT_STATE,
                    f"illegal transition {a.state.value} -> {target.value}",
                )
            now = utcnow()
            update: dict[str, Any] = {"state": target, "updated_at": now}
            if target in TERMINAL_STATES:
                update["completed_at"] = now
            if error is not None:
                update["error"] = error
            new = a.model_copy(update=update)
            conn.execute(
                sa.update(assessments)
                .where(assessments.c.id == self.assessment_id)
                .values(
                    state=target.value, doc=new.model_dump(mode="json"), updated_at=sa.func.now()
                )
            )
            _emit(
                conn,
                self.assessment_id,
                StreamEventType.state_changed,
                message,
                state=target,
                data={"from": a.state.value, **({"error_code": error.code.value} if error else {})},
            )
            if target is AssessmentState.COMPLETED:
                _emit(
                    conn,
                    self.assessment_id,
                    StreamEventType.completed,
                    "assessment completed",
                    state=target,
                )
            elif target in TERMINAL_STATES:
                _emit(
                    conn,
                    self.assessment_id,
                    StreamEventType.failed,
                    message,
                    state=target,
                    data={"error_code": error.code.value if error else None},
                )
            audit.record(
                conn,
                org_id=self.org_id,
                project_id=self.project_id,
                actor_type=ActorType.system,
                actor_id="orchestrator",
                action="assessment.state_changed",
                subject_type="assessment",
                subject_id=self.assessment_id,
                details={"from": a.state.value, "to": target.value},
            )
            if target in (AssessmentState.COMPLETED, AssessmentState.AWAITING_APPROVAL):
                _enqueue_pr_review(conn, new)
            return new

    def progress(self, message: str, *, agent: AgentName | None = None, **data: Any) -> None:
        with transaction() as conn:
            _emit(
                conn,
                self.assessment_id,
                StreamEventType.progress,
                message,
                state=AssessmentState(self._row(conn)["state"]),
                agent=agent,
                data=data,
            )

    def save_checkpoint(self, namespace: str, payload: dict[str, Any]) -> None:
        """Publish an assessment-scoped pointer to a hashed JSON artifact.

        Salvaged from PR #35 (``feat/alan/seven-agent-runtime``) so a worker
        restart can rebuild the orchestrator. Latest write for a namespace wins.
        """
        self.check_cancelled()
        if not namespace or len(namespace) > 100:
            raise HostError(ErrorCode.VALIDATION_FAILED, "invalid checkpoint namespace")
        with transaction() as conn:
            self._row(conn)
            ref = self.store.put_json(
                conn, payload, kind=ArtifactKind.other, project_id=self.project_id
            )
            _emit(
                conn,
                self.assessment_id,
                StreamEventType.progress,
                "checkpoint saved",
                data={"checkpoint_namespace": namespace, "checkpoint_artifact_id": ref.id},
            )

    def load_checkpoint(self, namespace: str) -> dict[str, Any] | None:
        """Load only this assessment's latest checkpoint, checking artifact integrity."""
        with transaction() as conn:
            self._row(conn)
            event = self._latest_checkpoint_event(conn, namespace)
            if event is None or event.get("data", {}).get("checkpoint_consumed"):
                return None
            row, content = self.store.get(conn, event["data"]["checkpoint_artifact_id"])
            if row["project_id"] != self.project_id:
                raise HostError(ErrorCode.VALIDATION_FAILED, "checkpoint project mismatch")
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise HostError(ErrorCode.VALIDATION_FAILED, "checkpoint must be an object")
        return payload

    def claim_checkpoint(self, namespace: str) -> dict[str, Any] | None:
        """Atomically consume this assessment's latest checkpoint for ``namespace``.

        Serialized with ``FOR UPDATE`` on the assessment row so two workers cannot
        both resume Patch/Verify from the same payload. Returns the payload when
        this caller uniquely claimed it, or ``None`` when a previous caller already
        consumed it. Raises ``CONFLICT_STATE`` when no checkpoint was ever saved.
        """
        self.check_cancelled()
        if not namespace or len(namespace) > 100:
            raise HostError(ErrorCode.VALIDATION_FAILED, "invalid checkpoint namespace")
        with transaction() as conn:
            self._row(conn, lock=True)
            event = self._latest_checkpoint_event(conn, namespace)
            if event is None:
                raise HostError(ErrorCode.CONFLICT_STATE, "no checkpoint to claim")
            data = event.get("data", {})
            if data.get("checkpoint_consumed") and self._claim_still_held(conn, data):
                return None
            artifact_id = data["checkpoint_artifact_id"]
            row, content = self.store.get(conn, artifact_id)
            if row["project_id"] != self.project_id:
                raise HostError(ErrorCode.VALIDATION_FAILED, "checkpoint project mismatch")
            payload = json.loads(content)
            if not isinstance(payload, dict):
                raise HostError(ErrorCode.VALIDATION_FAILED, "checkpoint must be an object")
            _emit(
                conn,
                self.assessment_id,
                StreamEventType.progress,
                "checkpoint consumed",
                data={
                    "checkpoint_namespace": namespace,
                    "checkpoint_artifact_id": artifact_id,
                    "checkpoint_consumed": True,
                    "claimed_by_job": getattr(getattr(self._job_ctx, "job", None), "id", None),
                },
            )
            return payload

    def _claim_still_held(self, conn: Connection, data: dict[str, Any]) -> bool:
        """A claim lapses when its job is no longer running (it finished without resuming,
        died, or lost its lease) while the assessment still waits at the approval gate;
        otherwise one aborted resume strands the assessment. A resume that moved the
        assessment on can never be replayed."""
        job_id = data.get("claimed_by_job")
        if not job_id:
            return True  # claims recorded before claimers were tracked stay as they were
        state = conn.execute(
            sa.select(assessments.c.state).where(assessments.c.id == self.assessment_id)
        ).scalar()
        if state != AssessmentState.AWAITING_APPROVAL.value:
            return True
        row = conn.execute(
            sa.select(jobs.c.status, jobs.c.lease_expires_at).where(jobs.c.id == job_id)
        ).first()
        return bool(
            row is not None
            and row.status == "running"
            and (row.lease_expires_at is None or row.lease_expires_at > utcnow())
        )

    def _latest_checkpoint_event(self, conn: Connection, namespace: str) -> dict[str, Any] | None:
        event = conn.execute(
            sa.select(assessment_events.c.doc)
            .where(
                assessment_events.c.assessment_id == self.assessment_id,
                assessment_events.c.doc["data"]["checkpoint_namespace"].astext == namespace,
            )
            .order_by(assessment_events.c.seq.desc())
            .limit(1)
        ).scalar_one_or_none()
        return dict(event) if isinstance(event, dict) else None

    # --------------------------------------------------------------------- llm
    def llm(self) -> Any:
        """LLM gateway bound to this assessment's budget, usage ledger and cancellation."""
        from backend.llm import build_gateway

        return build_gateway(
            charge=self.charge,
            ensure_budget=self.ensure_budget,
            check_cancelled=self.check_cancelled,
        )

    # --------------------------------------------------------------- artifacts
    def store_json(self, value: Any, kind: ArtifactKind = ArtifactKind.tool_output) -> ArtifactRef:
        with transaction() as conn:
            return self.store.put_json(conn, value, kind=kind, project_id=self.project_id)

    def store_text(
        self, text: str, kind: ArtifactKind = ArtifactKind.log, media_type: str = "text/plain"
    ) -> ArtifactRef:
        with transaction() as conn:
            return self.store.put_text(
                conn, text, kind=kind, project_id=self.project_id, media_type=media_type
            )

    def seal_diff(self, diff: str, expected_sha256: str) -> ArtifactRef:
        actual_sha256 = hashlib.sha256(diff.encode()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise HostError(ErrorCode.CONFLICT_STATE, "diff bytes do not match the expected hash")
        with transaction() as conn:
            return self.store.put_bytes(
                conn,
                diff.encode(),
                kind=ArtifactKind.sealed_diff,
                project_id=self.project_id,
                media_type="text/x-diff",
                redacted=False,
            )

    # -------------------------------------------------------------- agent runs
    def start_agent_run(
        self,
        agent: AgentName,
        step_key: str,
        *,
        attempt: int = 1,
        provider_mode: str,
        model_id: str | None,
        prompt_version: str | None,
        input_payload: Any | None = None,
    ) -> str:
        self.check_cancelled()
        self.ensure_budget()
        run_id = new_id(IdPrefix.run)
        input_ref = (
            self.store_json(input_payload, ArtifactKind.other)
            if input_payload is not None
            else None
        )
        run = AgentRun(
            id=run_id,
            assessment_id=self.assessment_id,
            agent=agent,
            step_key=step_key,
            attempt=attempt,
            status=AgentRunStatus.running,
            provider_mode=provider_mode,
            model_id=model_id,
            prompt_version=prompt_version,
            input_artifact_id=input_ref.id if input_ref else None,
            started_at=utcnow(),
        )
        with transaction() as conn:
            conn.execute(
                sa.insert(agent_runs).values(
                    id=run_id,
                    assessment_id=self.assessment_id,
                    agent=agent.value,
                    status=run.status.value,
                    doc=run.model_dump(mode="json"),
                )
            )
            _emit(
                conn,
                self.assessment_id,
                StreamEventType.agent_run_started,
                f"{agent.value} started {step_key}",
                agent=agent,
                agent_run_id=run_id,
                state=AssessmentState(self._row(conn)["state"]),
            )
        return run_id

    def finish_agent_run(
        self,
        run_id: str,
        status: AgentRunStatus,
        *,
        usage: Usage | None = None,
        tool_calls: list[ToolCallRecord] | None = None,
        output: Any | None = None,
        transcript: Any | None = None,
        error: ErrorBody | None = None,
    ) -> AgentRun:
        out_ref = self.store_json(output, ArtifactKind.other) if output is not None else None
        tr_ref = (
            self.store_json(transcript, ArtifactKind.llm_transcript)
            if transcript is not None
            else None
        )
        with transaction() as conn:
            doc = conn.execute(
                sa.select(agent_runs.c.doc).where(agent_runs.c.id == run_id).with_for_update()
            ).scalar_one()
            run = AgentRun.model_validate(doc)
            if run.status is not AgentRunStatus.running:
                raise HostError(
                    ErrorCode.CONFLICT_STATE, f"agent run {run_id} already {run.status.value}"
                )
            run = run.model_copy(
                update={
                    "status": status,
                    "usage": usage or run.usage,
                    "tool_calls": tool_calls or run.tool_calls,
                    "output_artifact_id": out_ref.id if out_ref else None,
                    "transcript_artifact_id": tr_ref.id if tr_ref else None,
                    "error": error,
                    "ended_at": utcnow(),
                }
            )
            AgentRun.model_validate(run.model_dump())
            conn.execute(
                sa.update(agent_runs)
                .where(agent_runs.c.id == run_id)
                .values(status=status.value, doc=run.model_dump(mode="json"))
            )
            if run.tool_calls:
                # The gateway charges tokens/LLM calls per request; tool calls are counted here,
                # once, when the run's tool history is final.
                self._add_usage(conn, Usage(tool_calls=len(run.tool_calls)))
            _emit(
                conn,
                self.assessment_id,
                StreamEventType.agent_run_finished,
                f"{run.agent.value} {status.value}",
                agent=run.agent,
                agent_run_id=run_id,
                data={
                    "usage": run.usage.model_dump(),
                    "error_code": error.code.value if error else None,
                },
            )
        return run

    def agent_run(self, run_id: str) -> AgentRun:
        with transaction() as conn:
            doc = conn.execute(
                sa.select(agent_runs.c.doc).where(agent_runs.c.id == run_id)
            ).scalar()
        if doc is None:
            raise HostError(
                ErrorCode.NOT_FOUND,
                f"agent run {run_id} not found (records must cite a host run id)",
            )
        return AgentRun.model_validate(doc)

    # --------------------------------------------------------------- findings
    def record_finding(self, finding: Finding, *, actor: str) -> Finding:
        if finding.assessment_id != self.assessment_id:
            raise HostError(ErrorCode.VALIDATION_FAILED, "finding belongs to another assessment")
        paths = sorted(
            {e.path for e in finding.evidence if e.kind is EvidenceKind.source_span and e.path}
        )
        with transaction() as conn:
            existing = conn.execute(
                sa.select(findings.c.doc).where(findings.c.id == finding.id).with_for_update()
            ).scalar()
            if existing is None:
                conn.execute(
                    sa.insert(findings).values(
                        id=finding.id,
                        project_id=self.project_id,
                        assessment_id=self.assessment_id,
                        kind=finding.kind.value,
                        status=finding.status.value,
                        paths=paths,
                        doc=finding.model_dump(mode="json"),
                    )
                )
            else:
                conn.execute(
                    sa.update(findings)
                    .where(findings.c.id == finding.id)
                    .values(
                        status=finding.status.value,
                        paths=paths,
                        doc=finding.model_dump(mode="json"),
                    )
                )
            _emit(
                conn,
                self.assessment_id,
                StreamEventType.finding_updated,
                f"{finding.kind.value} finding {finding.status.value}: {finding.title}",
                finding_id=finding.id,
                data={"status": finding.status.value, "actor": actor},
            )
        return finding

    def record_standards(
        self,
        finding_id: str,
        standards: FindingStandards,
        *,
        severity: SeverityAssessment | None = None,
    ) -> None:
        """Update classifications under a row lock without reverting finding state/history."""
        checked = FindingStandards.model_validate(standards.model_dump(mode="json"))
        with transaction() as conn:
            doc = conn.execute(
                sa.select(findings.c.doc)
                .where(
                    findings.c.id == finding_id,
                    findings.c.assessment_id == self.assessment_id,
                )
                .with_for_update()
            ).scalar()
            if doc is None:
                raise HostError(ErrorCode.NOT_FOUND, "finding not in this assessment")
            finding = Finding.model_validate(doc)
            update: dict[str, Any] = {"standards": checked, "updated_at": utcnow()}
            if severity is not None:
                update["severity"] = SeverityAssessment.model_validate(
                    severity.model_dump(mode="json")
                )
            refreshed = Finding.model_validate(
                finding.model_copy(update=update).model_dump(mode="json")
            )
            conn.execute(
                sa.update(findings)
                .where(findings.c.id == finding_id)
                .values(doc=refreshed.model_dump(mode="json"))
            )
            _emit(
                conn,
                self.assessment_id,
                StreamEventType.finding_updated,
                "standards classification updated",
                finding_id=finding_id,
                data={"actor": "system:standards", "classification_only": True},
            )

    def set_finding_status(
        self, finding_id: str, status: FindingStatus | str, *, actor: str, reason: str | None = None
    ) -> Finding:
        """Apply a lifecycle transition with a StatusChange entry (idempotent for same status).

        Deterministic gates: the edge must be in the docs/12 lifecycle, and validated /
        refuted / patch_proposed / verified require the matching persisted evidence (a
        ValidationResult with that outcome, a PatchCandidate, an accepted Verify).
        """
        target = FindingStatus(status)
        with transaction() as conn:
            doc = conn.execute(
                sa.select(findings.c.doc)
                .where(
                    findings.c.id == finding_id,
                    findings.c.assessment_id == self.assessment_id,
                )
                .with_for_update()
            ).scalar()
            if doc is None:
                raise HostError(ErrorCode.NOT_FOUND, f"finding {finding_id} not in this assessment")
            finding = Finding.model_validate(doc)
            if finding.status is target:
                return finding
            if target not in _ANYTIME and target not in _STATUS_EDGES.get(
                finding.status, frozenset()
            ):
                raise HostError(
                    ErrorCode.CONFLICT_STATE,
                    f"finding {finding_id}: {finding.status.value} -> {target.value} not allowed",
                )
            missing = self._status_evidence_missing(conn, finding_id, target)
            if missing:
                raise HostError(ErrorCode.VALIDATION_FAILED, f"{target.value} requires {missing}")
            now = utcnow()
            change = StatusChange(
                from_status=finding.status, to_status=target, at=now, actor=actor, reason=reason
            )
            updated = Finding.model_validate(
                finding.model_copy(
                    update={
                        "status": target,
                        "history": [*finding.history, change],
                        "updated_at": now,
                    }
                ).model_dump(mode="json")
            )
            conn.execute(
                sa.update(findings)
                .where(findings.c.id == finding_id)
                .values(status=target.value, doc=updated.model_dump(mode="json"))
            )
            _emit(
                conn,
                self.assessment_id,
                StreamEventType.finding_updated,
                f"{finding.kind.value} finding {finding.status.value} -> {target.value}: "
                f"{finding.title}",
                finding_id=finding_id,
                data={"status": target.value, "from_status": finding.status.value, "actor": actor},
            )
        return updated

    @staticmethod
    def _status_evidence_missing(conn: Connection, finding_id: str, target: FindingStatus) -> str:
        if target in (FindingStatus.validated, FindingStatus.refuted):
            outcome = "confirmed" if target is FindingStatus.validated else "refuted"
            docs = conn.execute(
                sa.select(finding_records.c.doc).where(
                    finding_records.c.finding_id == finding_id,
                    finding_records.c.record_type == "validation_result",
                )
            ).scalars()
            if not any(d.get("outcome") == outcome for d in docs):
                return f"a persisted {outcome} ValidationResult"
        if target is FindingStatus.patch_proposed:
            if (
                conn.execute(
                    sa.select(patch_candidates.c.id).where(
                        patch_candidates.c.finding_id == finding_id
                    )
                ).first()
                is None
            ):
                return "a persisted PatchCandidate"
        if target is FindingStatus.verified:
            accepted = conn.execute(
                sa.select(verification_results.c.id)
                .join(patch_candidates, patch_candidates.c.id == verification_results.c.patch_id)
                .where(
                    patch_candidates.c.finding_id == finding_id,
                    verification_results.c.verdict == "accepted",
                )
            ).first()
            if accepted is None:
                return "an accepted VerificationResult for this finding's patch"
        return ""

    def record(self, finding_id: str, record: BaseModel) -> None:
        """Persist Hypothesis/ValidationResult/AttackPath/RootCause/RemediationPlan/Hardening."""
        rtype = _RECORD_TYPES.get(type(record))
        if rtype is None:
            raise HostError(
                ErrorCode.VALIDATION_FAILED, f"unsupported record {type(record).__name__}"
            )
        if getattr(record, "finding_id", finding_id) != finding_id:
            raise HostError(ErrorCode.VALIDATION_FAILED, "record finding_id mismatch")
        self._require_own_finding(finding_id)
        with transaction() as conn:
            conn.execute(
                sa.insert(finding_records).values(
                    id=record.id,
                    finding_id=finding_id,
                    record_type=rtype,
                    doc=record.model_dump(mode="json"),
                )
            )

    def _require_own_finding(self, finding_id: str) -> None:
        with transaction() as conn:
            owner = conn.execute(
                sa.select(findings.c.assessment_id).where(findings.c.id == finding_id)
            ).scalar()
        if owner != self.assessment_id:
            raise HostError(ErrorCode.NOT_FOUND, f"finding {finding_id} not in this assessment")

    def record_patch(self, patch: PatchCandidate) -> PatchCandidate:
        self._require_own_finding(patch.finding_id)
        run = self.agent_run(patch.produced_by_run_id)
        if run.assessment_id != self.assessment_id:
            raise HostError(
                ErrorCode.VALIDATION_FAILED, "patch produced by a run of another assessment"
            )
        with transaction() as conn:
            conn.execute(
                sa.insert(patch_candidates).values(
                    id=patch.id, finding_id=patch.finding_id, doc=patch.model_dump(mode="json")
                )
            )
        return patch

    def record_verification(self, result: VerificationResult) -> VerificationResult:
        """Verify independence gate: verdict must come from a distinct, finished verify run."""
        with transaction() as conn:
            pdoc = conn.execute(
                sa.select(patch_candidates.c.doc).where(patch_candidates.c.id == result.patch_id)
            ).scalar()
        if pdoc is None:
            raise HostError(ErrorCode.NOT_FOUND, f"patch {result.patch_id} not found")
        patch = PatchCandidate.model_validate(pdoc)
        self._require_own_finding(patch.finding_id)
        vrun = self.agent_run(result.produced_by_run_id)
        if vrun.agent is not AgentName.verify:
            raise HostError(
                ErrorCode.FORBIDDEN, "VerificationResult must be produced by the verify agent"
            )
        if vrun.id == patch.produced_by_run_id or vrun.assessment_id != self.assessment_id:
            raise HostError(
                ErrorCode.FORBIDDEN, "Verify must run independently of the patch-producing run"
            )
        if vrun.status is not AgentRunStatus.succeeded:
            raise HostError(
                ErrorCode.CONFLICT_STATE,
                "VerificationResult must cite a finished successful verify run",
            )
        if result.verdict.value == "accepted":
            from shared.findings import VerificationCheckName

            names = [check.name for check in result.checks]
            if len(names) != len(VerificationCheckName) or set(names) != set(VerificationCheckName):
                raise HostError(
                    ErrorCode.SCHEMA_INVALID, "Accepted Verify requires all five unique checks"
                )
        if not any(c.evidence for c in result.checks):
            raise HostError(
                ErrorCode.VALIDATION_FAILED, "verification checks need executed evidence"
            )
        with transaction() as conn:
            conn.execute(
                sa.insert(verification_results).values(
                    id=result.id,
                    patch_id=result.patch_id,
                    verdict=result.verdict.value,
                    doc=result.model_dump(mode="json"),
                )
            )
            if result.verdict.value == "accepted":
                from backend import jobs

                jobs.enqueue(
                    conn,
                    "register_verified_regression",
                    {
                        "assessment_id": self.assessment_id,
                        "verification_id": result.id,
                    },
                    dedupe_key=f"regression:{result.id}",
                )
                from backend.delivery import authority

                if authority(conn, self.project_id):
                    jobs.enqueue(
                        conn,
                        "deliver_verified_patch",
                        {"assessment_id": self.assessment_id, "verification_id": result.id},
                        dedupe_key=f"deliver:{result.id}",
                    )
        return result

    # --------------------------------------------------------------- approvals
    def record_roe(self, doc: dict[str, Any]) -> ArtifactRef:
        """Persist the rules-of-engagement document the orchestrator issued at SCOPE_CHECK
        (security_agents.standards.roe, NIST SP 800-115 App. B alignment).

        Checked here, not trusted: it must name this assessment's project and scope, carry the
        scope's current status, and its ``digest`` must equal the sha256 of the document body.
        Stored as a content-addressed artifact; the hash-chained audit event ``roe.recorded``
        on the assessment is the durable pointer the API serves it from.
        """
        if not isinstance(doc, dict):
            raise HostError(ErrorCode.VALIDATION_FAILED, "rules of engagement must be an object")
        scope = self.scope
        if doc.get("project_id") != self.project_id or doc.get("scope_id") != scope.id:
            raise HostError(
                ErrorCode.VALIDATION_FAILED, "rules of engagement name another project or scope"
            )
        body = {k: v for k, v in doc.items() if k != "digest"}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        if not doc.get("digest") or doc["digest"] != digest:
            raise HostError(ErrorCode.VALIDATION_FAILED, "rules of engagement digest mismatch")
        self.require_active_scope()
        ref = self.store_json(doc, ArtifactKind.report)
        with transaction() as conn:
            audit.record(
                conn,
                org_id=self.org_id,
                project_id=self.project_id,
                actor_type=ActorType.system,
                actor_id="orchestrator",
                action="roe.recorded",
                subject_type="assessment",
                subject_id=self.assessment_id,
                details={"artifact_id": ref.id, "roe_id": str(doc.get("id")), "digest": digest},
            )
        # No stream event: the adapter hands the RoE over while settling, which can be after
        # the terminal event, and nothing may follow that in the SSE stream (clients close).
        return ref

    def request_approval(
        self, subject_type: ApprovalSubject, subject_id: str, reason: str
    ) -> Approval:
        ap = Approval(
            id=new_id(IdPrefix.apv),
            project_id=self.project_id,
            subject_type=subject_type,
            subject_id=subject_id,
            status=ApprovalStatus.pending,
            requested_at=utcnow(),
            reason=reason,
        )
        with transaction() as conn:
            conn.execute(
                sa.insert(approvals).values(
                    id=ap.id,
                    project_id=self.project_id,
                    subject_type=subject_type.value,
                    subject_id=subject_id,
                    assessment_id=self.assessment_id,
                    status=ap.status.value,
                    doc=ap.model_dump(mode="json"),
                )
            )
            _emit(
                conn,
                self.assessment_id,
                StreamEventType.approval_requested,
                f"approval requested: {subject_type.value} {subject_id}",
                approval_id=ap.id,
            )
            audit.record(
                conn,
                org_id=self.org_id,
                project_id=self.project_id,
                actor_type=ActorType.system,
                actor_id="orchestrator",
                action="approval.requested",
                subject_type="approval",
                subject_id=ap.id,
                details={"subject_type": subject_type.value, "subject_id": subject_id},
            )
        return ap


def input_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@contextmanager
def agent_run(host: AssessmentHost, agent: AgentName, step_key: str, **kw: Any) -> Iterator[str]:
    """Convenience: open a run, mark failed on exception."""
    run_id = host.start_agent_run(agent, step_key, **kw)
    try:
        yield run_id
    except HostError as exc:
        host.finish_agent_run(
            run_id, AgentRunStatus.failed, error=ErrorBody(code=exc.code, message=str(exc)[:500])
        )
        raise
    except Exception as exc:
        host.finish_agent_run(
            run_id,
            AgentRunStatus.failed,
            error=ErrorBody(code=ErrorCode.INTERNAL, message=f"{type(exc).__name__}: {exc}"[:500]),
        )
        raise
