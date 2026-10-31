"""Deterministic orchestrator: runs one assessment through the seven agents.

The orchestrator owns state, gates and artifacts; agents own role reasoning.
Every step is idempotent by `(assessment_id, step_key, attempt)`, every
transition is logged write-ahead, and every gate (scope, approval, budget, kill
switch) is evaluated here in code — never inside an agent.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from shared.scope import check_category_allowed

from .. import specialist_bridge, standards_bridge
from ..agents import diagnose as diagnose_agent
from ..agents import fortify as fortify_agent
from ..agents import patch as patch_agent
from ..agents import probe as probe_agent
from ..agents import scout as scout_agent
from ..agents import trace as trace_agent
from ..agents import verify as verify_agent
from ..checks.library import CheckLibrary
from ..contracts import (
    AgentName,
    AgentRun,
    Approval,
    AttackPath,
    AuditEvent,
    AuthorizedScope,
    Budget,
    ErrorCode,
    EvidenceRef,
    HardeningRecommendation,
    Hypothesis,
    HypothesisDraft,
    PatchCandidate,
    PlatformError,
    RegressionTest,
    RemediationPlan,
    RootCause,
    SeverityAssessment,
    Usage,
    ValidationOutcome,
    ValidationResult,
    VerificationResult,
    new_id,
    utcnow,
)
from ..intel import CodeIntel, FindingsHistory, hypothesis_key
from ..llm import LLMGateway
from ..runtime import AgentSpec, StepOutcome, StepRecord, run_agent
from ..sandbox.driver import SandboxDriver
from ..sandbox.workspace import (
    fresh_checkout,
    recipe_protected_tests,
    recipe_test_roots,
    resolve_under,
    safe_rel_path,
    test_root_files,
    tree_digest,
    under_test_roots,
)
from ..scope_guard import ActionRequest
from ..scope_guard import check as scope_check
from ..tools import (
    AuditSink,
    CheckRunner,
    PatchApplier,
    PatchWorktree,
    TestRunner,
    ToolContext,
    ToolRegistry,
    default_registry,
    recipe_from_snapshot,
)
from ..tools.services import sandbox_setup
from .states import (
    AssessmentState,
    RemediationState,
    StateLog,
    next_remediation_state,
    transition,
)

DEFAULT_BUDGET = Budget(max_tokens=400_000, max_usd=8.0, max_wall_clock_s=3600.0)


class ApprovalPending(Exception):
    """Raised by an approval callback when a human decision is outstanding.

    The orchestrator stops in `AWAITING_APPROVAL` without a terminal
    transition, so a host can persist the request and re-enter later.
    """

    def __init__(self, finding_id: str, approval_id: str | None = None) -> None:
        super().__init__(f"approval pending for {finding_id}")
        self.finding_id = finding_id
        self.approval_id = approval_id


class AssessmentSink:
    """Hooks a host uses to mirror orchestration into its own store.

    Every method is a no-op by default, so the standalone orchestrator behaves
    exactly as before. A host implementation (see `security_agents.host_adapter`)
    persists each record and emits progress events.
    """

    def on_transition(self, src: AssessmentState, dst: AssessmentState, note: str) -> None: ...

    def on_finding(self, finding: FindingRecord) -> None: ...

    def on_record(self, finding: FindingRecord, record: Any, kind: str) -> None: ...

    def on_patch(self, finding: FindingRecord, candidate: PatchCandidate, diff: str) -> None: ...

    def on_verification(self, finding: FindingRecord, result: VerificationResult) -> None: ...

    def on_status(self, finding: FindingRecord, status: str) -> None: ...

    def on_standards(self, finding: FindingRecord) -> None: ...


@dataclass
class FindingRecord:
    """Everything the platform knows about one security finding."""

    finding_id: str
    hypothesis: Hypothesis
    status: str = "hypothesis"
    validation: ValidationResult | None = None
    attack_path: AttackPath | None = None
    severity: SeverityAssessment | None = None
    root_cause: RootCause | None = None
    plan: RemediationPlan | None = None
    related_sites: list[EvidenceRef] = field(default_factory=list)
    approval: Approval | None = None
    candidates: list[PatchCandidate] = field(default_factory=list)
    verifications: list[VerificationResult] = field(default_factory=list)
    regression_tests: list[RegressionTest] = field(default_factory=list)
    verified_regression_bundle: dict[str, Any] | None = None
    verify_http_evidence: dict[str, Any] | None = None
    recommendations: list[HardeningRecommendation] = field(default_factory=list)
    future_hypotheses: list[HypothesisDraft] = field(default_factory=list)
    monitoring_hints: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    standards: Any = None  # shared.standards.FindingStandards

    def public(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "title": self.hypothesis.title,
            "weakness_class": self.hypothesis.weakness_class,
            "status": self.status,
            "severity": self.severity.level.value if self.severity else None,
            "validation_outcome": self.validation.outcome.value if self.validation else None,
            "verdicts": [v.verdict for v in self.verifications],
            "patch_candidates": len(self.candidates),
            "patch_tests_added": list(self.candidates[-1].tests_added) if self.candidates else [],
            "regression_tests": [t.test_path for t in self.regression_tests],
            "notes": self.notes,
            "standards": standards_bridge.summary(self.standards),
        }


@dataclass
class AssessmentResult:
    assessment_id: str
    state: AssessmentState
    state_log: StateLog
    runs: list[AgentRun] = field(default_factory=list)
    records: list[StepRecord] = field(default_factory=list)
    findings: list[FindingRecord] = field(default_factory=list)
    audit: list[AuditEvent] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    error_code: ErrorCode | None = None
    error_detail: str | None = None
    roe: Any = None

    def runs_by_agent(self) -> dict[str, list[AgentRun]]:
        out: dict[str, list[AgentRun]] = {}
        for r in self.runs:
            out.setdefault(r.agent.value, []).append(r)
        return out

    def agents_executed(self) -> set[str]:
        return {r.agent.value for r in self.runs}

    def _tool_trace(self, run_id: str) -> list[dict[str, Any]]:
        """Per-call trace of one run (name, ok/error, args, result summary), re-redacted and
        bounded, so a failed paid run can be diagnosed from the report alone."""
        from ..redact import redact

        record = next((r for r in self.records if r.run_id == run_id), None)
        if record is None:
            return []
        trace = []
        for call in record.tool_invocations[:40]:
            trace.append(
                {
                    "name": call.name,
                    "ok": call.ok,
                    "error_code": call.error_code,
                    "args": json.dumps(redact(call.args), default=str, sort_keys=True)[:800],
                    "summary": json.dumps(redact(call.summary), default=str, sort_keys=True)[:1200],
                }
            )
        return trace

    def report(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "state": self.state.value,
            "states": self.state_log.states(),
            "provider_modes": sorted({r.provider_mode for r in self.runs}),
            "agents_executed": sorted(self.agents_executed()),
            "runs": [
                {
                    "agent": r.agent.value,
                    "step_key": r.step_key,
                    "attempt": r.attempt,
                    "status": r.status,
                    "model_id": r.model_id,
                    "provider_mode": r.provider_mode,
                    "tool_calls": r.tool_calls,
                    "llm_turns": r.llm_turns,
                    "tokens": r.usage.total_tokens,
                    "usd": round(r.usage.usd, 6),
                    "error_code": r.error_code.value if r.error_code else None,
                    "error_detail": r.error_detail,
                    "tool_trace": self._tool_trace(r.id),
                }
                for r in self.runs
            ],
            "findings": [f.public() for f in self.findings],
            "usage": self.usage.model_dump(mode="json"),
            "audit_actions": [e.action for e in self.audit],
            "specialist_inferences": [
                {k: e.details.get(k) for k in ("agent", "model", "source", "top", "latency_ms", "reason")}
                for e in self.audit
                if e.action == "specialist.inference"
            ],
            "error_code": self.error_code.value if self.error_code else None,
            "error_detail": self.error_detail,
        }


@dataclass
class OrchestratorConfig:
    assessment_id: str
    project_id: str
    org_id: str
    scope: AuthorizedScope
    target_id: str
    requested_by: str
    budget: Budget = field(default_factory=lambda: DEFAULT_BUDGET)
    max_hypotheses_to_validate: int = 3
    max_hypotheses: int = 5
    org_member_count: int = 1
    protected_tests: list[str] = field(default_factory=list)
    test_command_args: tuple[str, ...] = ("-q", "tests")
    probe_check_transport: str = "inprocess"
    verify_check_transport: str = "inprocess"
    # The commit under review: Scout starts from what changed instead of the whole app.
    change_paths: tuple[str, ...] = ()
    change_diff: str = ""


class Orchestrator:
    """Runs the assessment. All gates live in this class, in plain Python."""

    def __init__(
        self,
        config: OrchestratorConfig,
        *,
        intel: CodeIntel,
        gateway: LLMGateway,
        driver: SandboxDriver,
        library: CheckLibrary,
        workdir: Path,
        registry: ToolRegistry | None = None,
        history: FindingsHistory | None = None,
        approve_remediation: Callable[[FindingRecord], Approval | None] | None = None,
        sink: AssessmentSink | None = None,
        step_runner: Callable[..., StepOutcome] | None = None,
        kill_switch: Callable[[], bool] = lambda: False,
        cancelled: Callable[[], bool] = lambda: False,
        now: Callable[[], datetime] = utcnow,
        external_pending: Callable[[], bool] | None = None,
        specialists: Any | None = None,
    ) -> None:
        self.config = config
        # Trained specialist models (advisory). None → the agents run without them.
        self.specialists = specialists if specialists is not None else specialist_bridge.default()
        self.intel = intel
        self.gateway = gateway
        self.driver = driver
        self.library = library
        self.workdir = workdir
        self.registry = registry or default_registry()
        self.history = history or FindingsHistory()
        self.approve_remediation = approve_remediation
        self.sink = sink or AssessmentSink()
        # Injected so a backend host can run the agent turn through its own
        # gateway instead of this package's provider (ADR-0007: one gateway).
        self.step_runner = step_runner
        self.kill_switch = kill_switch
        self.cancelled = cancelled
        self.now = now
        self.external_pending = external_pending

        self.state = AssessmentState.CREATED
        self.state_log = StateLog()
        self.result = AssessmentResult(
            assessment_id=config.assessment_id, state=self.state, state_log=self.state_log
        )
        self.audit = AuditSink()
        self._patch_artifacts: dict[str, dict[str, Any]] = {}
        self._completed_steps: dict[str, StepOutcome] = {}
        self._audit_prev_hash = "0" * 64
        self._audit_lock = threading.Lock()
        self._baseline_job: Any = None
        self._baseline_value: list[str] = []
        self._baseline_audit: dict[str, Any] | None = None
        self.roe: Any = None
        self.workdir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ plumbing

    def _goto(self, dst: AssessmentState, note: str = "") -> None:
        src = self.state
        self.state = transition(src, dst)
        self.state_log.record(src, dst, note)
        self.result.state = self.state
        self.sink.on_transition(src, dst, note)
        self._audit(
            "assessment.transition",
            "assessment",
            self.config.assessment_id,
            actor="system",
            src=src.value,
            dst=dst.value,
            note=note,
        )

    def _audit(
        self, action: str, subject_type: str, subject_id: str, *, actor: str = "system", **details: Any
    ) -> AuditEvent:
        payload = {
            "org_id": self.config.org_id,
            "project_id": self.config.project_id,
            "actor": actor,
            "actor_id": details.pop("actor_id", "system"),
            "action": action,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "at": self.now(),
            "details": details,
        }
        with self._audit_lock:  # Fortify's step audits from its own thread
            body = json.dumps(
                {**payload, "at": payload["at"].isoformat(), "prev_hash": self._audit_prev_hash},
                sort_keys=True,
                default=str,
            )
            digest = hashlib.sha256(body.encode()).hexdigest()
            event = AuditEvent(**payload, prev_hash=self._audit_prev_hash, hash=digest)  # type: ignore[arg-type]
            self._audit_prev_hash = digest
            self.result.audit.append(event)
        return event

    def _budget_left(self) -> bool:
        return (
            self.result.usage.total_tokens < self.config.budget.max_tokens
            and self.result.usage.usd < self.config.budget.max_usd
        )

    def _context(
        self, agent: AgentName, step_key: str, *, services: dict[str, Any], read_root: Path | None = None
    ) -> ToolContext:
        return ToolContext(
            assessment_id=self.config.assessment_id,
            agent=agent,
            run_id=new_id("run"),
            step_key=step_key,
            read_root=read_root or self.intel.snapshot.root,
            services=services,
            scope=self.config.scope,
            org_member_count=self.config.org_member_count,
            audit=self.audit,
            kill_switch=self.kill_switch,
            now=self.now,
            roe=self.roe,
        )

    def _probe_transport_problem(self) -> str | None:
        from ..checks.library import TRANSPORTS

        for role, transport in (
            ("probe", self.config.probe_check_transport),
            ("verify", self.config.verify_check_transport),
        ):
            if transport not in TRANSPORTS:
                return f"unknown {role} check transport {transport!r}"
            if transport == "inprocess":
                continue
            if getattr(self.driver, "name", "") != "docker":
                return f"the http {role} transport requires the Docker sandbox driver"
            if not recipe_from_snapshot(self.intel)(None, self.config.target_id).get("app_factory"):
                return f"the http {role} transport requires an app_factory in the build recipe"
            if self.roe is None:
                return f"the http {role} transport requires issued rules of engagement"
            self._audit(
                f"{role}.transport",
                "assessment",
                self.config.assessment_id,
                transport=transport,
                target="assessed snapshot served in the check image"
                if role == "probe"
                else "patched fresh checkout served in the check image",
                target_id=self.config.target_id,
            )
        return None

    def _issue_roe(self) -> Any:
        from ..standards.roe import RoeError, build_roe

        scope_event = self.result.audit[-1].id if self.result.audit else None
        try:
            roe = build_roe(
                self.config.scope,
                self.library.list_public(),
                audit_event_ids=[scope_event] if scope_event else [],
            )
        except RoeError as error:
            self._audit("roe.refused", "scope", self.config.scope.id, detail=str(error)[:300])
            return None
        self._audit(
            "roe.issued",
            "rules_of_engagement",
            roe.id,
            scope_id=roe.scope_id,
            digest=roe.digest,
            permitted_templates=roe.permitted_check_templates,
            valid_until=roe.valid_until.isoformat(),
        )
        self.result.roe = roe
        return roe

    def _read_services(self) -> dict[str, Any]:
        return {"intel": self.intel, "history": self.history, "checks": self.library}

    def _run_step(
        self, spec: AgentSpec, ctx: ToolContext, prompt: str, *, input_ref: str, attempt: int = 1
    ) -> StepOutcome:
        """Idempotent agent step. A completed step key is never re-executed."""
        key = f"{ctx.step_key}#{attempt}"
        if key in self._completed_steps:
            return self._completed_steps[key]
        if not self._budget_left():
            raise PlatformError(ErrorCode.BUDGET_EXHAUSTED, "assessment budget exhausted")
        runner = self.step_runner or run_agent
        outcome = runner(
            spec,
            gateway=self.gateway,
            registry=self.registry,
            ctx=ctx,
            task_prompt=prompt,
            budget=self.config.budget,
            assessment_usage=self.result.usage,
            input_ref=input_ref,
            cancelled=self.cancelled,
        )
        outcome.run.attempt = attempt
        self.result.runs.append(outcome.run)
        self.result.records.append(outcome.record)
        self._completed_steps[key] = outcome
        self._audit(
            "agent.step",
            "agent_run",
            outcome.run.id,
            actor="agent",
            actor_id=spec.name.value,
            status=outcome.run.status,
            provider_mode=outcome.run.provider_mode,
            tool_calls=outcome.run.tool_calls,
            error_code=outcome.run.error_code,
        )
        return outcome

    def _advise(self, subject_type: str, subject_id: str, advice: Any) -> Any:
        """Audit one specialist inference (persisted with the assessment's audit chain)."""
        if advice is not None:
            self._audit(
                "specialist.inference",
                subject_type,
                subject_id,
                actor="agent",
                actor_id=f"specialist:{advice.agent}",
                **specialist_bridge.audit_fields(advice),
            )
        return advice

    def _fail(self, code: ErrorCode, detail: str) -> AssessmentResult:
        target = (
            AssessmentState.BUDGET_EXHAUSTED
            if code is ErrorCode.BUDGET_EXHAUSTED
            else AssessmentState.CANCELLED
            if code is ErrorCode.CANCELLED
            else AssessmentState.FAILED
        )
        self.result.error_code = code
        self.result.error_detail = detail
        self._goto(target, detail[:200])
        self._audit(
            "assessment.failed", "assessment", self.config.assessment_id, code=code.value, detail=detail[:300]
        )
        return self.result

    # ------------------------------------------------------------------ main

    def run(self) -> AssessmentResult:
        try:
            return self._run()
        except PlatformError as exc:
            return self._fail(exc.code, exc.message)

    def _run(self) -> AssessmentResult:
        cfg = self.config

        # ---- SCOPE_CHECK gate
        self._goto(AssessmentState.SCOPE_CHECK)
        decision = scope_check(
            cfg.scope,
            ActionRequest(action="assessment_start"),
            self.now(),
            org_member_count=cfg.org_member_count,
        )
        self._audit(
            "scope.checked",
            "scope",
            cfg.scope.id,
            allowed=decision.allowed,
            reason_code=decision.reason_code,
        )
        if not decision.allowed:
            return self._fail(ErrorCode.SCOPE_DENIED, f"{decision.reason_code} {decision.detail}".strip())
        self.roe = self._issue_roe()
        transport_problem = self._probe_transport_problem()
        if transport_problem:
            return self._fail(ErrorCode.SANDBOX_START_FAILED, transport_problem)

        # ---- INDEXING (code intelligence is an upstream subsystem)
        self._goto(AssessmentState.INDEXING, self.intel.provenance().get("status", ""))
        if not self.intel.entry_points():
            # No HTTP entry points are recognised (only Python web routes are; a JavaScript
            # app has none). The functional review still runs on the commit; the security
            # agents have no attack surface to test, so they are skipped and labelled as such.
            note = "no HTTP entry points in snapshot index; security agents skipped"
            self._goto(AssessmentState.FUNCTIONAL_REVIEW, "functional review only: " + note)
            self._goto(AssessmentState.RECON, "skipped: " + note)
            return self._report_or_wait(note)

        # ---- FUNCTIONAL_REVIEW is Sai's pillar; we record the baseline it needs.
        self._goto(AssessmentState.FUNCTIONAL_REVIEW, "baseline test run (code_review pillar pending)")
        # Only Verify (and the approval checkpoint) need the baseline: run it beside Scout.
        pool = ThreadPoolExecutor(max_workers=1)
        self._baseline_job = pool.submit(self._baseline_test_failures)
        pool.shutdown(wait=False)

        # ---- RECON (Scout)
        self._goto(AssessmentState.RECON)
        hypotheses = self._run_scout()
        if not hypotheses:
            return self._report_or_wait("no hypotheses")

        # ---- VALIDATION (Probe, one step per hypothesis, capped)
        self._goto(AssessmentState.VALIDATION)
        confirmed = self._run_validation(hypotheses)
        if not confirmed:
            return self._report_or_wait("no confirmed findings")

        # ---- ANALYSIS (Trace). Diagnose's step needs only the validated finding, so it runs
        # alongside; its plan applies only to a finding Trace could analyse.
        self._goto(AssessmentState.ANALYSIS)
        with ThreadPoolExecutor(max_workers=len(confirmed)) as pool:
            plans = {f.finding_id: pool.submit(self._diagnose_step, f) for f in confirmed}
            for finding in confirmed:
                self._run_trace(finding)
            traced = [f for f in confirmed if f.attack_path is not None]
            if not traced:
                return self._report_or_wait("impact analysis failed for all findings")

            # ---- DIAGNOSIS (Diagnose)
            self._goto(AssessmentState.DIAGNOSIS)
            for finding in traced:
                self._apply_diagnose(finding, *plans[finding.finding_id].result())
        diagnosed = [f for f in traced if f.plan is not None]
        if not diagnosed:
            return self._report_or_wait("no remediation plan produced")

        # ---- AWAITING_APPROVAL gate (human only)
        self._goto(AssessmentState.AWAITING_APPROVAL)
        approved: list[FindingRecord] = []
        pending: list[str] = []
        for finding in diagnosed:
            try:
                approval = self.approve_remediation(finding) if self.approve_remediation else None
            except ApprovalPending as waiting:
                pending.append(waiting.finding_id)
                finding.notes.append("awaiting human remediation approval")
                self._audit(
                    "approval.requested",
                    "finding",
                    finding.finding_id,
                    approval_id=waiting.approval_id,
                )
                continue
            if approval is None:
                finding.notes.append("remediation not approved; finding stays diagnosed")
                self._audit("approval.absent", "finding", finding.finding_id)
                continue
            if approval.decision != "approved" or approval.subject_type != "remediation":
                finding.notes.append(f"remediation {approval.decision}")
                self._audit("approval.declined", "finding", finding.finding_id, approval_id=approval.id)
                continue
            if approval.subject_id != (finding.plan.id if finding.plan else ""):
                finding.notes.append("approval does not match this remediation plan; ignored")
                self._audit("approval.mismatch", "finding", finding.finding_id, approval_id=approval.id)
                continue
            if approval.decided_by.startswith("agent") or approval.decided_by == "system":
                finding.notes.append("approval was not made by a human; ignored")
                self._audit("approval.invalid_actor", "finding", finding.finding_id)
                continue
            finding.approval = approval
            approved.append(finding)
            self._audit(
                "approval.granted",
                "finding",
                finding.finding_id,
                actor="user",
                actor_id=approval.decided_by,
                approval_id=approval.id,
            )
        # An undecided external (functional) approval pauses unconditionally, even when
        # some security remediation is already approved: the resume handles both together.
        if (pending and not approved) or self._wait_external():
            # Stop here, without a terminal transition: a host persists the
            # request and re-enters this orchestrator once a human decides.
            self.result.error_code = None
            return self.result
        if not approved:
            self._goto(AssessmentState.REPORTING, "no approved remediation")
            return self._finish()

        self.remediate_and_harden(approved)
        self._goto(AssessmentState.REPORTING)
        return self._finish()

    def remediate_and_harden(
        self, approved: list[FindingRecord], after_remediation: Callable[[], None] | None = None
    ) -> None:
        """REMEDIATION (Patch <-> Verify) then HARDENING (Fortify) for verified findings.

        Fortify's model step reads only the diagnosis, so it runs alongside remediation; its
        records apply only to a finding Verify accepted."""
        self._goto(AssessmentState.REMEDIATION)
        with ThreadPoolExecutor(max_workers=max(1, len(approved))) as pool:
            advice = {f.finding_id: pool.submit(self._fortify_step, f) for f in approved}
            for finding in approved:
                self._run_remediation(finding)
            # Wait for Fortify's step before the functional repair, so the repair never shares
            # the host with another thread (it usually finished during Patch -> Verify).
            steps = {f.finding_id: advice[f.finding_id].result() for f in approved}
        if after_remediation is not None:
            after_remediation()
        self._goto(AssessmentState.HARDENING)
        for finding in approved:
            if finding.status == "verified":
                self._apply_fortify(finding, *steps[finding.finding_id])

    def _wait_external(self) -> bool:
        return bool(self.external_pending and self.external_pending())

    def _report_or_wait(self, note: str) -> AssessmentResult:
        """Security work ended early. If a functional remediation approval is still owed,
        walk the skipped phases (each labelled) to AWAITING_APPROVAL and pause;
        otherwise report and finish. Only DIAGNOSIS→AWAITING_APPROVAL is a legal pause edge.
        """
        if not self._wait_external():
            self._goto(AssessmentState.REPORTING, note)
            return self._finish()
        chain = (
            AssessmentState.RECON,
            AssessmentState.VALIDATION,
            AssessmentState.ANALYSIS,
            AssessmentState.DIAGNOSIS,
            AssessmentState.AWAITING_APPROVAL,
        )
        for state in chain[chain.index(self.state) + 1 :]:
            self._goto(state, f"skipped ({note}); awaiting functional remediation approval")
        self.result.error_code = None
        return self.result

    def _finish(self) -> AssessmentResult:
        self._baseline_failing  # noqa: B018 - waits for the background baseline and counts its sandbox time
        self._goto(AssessmentState.COMPLETED)
        self._audit(
            "assessment.completed",
            "assessment",
            self.config.assessment_id,
            findings=len(self.result.findings),
        )
        return self.result

    # ------------------------------------------------------------------ steps

    def _added_tests_on_unpatched(
        self, candidate: Any, patched: Path | None, attempt: int
    ) -> list[dict[str, Any]]:
        """Run the candidate's added tests on a fresh UNPATCHED checkout (platform-run, no model).

        Only the added test files are copied in from Verify's patched workspace; the code under
        test is the pinned snapshot. Verify requires at least one of them to fail here.
        Returns [] when nothing could be run (Verify then fails closed).
        """
        roots = recipe_test_roots(self.intel.snapshot.root)
        tests = [t for t in candidate.tests_added if isinstance(t, str) and under_test_roots(t, roots)]
        if not patched or not tests:
            return []
        dest = self.workdir / f"unpatched-{candidate.id}-{attempt}"
        try:
            root = fresh_checkout(self.intel.snapshot, dest)
            for rel in tests:
                src = resolve_under(Path(patched), rel)
                if not src.is_file():
                    return []
                target = resolve_under(root, rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(src.read_bytes())
        except (OSError, PlatformError, ValueError):
            return []
        ctx = self._context(
            AgentName.VERIFY, f"remediation:{candidate.finding_id}:unpatched-tests", services={}
        )
        ctx.artifacts["tests_added"] = tests
        runner = self._test_runner(lambda _ctx: root)
        result = runner.run(ctx, "added_tests", [])
        self._audit(
            "tests.added_on_unpatched",
            "patch",
            candidate.id,
            failing=result.get("failing_tests"),
            passed=result.get("passed"),
            sandbox_run_id=result.get("sandbox_run_id"),
        )
        return [result]

    def _added_tests_on_patched(self, candidate: Any, ctx: ToolContext) -> list[dict[str, Any]]:
        """Run added tests deterministically against Verify's successfully patched workspace."""
        facts = ctx.artifacts.get("verify_facts") or {}
        patched = ctx.artifacts.get("verify_workspace")
        roots = recipe_test_roots(self.intel.snapshot.root)
        tests = [
            path for path in candidate.tests_added if isinstance(path, str) and under_test_roots(path, roots)
        ]
        if (
            not facts.get("applied")
            or facts.get("patch_id") != candidate.id
            or ctx.artifacts.get("verify_patch_id") != candidate.id
            or not patched
            or not tests
        ):
            return []
        try:
            for rel in tests:
                if not resolve_under(Path(patched), safe_rel_path(rel)).is_file():
                    return []
        except (OSError, PlatformError, ValueError):
            return []
        ctx.artifacts["tests_added"] = tests
        runner = self._test_runner(lambda _ctx: Path(patched))
        result = runner.run(ctx, "added_tests", [])
        result["workspace_role"] = "patched"
        result["patch_id"] = ctx.artifacts.get("verify_patch_id")
        self._audit(
            "tests.added_on_patched",
            "patch",
            candidate.id,
            failing=result.get("failing_tests"),
            passed=result.get("passed"),
            sandbox_run_id=result.get("sandbox_run_id"),
        )
        return [result]

    def _test_runner(self, workspace_provider: Any, *, suite_workspace_provider: Any = None) -> TestRunner:
        """Tests run with the target recipe's `test_command` and `sandbox` setup when it has them."""
        recipe: dict[str, Any] = {}
        path = self.intel.snapshot.root / "build_recipe.json"
        if path.is_file():
            with contextlib.suppress(ValueError, OSError):
                recipe = json.loads(path.read_text(encoding="utf-8"))
        command = recipe.get("test_command")
        valid = isinstance(command, list) and all(isinstance(a, str) for a in command) and command
        setup: dict[str, tuple[str, ...]] = {}
        with contextlib.suppress(PlatformError):  # Probe reports an invalid block; tests run bare
            setup = sandbox_setup(recipe)
        return TestRunner(
            driver=self.driver,
            workspace_provider=workspace_provider,
            suite_workspace_provider=suite_workspace_provider,
            test_command_args=tuple(command) if valid else self.config.test_command_args,
            setup=setup,
        )

    @property
    def _baseline_failing(self) -> list[str]:
        """Tests already failing on the unpatched snapshot; waits for the background run."""
        if self._baseline_job is not None:
            failing, seconds, audit = self._baseline_job.result()
            self._baseline_job = None
            self._baseline_value = failing
            self._baseline_audit = audit
            self.result.usage.sandbox_seconds += seconds
        return self._baseline_value

    @_baseline_failing.setter
    def _baseline_failing(self, value: list[str]) -> None:
        self._baseline_job = None
        self._baseline_value = value
        self._baseline_audit = None

    def _baseline_test_failures(self) -> tuple[list[str], float, dict[str, Any] | None]:
        """Record which tests already fail on the unpatched snapshot (Verify needs this)."""
        ctx = self._context(AgentName.VERIFY, "baseline", services={})
        runner = self._test_runner(lambda _ctx: self.intel.snapshot.root)
        result = runner.run(ctx, "project_suite", [])
        self._audit(
            "tests.baseline",
            "snapshot",
            self.intel.snapshot.snapshot_id,
            failing=result.get("failing_tests"),
            passed=result.get("passed"),
            sandbox_run_id=result.get("sandbox_run_id"),
        )
        audit = (ctx.artifacts.get("pytest_audits") or {}).get(str(result.get("sandbox_run_id") or ""))
        return (
            list(result.get("failing_tests") or []),
            float(result.get("duration_s") or 0),
            audit if isinstance(audit, dict) else None,
        )

    def _run_scout(self) -> list[Hypothesis]:
        data = scout_agent.ScoutInput(
            assessment_id=self.config.assessment_id,
            snapshot_id=self.intel.snapshot.snapshot_id,
            commit_sha=self.intel.snapshot.commit_sha,
            expectation_ids=[e.id for e in self.intel.expectations()],
            refuted_keys=self.history.refuted_keys(),
            changed_paths=list(self.config.change_paths),
            change_diff=self.config.change_diff,
            max_hypotheses=self.config.max_hypotheses,
            available_check_categories=sorted(
                c
                for c in self.library.categories()
                if check_category_allowed(c, self.config.scope.allowed_check_categories)
            ),
        )
        ctx = self._context(AgentName.SCOUT, "recon", services=self._read_services())
        survey_block = ""
        if self.specialists is not None:
            rows, advices = specialist_bridge.scout_survey(self.specialists, self.intel)
            model_id = next((a.model_id for a in advices if a.source == "model"), None)
            self._audit(
                "specialist.inference",
                "snapshot",
                self.intel.snapshot.snapshot_id,
                actor="agent",
                actor_id="specialist:scout",
                agent="scout",
                model=model_id,
                source="model" if rows else (advices[0].source if advices else "unavailable"),
                spans_scored=len(advices),
                top=json.dumps(rows[:5]),
                reason=None if rows else (advices[0].reason if advices else "no entry points"),
            )
            survey_block = specialist_bridge.scout_block(rows, model_id)
        outcome = self._run_step(
            scout_agent.SPEC,
            ctx,
            scout_agent.build_prompt(data, self.intel) + survey_block,
            input_ref="scout:input",
        )
        if not outcome.ok or not isinstance(outcome.output, scout_agent.ScoutOutput):
            self._audit("recon.failed", "assessment", self.config.assessment_id, error=outcome.run.error_code)
            # A Scout that could not run is a failed assessment, not "no hypotheses":
            # reporting COMPLETED here would present an unexecuted review as clean.
            code = outcome.run.error_code or ErrorCode.LLM_UNAVAILABLE
            raise PlatformError(code, f"recon failed: {outcome.run.error_detail or code.value}"[:300])
        hypotheses = scout_agent.to_hypotheses(
            outcome.output, data, self.intel, max_items=self.config.max_hypotheses
        )
        ranked = scout_agent.rank(hypotheses, self.intel)
        if self.specialists is not None and len(ranked) > 1:
            suspicion: dict[str, float] = {}
            for h in ranked:
                a = specialist_bridge.scout_suspicion(self.specialists, self.intel, h)
                if a is not None and a.source == "model":
                    suspicion[h.id] = 1.0 - (a.score("safe") or 0.0)
            reranked = specialist_bridge.blended_rank(hypotheses, ranked, suspicion)
            if [h.id for h in reranked] != [h.id for h in ranked]:
                self._audit(
                    "specialist.rerank",
                    "assessment",
                    self.config.assessment_id,
                    actor="agent",
                    actor_id="specialist:scout",
                    before=[h.id for h in ranked],
                    after=[h.id for h in reranked],
                )
            ranked = reranked
        for signal in outcome.output.suspicious_instructions:
            self._audit(
                "signal.suspicious_instruction",
                "snapshot",
                self.intel.snapshot.snapshot_id,
                detail=signal[:300],
            )
        for h in ranked:
            record = FindingRecord(finding_id=h.finding_id, hypothesis=h)
            self.result.findings.append(record)
            self.sink.on_finding(record)
            self._audit(
                "finding.hypothesis",
                "finding",
                h.finding_id,
                weakness_class=h.weakness_class,
                confidence=h.confidence.value,
            )
        return ranked

    def _finding_for(self, hypothesis_id: str) -> FindingRecord:
        return next(f for f in self.result.findings if f.hypothesis.id == hypothesis_id)

    def _run_validation(self, hypotheses: list[Hypothesis]) -> list[FindingRecord]:
        confirmed: list[FindingRecord] = []
        for index, hypothesis in enumerate(hypotheses[: self.config.max_hypotheses_to_validate]):
            finding = self._finding_for(hypothesis.id)
            data = probe_agent.input_from_hypothesis(
                hypothesis,
                assessment_id=self.config.assessment_id,
                scope_id=self.config.scope.id,
                target_id=self.config.target_id,
                intel=self.intel,
            )
            services = {
                **self._read_services(),
                "check_runner": CheckRunner(
                    driver=self.driver,
                    library=self.library,
                    workspace_provider=lambda _ctx: self.intel.snapshot.root,
                    recipe_provider=recipe_from_snapshot(self.intel),
                    transport=self.config.probe_check_transport,
                ),
            }
            ctx = self._context(AgentName.PROBE, f"validation:{index}", services=services)
            try:
                advice = (
                    self._advise(
                        "finding",
                        finding.finding_id,
                        specialist_bridge.probe_advice(self.specialists, hypothesis),
                    )
                    if self.specialists is not None
                    else None
                )
                outcome = self._run_step(
                    probe_agent.SPEC,
                    ctx,
                    probe_agent.build_prompt(data, self.intel)
                    + specialist_bridge.block(
                        advice,
                        "Ranked OWASP WSTG validation strategies from a trained model. Choose a reviewed check "
                        "template yourself; the executed template's result decides the outcome, not this ranking.",
                    ),
                    input_ref=f"probe:{hypothesis.id}",
                )
            except PlatformError as exc:
                if exc.code is ErrorCode.BUDGET_EXHAUSTED:
                    raise
                finding.status = "inconclusive"
                finding.notes.append(f"probe step failed: {exc.code.value}")
                continue
            check_runs = list(ctx.artifacts.get("check_runs") or [])
            for run in check_runs:
                self.result.usage.sandbox_seconds += float(run.get("duration_s") or 0)
            out = outcome.output if isinstance(outcome.output, probe_agent.ProbeOutput) else None
            if out is None:
                finding.status = "inconclusive"
                finding.notes.append(f"probe output invalid: {outcome.run.error_code}")
                continue
            validation = probe_agent.to_validation_result(out, data, outcome.record, check_runs)
            finding.validation = validation
            self.sink.on_record(finding, validation, "validation_result")
            finding.status = {
                ValidationOutcome.CONFIRMED: "validated",
                ValidationOutcome.REFUTED: "refuted",
                ValidationOutcome.INCONCLUSIVE: "inconclusive",
            }[validation.outcome]
            self._audit(
                "finding.validation",
                "finding",
                finding.finding_id,
                outcome=validation.outcome.value,
                sandbox_run_id=validation.sandbox_run_id,
                template=validation.check_template_id,
            )
            self.history.records.append(
                {
                    "key": hypothesis_key(
                        hypothesis.weakness_class,
                        hypothesis.cited_spans[0].path or "",
                        hypothesis.entry_point_id,
                    ),
                    "outcome": validation.outcome.value,
                    "finding_id": finding.finding_id,
                    "title": hypothesis.title,
                }
            )
            self._classify(finding)
            if validation.outcome is ValidationOutcome.CONFIRMED:
                confirmed.append(finding)
        return confirmed

    def _run_trace(self, finding: FindingRecord) -> None:
        assert finding.validation is not None
        paths = [s.path for s in finding.hypothesis.cited_spans if s.path]
        symbol = self._handler_symbol(finding)
        data = trace_agent.TraceInput(
            check_category=finding.hypothesis.suggested_check_category,
            assessment_id=self.config.assessment_id,
            finding_id=finding.finding_id,
            validation_result_id=finding.validation.id,
            sandbox_run_id=finding.validation.sandbox_run_id,
            hypothesis_title=finding.hypothesis.title,
            weakness_class=finding.hypothesis.weakness_class,
            confirmed_outcome=finding.validation.outcome.value,
            validation_observations=dict(finding.validation.observations),
            affected_paths=paths,
            affected_symbol=symbol,
            expectation_ids=list(finding.hypothesis.expectation_ids),
        )
        ctx = self._context(AgentName.TRACE, f"analysis:{finding.finding_id}", services=self._read_services())
        outcome = self._run_step(
            trace_agent.SPEC,
            ctx,
            trace_agent.build_prompt(data, self.intel)
            + specialist_bridge.block(
                self._advise(
                    "finding",
                    finding.finding_id,
                    specialist_bridge.trace_advice(self.specialists, finding.hypothesis),
                )
                if self.specialists is not None
                else None,
                "MITRE ATT&CK tactics a trained model associates with this weakness (via CAPEC). Use them to decide "
                "which paths to follow. Severity is computed by deterministic rules from verified steps, not from this.",
            ),
            input_ref=f"trace:{finding.finding_id}",
        )
        if not isinstance(outcome.output, trace_agent.TraceOutput):
            finding.notes.append(f"trace failed: {outcome.run.error_code}")
            return
        path, severity = trace_agent.to_attack_path(
            outcome.output, data, self.intel, validation=finding.validation
        )
        finding.attack_path = path
        finding.severity = severity
        self.sink.on_record(finding, path, "attack_path")
        self._audit(
            "finding.severity",
            "finding",
            finding.finding_id,
            level=severity.level.value,
            factors=severity.factors.model_dump(mode="json"),
            verified_steps=sum(1 for s in path.steps if s.verified),
        )
        self._classify(finding)

    def _diagnose_step(self, finding: FindingRecord) -> tuple[diagnose_agent.DiagnoseInput, StepOutcome]:
        # Starts before Trace finishes: severity and impact are filled in when Trace already has them.
        data = diagnose_agent.DiagnoseInput(
            assessment_id=self.config.assessment_id,
            finding_id=finding.finding_id,
            weakness_class=finding.hypothesis.weakness_class,
            hypothesis_title=finding.hypothesis.title,
            severity_level=finding.severity.level.value if finding.severity else "pending impact analysis",
            impact_summary=(
                finding.attack_path.impact_summary if finding.attack_path else finding.hypothesis.title
            ),
            validation_observations=dict(finding.validation.observations) if finding.validation else {},
            affected_symbol=self._handler_symbol(finding),
            affected_paths=[s.path for s in finding.hypothesis.cited_spans if s.path],
            violated_expectation_ids=list(finding.hypothesis.expectation_ids),
        )
        ctx = self._context(
            AgentName.DIAGNOSE, f"diagnosis:{finding.finding_id}", services=self._read_services()
        )
        outcome = self._run_step(
            diagnose_agent.SPEC,
            ctx,
            diagnose_agent.build_prompt(data, self.intel)
            + specialist_bridge.block(
                self._advise(
                    "finding",
                    finding.finding_id,
                    specialist_bridge.diagnose_advice(self.specialists, self.intel, finding),
                )
                if self.specialists is not None
                else None,
                "Likely root-cause class from a trained model or its baseline (see source). Confirm it against the "
                "code and the validation evidence; cite the cause span yourself.",
            ),
            input_ref=f"diagnose:{finding.finding_id}",
        )
        return data, outcome

    def _apply_diagnose(
        self, finding: FindingRecord, data: diagnose_agent.DiagnoseInput, outcome: StepOutcome
    ) -> None:
        if not isinstance(outcome.output, diagnose_agent.DiagnoseOutput):
            finding.notes.append(f"diagnose failed: {outcome.run.error_code}")
            return
        try:
            root_cause, plan, related = diagnose_agent.to_plan(outcome.output, data, self.intel)
        except ValueError as exc:
            finding.notes.append(f"diagnose output unusable: {exc}")
            return
        finding.root_cause = root_cause
        finding.plan = plan
        finding.related_sites = related
        finding.status = "diagnosed"
        self.sink.on_record(finding, root_cause, "root_cause")
        self.sink.on_record(finding, plan, "remediation_plan")
        self.sink.on_status(finding, finding.status)
        self._audit(
            "finding.diagnosed",
            "finding",
            finding.finding_id,
            cause_class=root_cause.cause_class.value,
            plan_id=plan.id,
            files_expected=plan.files_expected,
        )

    def _run_remediation(self, finding: FindingRecord) -> None:
        assert finding.plan is not None and finding.approval is not None
        sub_state = RemediationState.PATCHING
        attempt = 1
        reasons: list[str] = []
        while True:
            if sub_state is RemediationState.PATCHING:
                candidate, worktree = self._run_patch(finding, attempt, reasons)
                patch_failed = candidate is None
                sub_state = next_remediation_state(
                    sub_state, verdict=None, attempt=attempt, patch_failed=patch_failed
                )
                if sub_state is RemediationState.UNRESOLVED:
                    finding.status = "unresolved"
                    self._audit("remediation.unresolved", "finding", finding.finding_id, attempt=attempt)
                    return
                finding.status = "patch_proposed"
                continue
            if sub_state is RemediationState.VERIFYING:
                verification = self._run_verify(finding, attempt, worktree)
                verdict = verification.verdict if verification else "rejected"
                if verification:
                    finding.verifications.append(verification)
                    self.sink.on_verification(finding, verification)
                    reasons = list(verification.reasons)
                    self._audit(
                        "finding.verification",
                        "finding",
                        finding.finding_id,
                        verdict=verdict,
                        checks={c.name.value: c.passed for c in verification.checks},
                        verify_run_id=verification.verify_run_id,
                    )
                else:
                    reasons = ["verification step failed to execute"]
                sub_state = next_remediation_state(sub_state, verdict=verdict, attempt=attempt)
                if sub_state is RemediationState.ACCEPTED:
                    finding.status = "verified"
                    self.sink.on_status(finding, finding.status)
                    return
                if sub_state is RemediationState.UNRESOLVED:
                    finding.status = "patch_rejected"
                    self._audit("remediation.exhausted", "finding", finding.finding_id, attempts=attempt)
                    return
                attempt += 1
                continue
            raise PlatformError(ErrorCode.CONFLICT_STATE, f"bad remediation state {sub_state}")

    def _run_patch(
        self, finding: FindingRecord, attempt: int, reasons: list[str]
    ) -> tuple[PatchCandidate | None, PatchWorktree | None]:
        assert finding.plan is not None and finding.approval is not None
        worktree = PatchWorktree.create(
            self.intel,
            self.workdir / f"worktree-{finding.finding_id}-{attempt}",
            allowed_paths=list(finding.plan.files_expected),
            test_roots=recipe_test_roots(self.intel.snapshot.root),
        )
        data = patch_agent.input_from_plan(
            finding.plan,
            assessment_id=self.config.assessment_id,
            approval_id=finding.approval.id,
            attempt=attempt,
            cause_paths=[
                s.path for s in (finding.root_cause.cause_spans if finding.root_cause else []) if s.path
            ],
            cause_spans=[
                (s.path, s.span.start_line, s.span.end_line)
                for s in (finding.root_cause.cause_spans if finding.root_cause else [])
                if s.path and s.span
            ],
            prior_reasons=reasons,
        )
        services = {
            **self._read_services(),
            "worktree": worktree,
            "test_runner": self._test_runner(lambda _ctx, wt=worktree: wt),
        }
        ctx = self._context(
            AgentName.PATCH,
            f"remediation:{finding.finding_id}:patch",
            services=services,
            read_root=worktree.root,
        )
        outcome = self._run_step(
            patch_agent.SPEC,
            ctx,
            patch_agent.build_prompt(data, self.intel)
            + specialist_bridge.block(
                self._advise(
                    "finding",
                    finding.finding_id,
                    specialist_bridge.patch_advice(self.specialists, self.intel, finding),
                )
                if attempt == 1 and self.specialists is not None
                else None,
                "Suggested minimal remediation strategy (trained model or its baseline; see source). Keep the change "
                "inside the approved files and preserve existing tests. Verify decides acceptance from executed checks.",
            ),
            input_ref=f"patch:{finding.plan.id}",
            attempt=attempt,
        )
        for run in ctx.artifacts.get("test_runs") or []:
            self.result.usage.sandbox_seconds += float(run.get("duration_s") or 0)
        if not isinstance(outcome.output, patch_agent.PatchOutput):
            finding.notes.append(f"patch attempt {attempt} failed: {outcome.run.error_code}")
            return None, worktree
        diff_uri = f"artifact://patch/{finding.finding_id}/{attempt}.diff"
        candidate, diff_text, notes = patch_agent.to_candidate(
            outcome.output, data, worktree, diff_uri=diff_uri
        )
        finding.notes.extend(notes)
        if candidate is None:
            return None, worktree
        (self.workdir / f"patch-{finding.finding_id}-{attempt}.diff").write_text(diff_text, encoding="utf-8")
        self._patch_artifacts[candidate.id] = {
            "diff": diff_text,
            "tests_added": list(candidate.tests_added),
            "plan_files": list(finding.plan.files_expected),
            "protected_tests": self._protected_tests(),
        }
        finding.candidates.append(candidate)
        self.sink.on_patch(finding, candidate, diff_text)
        self._audit(
            "patch.candidate",
            "patch",
            candidate.id,
            actor="agent",
            actor_id="patch",
            files_changed=candidate.files_changed,
            tests_added=candidate.tests_added,
            diff_sha256=candidate.diff_sha256,
            attempt=attempt,
        )
        return candidate, worktree

    def _run_verify(
        self, finding: FindingRecord, attempt: int, worktree: PatchWorktree | None = None
    ) -> VerificationResult | None:
        candidate = finding.candidates[-1]
        assert finding.plan is not None
        data = verify_agent.input_from_candidate(
            candidate,
            finding.plan,
            finding.validation,
            assessment_id=self.config.assessment_id,
            scope_id=self.config.scope.id,
            target_id=self.config.target_id,
            baseline_failing=self._baseline_failing,
        )
        applier = PatchApplier(intel=self.intel, artifacts=self._patch_artifacts, workdir=self.workdir)

        def verify_workspace(ctx: ToolContext) -> Any:
            # Verify runs tests/checks against the patched fresh checkout once it
            # has applied the diff; before that, against the pristine snapshot.
            return ctx.artifacts.get("verify_workspace") or self.intel.snapshot.root

        def verify_suite_workspace(ctx: ToolContext) -> Any:
            return ctx.artifacts.get("verify_suite_workspace")

        services = {
            **self._read_services(),
            "patch_applier": applier,
            "test_runner": self._test_runner(
                verify_workspace, suite_workspace_provider=verify_suite_workspace
            ),
            "check_runner": CheckRunner(
                driver=self.driver,
                library=self.library,
                workspace_provider=verify_workspace,
                recipe_provider=recipe_from_snapshot(self.intel),
                transport=self.config.verify_check_transport,
            ),
        }
        ctx = self._context(AgentName.VERIFY, f"remediation:{finding.finding_id}:verify", services=services)
        ctx.artifacts["tests_added"] = list(candidate.tests_added)
        # The added tests on unpatched code need no model: run them while Verify works. The
        # test files come from Patch's worktree, the same bytes the diff carries.
        with ThreadPoolExecutor(max_workers=1) as pool:
            unpatched = pool.submit(
                self._added_tests_on_unpatched, candidate, worktree.root if worktree else None, attempt
            )
            outcome = self._run_step(
                verify_agent.SPEC,
                ctx,
                verify_agent.build_prompt(data, self.intel),
                input_ref=f"verify:{candidate.id}",
                attempt=attempt,
            )
            unpatched_added_runs = unpatched.result()
        check_runs = list(ctx.artifacts.get("check_runs") or [])
        test_runs = list(ctx.artifacts.get("test_runs") or [])
        test_runs.extend(self._added_tests_on_patched(candidate, ctx))
        for run in check_runs + test_runs:
            self.result.usage.sandbox_seconds += float(run.get("duration_s") or 0)
        for run in unpatched_added_runs:
            self.result.usage.sandbox_seconds += float(run.get("duration_s") or 0)
        evidence = verify_agent.VerifyEvidence(
            apply_facts=dict(ctx.artifacts.get("verify_facts") or {}),
            recheck_runs=check_runs,
            test_runs=test_runs,
            baseline_failing=self._baseline_failing,
            tests_added=list(candidate.tests_added),
            protected_tests=self._protected_tests(),
            original_check_ref=data.original_check_template_ref,
            original_check_params=dict(data.original_check_parameters),
            candidate_id=candidate.id,
            baseline_pytest_audit=self._baseline_audit,
            suite_pytest_audits={
                str(run_id): audit
                for run_id, audit in (ctx.artifacts.get("pytest_audits") or {}).items()
                if isinstance(run_id, str) and isinstance(audit, dict)
            },
            required_transport=self.config.verify_check_transport,
            original_tree_sha256=tree_digest(self.intel.snapshot.root),
            patched_tree_sha256=tree_digest(ctx.artifacts["verify_workspace"])
            if ctx.artifacts.get("verify_workspace")
            else None,
            unpatched_added_runs=unpatched_added_runs,
        )
        out = outcome.output if isinstance(outcome.output, verify_agent.VerifyOutput) else None
        sandbox_ids = [
            str(r.get("sandbox_run_id")) for r in check_runs + test_runs if r.get("sandbox_run_id")
        ]
        result = verify_agent.to_result(
            out, data, evidence, sandbox_run_ids=sandbox_ids, verify_run_id=outcome.run.id
        )
        if self.specialists is not None:
            diff = str(self._patch_artifacts.get(candidate.id, {}).get("diff") or "")
            advice = self._advise(
                "patch",
                candidate.id,
                specialist_bridge.verify_advice(self.specialists, diff, test_runs, unpatched_added_runs),
            )
            # Advisory only: the verdict above came from executed checks and stays as computed.
            if (
                advice is not None
                and advice.source == "model"
                and result.verdict == "accepted"
                and advice.top != "adequate"
            ):
                finding.notes.append(
                    f"verify specialist ({advice.model_id}) rates the evidence '{advice.top}' "
                    f"({advice.predictions[0]['score']:.2f}); verdict unchanged, human review recommended"[
                        :300
                    ]
                )
        from ..regressions import RegressionEvidenceUnavailable, verified_bundle

        finding.verify_http_evidence = verify_agent.http_recheck_evidence(evidence, result.verdict)
        finding.verified_regression_bundle = None
        if result.verdict == "accepted" and outcome.ok:
            try:
                initial = ctx.artifacts.get("regression_sources_before_tests")
                root = ctx.artifacts.get("verify_workspace")
                if initial is None or root is None:
                    raise RegressionEvidenceUnavailable("no captured Verify regression sources")
                finding.verified_regression_bundle = verified_bundle(candidate, result, root, initial)
            except (RegressionEvidenceUnavailable, PlatformError) as exc:
                finding.notes.append(f"regression artifact unavailable: {exc}")
        return result

    def _fortify_step(self, finding: FindingRecord) -> tuple[fortify_agent.FortifyInput, StepOutcome]:
        data = fortify_agent.FortifyInput(
            check_category=finding.hypothesis.suggested_check_category,
            assessment_id=self.config.assessment_id,
            finding_id=finding.finding_id,
            verification_result_id="pending",  # set from Verify in _apply_fortify
            root_cause_summary=finding.root_cause.summary if finding.root_cause else finding.hypothesis.title,
            cause_class=finding.root_cause.cause_class.value if finding.root_cause else "unknown",
            cause_paths=[
                s.path for s in (finding.root_cause.cause_spans if finding.root_cause else []) if s.path
            ],
            related_sites=[r.symbol or "" for r in finding.related_sites][:10],
            affected_symbol=self._handler_symbol(finding),
            weakness_class=finding.hypothesis.weakness_class,
        )
        ctx = self._context(
            AgentName.FORTIFY, f"hardening:{finding.finding_id}", services=self._read_services()
        )
        outcome = self._run_step(
            fortify_agent.SPEC,
            ctx,
            fortify_agent.build_prompt(data, self.intel)
            + specialist_bridge.block(
                self._advise(
                    "finding", finding.finding_id, specialist_bridge.fortify_advice(self.specialists, finding)
                )
                if self.specialists is not None
                else None,
                "MITRE D3FEND countermeasures a trained model ranks for the ATT&CK technique this finding maps to. "
                "Recommend only what fits this codebase; mappings stay proposed until a human accepts them.",
            ),
            input_ref=f"fortify:{finding.finding_id}",
        )
        return data, outcome

    def _apply_fortify(
        self, finding: FindingRecord, data: fortify_agent.FortifyInput, outcome: StepOutcome
    ) -> None:
        candidate = finding.candidates[-1] if finding.candidates else None
        verification = finding.verifications[-1] if finding.verifications else None
        if candidate is None or verification is None:
            return
        data = data.model_copy(
            update={"verification_result_id": verification.id, "tests_added": list(candidate.tests_added)}
        )
        if not isinstance(outcome.output, fortify_agent.FortifyOutput):
            finding.notes.append(f"fortify failed: {outcome.run.error_code}")
            return
        tests, recommendations, drafts, hints = fortify_agent.to_records(outcome.output, data, self.intel)
        finding.regression_tests = tests
        finding.recommendations = recommendations
        finding.future_hypotheses = drafts
        finding.monitoring_hints = hints
        finding.status = "hardened"
        for recommendation in recommendations:
            self.sink.on_record(finding, recommendation, "hardening_recommendation")
        self._classify(finding)
        self.sink.on_status(finding, finding.status)
        self._audit(
            "finding.hardened",
            "finding",
            finding.finding_id,
            regression_tests=[t.test_path for t in tests],
            recommendations=len(recommendations),
            future_hypotheses=len(drafts),
        )

    # ------------------------------------------------------------------ helpers

    def _classify(self, finding: FindingRecord) -> None:
        try:
            finding.standards = standards_bridge.finding_standards(finding, now=self.now())
        except ValueError as exc:
            finding.notes.append(f"standards classification failed: {exc}"[:500])
            return
        self.sink.on_standards(finding)
        cvss = finding.standards.cvss
        self._audit(
            "finding.classified",
            "finding",
            finding.finding_id,
            cwe=finding.standards.cwe.id if finding.standards.cwe else None,
            attack=[m.id for m in finding.standards.attack],
            d3fend=[m.id for m in finding.standards.d3fend],
            cvss_vector=cvss.vector if cvss else None,
            cvss_complete=bool(cvss and cvss.complete),
        )

    def _handler_symbol(self, finding: FindingRecord) -> str | None:
        if finding.hypothesis.entry_point_id:
            for e in self.intel.entry_points():
                if e.id == finding.hypothesis.entry_point_id:
                    return e.handler_symbol
        path = finding.hypothesis.cited_spans[0].path
        line = (
            finding.hypothesis.cited_spans[0].span.start_line if finding.hypothesis.cited_spans[0].span else 1
        )
        for s in self.intel.symbols():
            if s.path == path and s.start_line <= line <= s.end_line and s.kind != "class":
                return s.qualified_name
        return None

    def _protected_tests(self) -> list[str]:
        """Configured, recipe-declared, pre-existing and in-root symlinked tests, plus confirmed sources."""
        protected = set(self.config.protected_tests)
        root = self.intel.snapshot.root
        test_roots = recipe_test_roots(root)
        protected.update(recipe_protected_tests(root))
        protected.update(path for path in self.intel.snapshot.files() if under_test_roots(path, test_roots))
        try:
            protected.update(test_root_files(root, test_roots))
        except PlatformError as exc:
            protected.add(f"<{exc.message}> (protected-test discovery failed: {exc.code.value})")
        for expectation in self.intel.expectations():
            if expectation.status != "confirmed":
                continue
            for source in expectation.sources:
                if source.path and under_test_roots(source.path, test_roots):
                    protected.add(source.path)
        return sorted(protected)
