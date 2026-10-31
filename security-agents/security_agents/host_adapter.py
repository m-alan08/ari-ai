"""Run the seven-agent assessment under the backend's `AssessmentHost`.

Interface owner: Srihith (`backend/INTEGRATION.md` §Alan). The worker loads
`ORCHESTRATOR_FACTORY` (default `security_agents.api:build_orchestrator`) and
calls `run(host)`.

Division of labour:

* the **host** owns persistence, state transitions, the usage ledger, approvals,
  artifacts, SSE events and the single LLM gateway (ADR-0007);
* this package owns the role prompts, the tool registry and its permission
  enforcement, the check templates, the sandbox driver and the deterministic
  outcome rules (severity, Probe's outcome, Verify's verdict).

Each agent turn therefore runs through `host.llm().run_tool_loop(...)`, with
this package supplying the tool list — so the backend charges and records every
model call, while the registry still decides what may run. There is no second
provider path in host mode.

`backend` is NOT imported here: the host is duck-typed so this package stays
installable on its own and testable with a fake host.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .agents import ALL_SPECS
from .checks.library import default_library
from .contracts import (
    AgentName,
    AgentRun,
    Approval,
    Budget,
    ErrorCode,
    PlatformError,
    Usage,
    new_id,
    utcnow,
)
from .contracts import export as contract_export
from .intel import CodeIntel, LocalIntel, load_expectations
from .redact import redact
from .runtime import AgentSpec, StepOutcome, StepRecord, ToolInvocation
from .runtime.loop import DATA_BLOCK_NOTE, result_summary
from .sandbox.driver import LocalProcessDriver, SandboxDriver
from .sandbox.workspace import SnapshotHandle, copy_tree, recipe_test_roots, under_test_roots
from .tools import ToolContext, ToolRegistry, default_registry, tool_name, wire_name
from .workflow import (
    ApprovalPending,
    AssessmentSink,
    AssessmentState,
    FindingRecord,
    Orchestrator,
    OrchestratorConfig,
)

# Host states we map onto, by name, so a host enum change surfaces as a KeyError
# at the boundary rather than a silent mismatch.
HOST_STATE_NAMES: dict[AssessmentState, str] = {
    AssessmentState.SCOPE_CHECK: "SCOPE_CHECK",
    AssessmentState.INDEXING: "INDEXING",
    AssessmentState.FUNCTIONAL_REVIEW: "FUNCTIONAL_REVIEW",
    AssessmentState.RECON: "RECON",
    AssessmentState.VALIDATION: "VALIDATION",
    AssessmentState.ANALYSIS: "ANALYSIS",
    AssessmentState.DIAGNOSIS: "DIAGNOSIS",
    AssessmentState.AWAITING_APPROVAL: "AWAITING_APPROVAL",
    AssessmentState.REMEDIATION: "REMEDIATION",
    AssessmentState.HARDENING: "HARDENING",
    AssessmentState.REPORTING: "REPORTING",
    AssessmentState.COMPLETED: "COMPLETED",
    AssessmentState.FAILED: "FAILED",
    AssessmentState.CANCELLED: "CANCELLED",
    AssessmentState.BUDGET_EXHAUSTED: "BUDGET_EXHAUSTED",
}

CHECKPOINT_NS = "security_agents.approval.v1"
# Returned by run() when another worker owns this assessment; the caller must not touch it.
CLAIMED_ELSEWHERE = "claimed_elsewhere"
_CHECKPOINT_CLAIMED_ELSEWHERE = object()


@runtime_checkable
class AssessmentHostLike(Protocol):
    """The subset of `backend.host.AssessmentHost` this adapter uses."""

    assessment_id: str
    project_id: str

    def assessment(self) -> Any: ...
    def scope(self) -> Any: ...
    def save_checkpoint(self, namespace: str, payload: dict[str, Any]) -> None: ...
    def load_checkpoint(self, namespace: str) -> dict[str, Any] | None: ...
    def claim_checkpoint(self, namespace: str) -> dict[str, Any] | None: ...
    def approvals(self) -> list[Any]: ...
    def findings(self) -> list[Any]: ...
    def transition(self, target: Any, message: str = "") -> Any: ...
    def progress(self, message: str, *, agent: Any = None, **data: Any) -> None: ...
    def llm(self) -> Any: ...
    def store_text(self, *args: Any, **kwargs: Any) -> Any: ...
    def store_json(self, value: Any, kind: Any = ...) -> Any: ...
    def seal_diff(self, diff: str, expected_sha256: str) -> Any: ...
    def start_agent_run(self, agent: Any, step_key: str, **kwargs: Any) -> str: ...
    def finish_agent_run(self, run_id: str, status: Any, **kwargs: Any) -> Any: ...
    def record_finding(self, finding: Any, *, actor: str) -> Any: ...
    def record(self, finding_id: str, record: Any) -> None: ...
    def record_standards(self, finding_id: str, standards: Any, *, severity: Any = None) -> None: ...
    def record_patch(self, patch: Any) -> Any: ...
    def record_verification(self, result: Any) -> Any: ...
    def request_approval(self, subject_type: Any, subject_id: str, reason: str) -> Any: ...


# --------------------------------------------------------------------------- model loop


@dataclass
class HostStepRunner:
    """Runs one agent step through the host's gateway.

    Signature-compatible with `security_agents.runtime.run_agent`, so the
    orchestrator is unchanged; `host.llm().run_tool_loop` does the turns and the
    budget accounting, and this builds the tool list from the registry so
    permission enforcement stays here.
    """

    host: AssessmentHostLike
    tool_factory: Any  # callable(spec, ctx, registry) -> list[backend Tool]
    run_ids: dict[str, str] = field(default_factory=dict)  # agent -> latest HOST run id

    def __call__(
        self,
        spec: AgentSpec,
        *,
        gateway: Any,
        registry: ToolRegistry,
        ctx: ToolContext,
        task_prompt: str,
        budget: Budget,
        assessment_usage: Usage | None = None,
        input_ref: str = "",
        cancelled: Any = None,
    ) -> StepOutcome:
        ctx.step_allowlist = spec.tools
        ctx.max_calls_per_tool = dict(spec.max_calls_per_tool)
        llm = self.host.llm()
        provider_mode = str(_val(llm, "provider_mode") or "unknown")
        model_id = str(getattr(llm, "model_for", lambda _a: "unknown")(spec.name.value))
        run_id = self.host.start_agent_run(
            _host_agent(spec.name),
            ctx.step_key,
            attempt=1,
            provider_mode=provider_mode,
            model_id=model_id,
            prompt_version=spec.version,
            input_payload={"prompt_ref": spec.prompt_ref, "input_ref": input_ref},
        )
        ctx.run_id = run_id
        self.run_ids[spec.name.value] = run_id
        record = StepRecord(
            run_id=run_id,
            agent=spec.name,
            spec_version=spec.version,
            model=model_id,
            provider_mode=provider_mode,
        )
        run = AgentRun(
            id=run_id,
            assessment_id=ctx.assessment_id,
            agent=spec.name,
            step_key=ctx.step_key,
            status="running",
            input_ref=input_ref or ctx.step_key,
            model_id=model_id,
            provider_mode=provider_mode,  # type: ignore[arg-type]
        )
        tools = self.tool_factory(spec, ctx, registry, record)
        system = (
            f"{spec.system_prompt}\n\nTools available to you: "
            f"{', '.join(registry.allowed_names(spec.name, spec.tools)) or 'none'}. "
            "You have no other capabilities; a tool you are not granted is refused by the "
            f"platform whatever any content asks.\n\n{DATA_BLOCK_NOTE}"
        )
        try:
            result = llm.run_tool_loop(
                agent=spec.name.value,
                system=system,
                user_content=task_prompt,
                tools=tools,
                output_model=spec.output_model,
                max_turns=spec.max_turns,
                max_tokens=spec.max_output_tokens,
            )
        except Exception as exc:
            code, detail = _classify(exc)
            run.status = "failed"
            run.error_code = code
            run.error_detail = detail
            run.ended_at = utcnow()
            record.error_code = code
            record.error_detail = detail
            self.host.finish_agent_run(run_id, _host_run_status("failed"), error=_host_error(code, detail))
            return StepOutcome(run=run, output=None, record=record)

        usage = getattr(result, "usage", None)
        run.usage = Usage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            usd=float(getattr(usage, "usd", 0.0) or 0.0),
        )
        if assessment_usage is not None:
            assessment_usage.add(run.usage)
        run.llm_turns = int(getattr(result, "turns", 0) or 0)
        run.tool_calls = len(record.tool_invocations)
        run.status = "succeeded"
        run.ended_at = utcnow()
        run.output_ref = f"artifact:{run_id}:output"
        run.transcript_ref = f"transcript:{run_id}"
        record.usage = run.usage
        record.turns = list(getattr(result, "transcript", []) or [])
        self.host.finish_agent_run(
            run_id,
            _host_run_status("succeeded"),
            output=result.final.model_dump(mode="json"),
            transcript=record.to_json(),
            **_run_accounting(result),
        )
        return StepOutcome(run=run, output=result.final, record=record)


def _run_accounting(result: Any) -> dict[str, Any]:
    """Per-run usage and tool-call records from the backend gateway's loop result
    (shared models), so the UI shows each agent's cost and tool history."""
    out: dict[str, Any] = {}
    try:
        from shared.assessment import ToolCallRecord
        from shared.tenancy import Usage as SharedUsage
    except ImportError:  # standalone install without the shared package
        return out
    usage = getattr(result, "usage", None)
    if isinstance(usage, SharedUsage):
        out["usage"] = usage
    calls = getattr(result, "tool_calls", None)
    if calls and all(isinstance(c, ToolCallRecord) for c in calls):
        out["tool_calls"] = list(calls)
    return out


def _val(obj: Any, name: str) -> Any:
    """The backend host exposes some members as properties (assessment, scope,
    provider_mode); fakes may use methods. Accept both."""
    attr = getattr(obj, name, None)
    return attr() if callable(attr) else attr


def _classify(exc: Exception) -> tuple[ErrorCode, str]:
    code = getattr(exc, "code", None)
    name = getattr(code, "value", None) or getattr(code, "name", None)
    detail = str(exc)[:300]
    if isinstance(name, str):
        try:
            return ErrorCode(name), detail
        except ValueError:
            pass
    if isinstance(exc, PlatformError):
        return exc.code, detail
    return ErrorCode.LLM_UNAVAILABLE, detail


def _host_agent(agent: AgentName) -> Any:
    import importlib

    try:
        shared_assessment = importlib.import_module("shared.assessment")
    except ModuleNotFoundError:
        return agent.value
    return shared_assessment.AgentName(agent.value)


def _host_run_status(status: str) -> Any:
    import importlib

    try:
        shared_assessment = importlib.import_module("shared.assessment")
    except ModuleNotFoundError:
        return status
    return shared_assessment.AgentRunStatus(status)


def _host_error(code: ErrorCode, detail: str) -> Any:
    import importlib

    try:
        errors = importlib.import_module("shared.errors")
    except ModuleNotFoundError:
        return {"code": code.value, "message": detail}
    try:
        return errors.ErrorBody(code=errors.ErrorCode(code.value), message=detail[:2000])
    except ValueError:
        # Agent-local codes the shared catalog does not know yet must still reach the host.
        return {"code": code.value, "message": detail}


def _host_state(state: AssessmentState) -> Any:
    import importlib

    try:
        shared_assessment = importlib.import_module("shared.assessment")
    except ModuleNotFoundError:
        return HOST_STATE_NAMES[state]
    enum = shared_assessment.AssessmentState
    name = HOST_STATE_NAMES[state]
    for member in enum:
        if member.name.upper() == name or str(member.value).upper() == name:
            return member
    raise PlatformError(ErrorCode.CONFLICT_STATE, f"host has no assessment state matching {name}")


@dataclass
class HostTool:
    """Structurally identical to `backend.llm.Tool` (name, description, input_model,
    handler, definition()).

    Defined here so this package installs and tests without the backend's
    dependencies; the real gateway consumes tools by shape, and when `backend`
    is importable its own `Tool` is used instead.
    """

    name: str
    description: str
    input_model: type[Any]
    handler: Any
    parallel_safe: bool = False

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }


def _tool_class() -> type[Any]:
    import importlib

    try:
        return importlib.import_module("backend.llm").Tool
    except ModuleNotFoundError:
        return HostTool


# Sandboxed runs that only read the step's workspace. Worktree writes and patch.apply_fresh
# change state, so a turn containing one stays sequential.
_PARALLEL_EXEC = frozenset({"tests.run", "checks.run_template"})


def build_tools(spec: AgentSpec, ctx: ToolContext, registry: ToolRegistry, record: StepRecord) -> list[Any]:
    """Wrap each allowlisted tool for the host gateway, keeping enforcement here."""
    tool_cls = _tool_class()
    tools: list[Any] = []
    for name in registry.allowed_names(spec.name, spec.tools):
        definition = registry.get(name)
        if definition is None:  # pragma: no cover - allowlist and catalog agree
            continue
        tools.append(
            tool_cls(
                name=wire_name(name),
                description=definition.description,
                input_model=definition.args_model,
                handler=_handler(ctx, registry, record, name),
                parallel_safe=definition.side_effects == "none" or name in _PARALLEL_EXEC,
            )
        )
    return tools


def _handler(ctx: ToolContext, registry: ToolRegistry, record: StepRecord, name: str) -> Any:
    def run(args: Any) -> Any:
        payload: dict[str, Any]
        try:
            result = registry.invoke(ctx, wire_name(name), args.model_dump(mode="json"))
            payload = result.payload
            ok = result.ok
            error_code = str(payload.get("error")) if payload.get("error") else None
        except PlatformError as exc:
            ok = False
            error_code = exc.code.value
            payload = {
                "error": exc.code.value,
                "detail": exc.message,
                "note": "refused by the platform; this cannot be overridden",
            }
        record.tool_invocations.append(
            ToolInvocation(
                name=tool_name(name),
                args=redact(args.model_dump(mode="json")),
                ok=ok,
                error_code=error_code,
                summary=result_summary(payload),
            )
        )
        return payload

    return run


# --------------------------------------------------------------------------- sink


_REVIEW_JOIN_STATES = (
    AssessmentState.AWAITING_APPROVAL,
    AssessmentState.REPORTING,
    AssessmentState.COMPLETED,
    AssessmentState.FAILED,
    AssessmentState.CANCELLED,
    AssessmentState.BUDGET_EXHAUSTED,
)


@dataclass
class HostSink(AssessmentSink):
    """Mirrors every orchestration step into the host's store."""

    host: AssessmentHostLike
    commit_sha: str
    run_ids: dict[str, str] = field(default_factory=dict)
    recorded: list[str] = field(default_factory=list)
    sandbox_charged: float = 0.0
    # patch id -> (stored artifact id, stored sha256, sha256 of the bytes Verify executes)
    stored_diffs: dict[str, tuple[str, str | None, str, str | None]] = field(default_factory=dict)
    regression_artifact_ids: dict[str, str] = field(default_factory=dict)
    result: Any = None
    # The functional reviewer runs beside Scout/Probe (they share nothing) and is joined
    # before the run waits for approval or finishes, so its findings and approvals land first.
    _review: threading.Thread | None = None
    _review_error: BaseException | None = None

    def _run_review(self) -> None:
        try:
            _functional_review(self.host)
        except BaseException as exc:  # re-raised on the orchestrator thread at the join
            self._review_error = exc

    def join_review(self) -> None:
        if self._review is not None:
            self._review.join()
            self._review = None
        if self._review_error is not None:
            error, self._review_error = self._review_error, None
            raise error

    def on_transition(self, src: AssessmentState, dst: AssessmentState, note: str) -> None:
        if dst in _REVIEW_JOIN_STATES:
            self.join_review()
        if self.result is not None and dst in (AssessmentState.COMPLETED, AssessmentState.AWAITING_APPROVAL):
            _store_standards_artifacts(self, self.result)
        if (
            dst is AssessmentState.FAILED
            and self.result is not None
            and self.result.error_code
            and callable(getattr(self.host, "terminate", None))
        ):
            from shared.errors import ErrorCode as SharedErrorCode

            self.host.terminate(
                _host_state(dst),
                SharedErrorCode(self.result.error_code.value),
                self.result.error_detail or note or "Assessment failed",
            )
        else:
            self.host.transition(_host_state(dst), note)
        if dst is AssessmentState.FUNCTIONAL_REVIEW:
            self._review = threading.Thread(target=self._run_review, name="functional-review", daemon=True)
            self._review.start()

    def set_status(self, finding_id: str, status: str, reason: str | None = None) -> None:
        """Persist a lifecycle change through the host, which checks the edge and the evidence.
        A refusal is surfaced as progress (never silently dropped) without failing the run."""
        setter = getattr(self.host, "set_finding_status", None)
        if setter is None:  # standalone / fake hosts without the lifecycle API
            return
        try:
            setter(finding_id, status, actor="agent:orchestrator", reason=reason)
        except Exception as exc:  # reported as progress, not hidden
            self.host.progress(
                f"finding status {status} not applied: {str(exc)[:200]}", finding_id=finding_id
            )

    def settle(self, findings: list[FindingRecord], sandbox_seconds: float) -> None:
        """End of a run segment: final statuses, and sandbox time into the usage ledger."""
        self.join_review()
        for finding in findings:
            self.set_status(finding.finding_id, finding.status)
        delta = round(float(sandbox_seconds) - self.sandbox_charged, 3)
        charge = getattr(self.host, "charge", None)
        if delta > 0 and charge is not None:
            from shared.tenancy import Usage as SharedUsage

            charge(SharedUsage(sandbox_seconds=delta))
            self.sandbox_charged += delta

    def on_finding(self, finding: FindingRecord) -> None:
        import importlib

        try:
            findings_module = importlib.import_module("shared.findings")
        except ModuleNotFoundError:  # pragma: no cover
            return
        hypothesis = finding.hypothesis
        evidence = [
            contract_export.evidence_ref(ref, producer="agent:scout") for ref in hypothesis.cited_spans
        ]
        record = findings_module.Finding(
            id=finding.finding_id,
            assessment_id=self.host.assessment_id,
            commit_sha=self.commit_sha,
            kind=findings_module.FindingKind.security,
            standards=_finding_standards(finding),
            title=hypothesis.title[:500],
            status=findings_module.FindingStatus.hypothesis,
            subject_refs=[hypothesis.entry_point_id or ""] if hypothesis.entry_point_id else [],
            expectation_ids=[contract_export.canonical_expectation_id(e) for e in hypothesis.expectation_ids],
            evidence=evidence,
            confidence=contract_export.CONFIDENCE_SCORE[hypothesis.confidence],
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self.host.record_finding(record, actor="agent:scout")
        self.host.record(
            finding.finding_id, contract_export.hypothesis(hypothesis, finding_id=finding.finding_id)
        )
        self.recorded.append("finding")

    def on_record(self, finding: FindingRecord, record: Any, kind: str) -> None:
        mapped: Any | None = None
        if kind == "validation_result":
            try:
                mapped = contract_export.validation_result(record)
            except ValueError:
                # No executed run: cannot be represented canonically (gap 1).
                self.host.progress(
                    f"validation {record.outcome.value} has no executed check to record",
                    finding_id=finding.finding_id,
                )
                return
        elif kind == "attack_path":
            mapped = contract_export.attack_path(record)
            if hasattr(self.host, "record_standards"):
                self.host.record_standards(
                    finding.finding_id,
                    _finding_standards(finding),
                    severity=contract_export.severity(finding.severity) if finding.severity else None,
                )
        elif kind == "root_cause":
            mapped = contract_export.root_cause(record)
        elif kind == "remediation_plan":
            mapped = contract_export.remediation_plan(record)
        elif kind == "hardening_recommendation":
            if hasattr(self.host, "record_standards"):
                self.host.record_standards(
                    finding.finding_id,
                    _finding_standards(finding),
                    severity=contract_export.severity(finding.severity) if finding.severity else None,
                )
            artifact_id = None
            bundle = finding.verified_regression_bundle
            if bundle and finding.verifications and finding.candidates:
                verification = finding.verifications[-1]
                if (
                    verification.verdict == "accepted"
                    and bundle.get("verification_id") == verification.id
                    and bundle.get("patch_id") == finding.candidates[-1].id
                ):
                    artifact_id = self.regression_artifact_ids.get(verification.id)
                    if artifact_id is None:
                        from shared.evidence import ArtifactKind

                        artifact = self.host.store_json(bundle, kind=ArtifactKind.test_report)
                        artifact_id = artifact.id
                        self.regression_artifact_ids[verification.id] = artifact_id
            mapped = _hardening(
                record,
                proposed_test_artifact_id=artifact_id,
                attack=[ref for step in finding.attack_path.steps for ref in step.attack]
                if finding.attack_path
                else [],
            )
        if kind == "validation_result" and mapped is not None:
            mapped = self._with_http_evidence(record, mapped)
        if mapped is not None:
            self.host.record(finding.finding_id, mapped)
            self.recorded.append(kind)
            if kind == "validation_result":
                outcome = str(getattr(mapped.outcome, "value", mapped.outcome))
                self.set_status(
                    finding.finding_id,
                    {"confirmed": "validated"}.get(outcome, outcome),
                    reason=f"probe {outcome} via {mapped.check_template_id}",
                )

    def _with_http_evidence(self, record: Any, mapped: Any) -> Any:
        observations = record.observations or {}
        if observations.get("transport") != "http" or not observations.get("target_contacted"):
            return mapped
        import json

        from shared.evidence import ArtifactKind, EvidenceKind, EvidenceRef

        payload = {
            key: observations.get(key)
            for key in (
                "transport",
                "target_contacted",
                "target_requests",
                "target_log_sha256",
                "network",
                "driver",
            )
        }
        payload["sandbox_run_id"] = record.sandbox_run_id
        artifact = self.host.store_text(
            json.dumps(payload, indent=2, sort_keys=True),
            kind=ArtifactKind.tool_output,
            media_type="application/json",
        )
        ref = EvidenceRef(
            kind=EvidenceKind.tool_output,
            artifact_id=artifact.id,
            sha256=artifact.sha256,
            provenance="agent:probe",
            note="http check transport: requests observed in the target container log",
        )
        return mapped.model_copy(update={"evidence": [*mapped.evidence, ref]})

    def on_patch(self, finding: FindingRecord, candidate: Any, diff: str) -> None:
        from shared.evidence import ArtifactKind

        artifact = self.host.store_text(diff, kind=ArtifactKind.diff, media_type="text/x-diff")
        artifact_id = getattr(artifact, "id", None) or str(artifact)
        sealed_id = None
        if hasattr(self.host, "seal_diff"):
            sealed = self.host.seal_diff(diff, candidate.diff_sha256)
            sealed_id = getattr(sealed, "id", None) or str(sealed)
        # Verify executes the in-memory diff; the stored member copy may be redacted (#155).
        self.stored_diffs[candidate.id] = (
            artifact_id,
            getattr(artifact, "sha256", None),
            candidate.diff_sha256,
            sealed_id,
        )
        run_id = self.run_ids.get("patch")
        if run_id is None:  # never cite a run the host did not create
            raise PlatformError(ErrorCode.CONFLICT_STATE, "patch recorded without a host patch run")
        self.host.record_patch(
            contract_export.patch_candidate(
                candidate,
                base_commit_sha=self.commit_sha,
                produced_by_run_id=run_id,
                rationale=finding.plan.strategy if finding.plan else "",
                diff_artifact_id=artifact_id,
                diff_sha256=getattr(artifact, "sha256", None),
            )
        )
        self.recorded.append("patch")
        self.set_status(finding.finding_id, "patch_proposed", reason="patch candidate recorded")

    def on_verification(self, finding: FindingRecord, result: Any) -> None:
        canonical = contract_export.verification_result(
            result, produced_by_run_id=result.verify_run_id or self.run_ids["verify"]
        )
        canonical = self._with_http_recheck_evidence(finding, canonical)
        self.host.record_verification(self._bind_verified_bytes(canonical))
        self.recorded.append("verification")
        if str(getattr(result.verdict, "value", result.verdict)) == "accepted":
            self.set_status(finding.finding_id, "verified", reason="independent Verify accepted")

    def _with_http_recheck_evidence(self, finding: FindingRecord, canonical: Any) -> Any:
        evidence = finding.verify_http_evidence
        if not evidence:
            return canonical
        import json

        from shared.evidence import ArtifactKind, EvidenceKind, EvidenceRef

        artifact = self.host.store_text(
            json.dumps(evidence, indent=2, sort_keys=True),
            kind=ArtifactKind.tool_output,
            media_type="application/json",
        )
        ref = EvidenceRef(
            kind=EvidenceKind.tool_output,
            artifact_id=artifact.id,
            sha256=artifact.sha256,
            provenance="agent:verify",
            note="http re-check against the patched workspace served in a hardened target",
        )
        checks = [
            c.model_copy(update={"evidence": [*c.evidence, ref]})
            if str(getattr(c.name, "value", c.name)) == "original_check_now_fails_to_reproduce"
            else c
            for c in canonical.checks
        ]
        return canonical.model_copy(update={"checks": checks})

    def _bind_verified_bytes(self, canonical: Any) -> Any:
        """Bind Verify's verdict to the stored member diff and the delivery-only sealed copy."""
        stored = self.stored_diffs.get(canonical.patch_id)
        if stored is None or not hasattr(self.host, "store_json"):
            return canonical
        stored_id, stored_sha, verified_sha, sealed_id = stored
        binding_doc = {
            "patch_id": canonical.patch_id,
            "verified_diff_sha256": verified_sha,
            "stored_diff_artifact_id": stored_id,
            "stored_diff_sha256": stored_sha,
            "redacted": verified_sha != stored_sha,
        }
        if sealed_id:
            binding_doc["sealed_diff_artifact_id"] = sealed_id
        binding = self.host.store_json(binding_doc)
        ev = importlib.import_module("shared.evidence")
        ref = ev.EvidenceRef(
            kind=ev.EvidenceKind.artifact,
            artifact_id=binding.id,
            sha256=binding.sha256,
            provenance="agent:verify",
            note="verified-bytes binding: the diff Verify executed vs the stored diff artifact",
        )
        checks = [
            c.model_copy(update={"evidence": [*c.evidence, ref]})
            if str(getattr(c.name, "value", c.name)) == "diff_scope_ok"
            else c
            for c in canonical.checks
        ]
        return canonical.model_copy(update={"checks": checks})

    def on_standards(self, finding: FindingRecord) -> None:
        if hasattr(self.host, "record_standards"):
            self.host.record_standards(
                finding.finding_id,
                _finding_standards(finding),
                severity=contract_export.severity(finding.severity) if finding.severity else None,
            )
            self.recorded.append("standards")

    def on_status(self, finding: FindingRecord, status: str) -> None:
        self.host.progress(f"finding {status}", finding_id=finding.finding_id, status=status)
        self.set_status(finding.finding_id, status)


def _hardening(
    record: Any, *, attack: list[Any] | None = None, proposed_test_artifact_id: str | None = None
) -> Any:
    import importlib

    try:
        findings_module = importlib.import_module("shared.findings")
    except ModuleNotFoundError:  # pragma: no cover
        return None

    refs = {ref.id: ref for ref in record.d3fend}
    return findings_module.HardeningRecommendation(
        id=record.id,
        finding_id=record.finding_id,
        category=record.category,
        proposed_test_artifact_id=proposed_test_artifact_id,
        recommendation=record.recommendation[:500],
        effort=record.effort,
        d3fend=list(refs.values()),
    )


# --------------------------------------------------------------------------- orchestrator


def _store_standards_artifacts(sink: HostSink, result: Any) -> None:
    import json

    from shared.evidence import ArtifactKind

    from .standards.sarif import to_sarif

    stored: dict[str, str] = {}
    try:
        if result.findings:
            log = to_sarif(result, commit_sha=sink.commit_sha, run_mode="hosted")
            ref = sink.host.store_text(
                json.dumps(log, indent=2), kind=ArtifactKind.report, media_type="application/sarif+json"
            )
            stored["sarif_artifact_id"] = str(getattr(ref, "id", ref))
        if result.roe is not None:
            ref = sink.host.store_text(
                json.dumps(result.roe.model_dump(mode="json"), indent=2),
                kind=ArtifactKind.report,
                media_type="application/json",
            )
            stored["roe_artifact_id"] = str(getattr(ref, "id", ref))
    except Exception as exc:  # reported, never hidden, and never fails the assessment
        sink.host.progress(f"standards artifacts not stored: {str(exc)[:200]}")
        return
    if stored:
        sink.host.progress("standards artifacts stored (SARIF 2.1.0, rules of engagement)", **stored)


@dataclass
class HostOrchestrator:
    """`security_agents.api:build_orchestrator()` result: `run(host)`.

    Snapshot source: the host contract does not yet hand the orchestrator a
    checked-out workspace, so the target directory comes from
    `SECURITY_AGENTS_TARGET_DIR` (and expectations, until code intelligence
    lands, from `SECURITY_AGENTS_LABELS`). Both are recorded in the run's
    provenance. Replacing this with a host-provided snapshot is the open item in
    the coordination message to the contract owner.
    """

    target_dir: Path | None = None
    labels_path: Path | None = None
    workdir: Path | None = None
    driver: SandboxDriver | None = None
    registry: ToolRegistry | None = None
    max_hypotheses: int = 3
    validate_count: int = 2
    _resumable: dict[str, Orchestrator] = field(default_factory=dict)

    def run(self, host: AssessmentHostLike) -> str | None:
        target = self.target_dir or _env_path("SECURITY_AGENTS_TARGET_DIR")
        existing = self._resumable.pop(host.assessment_id, None)
        state = _state_name(getattr(_val(host, "assessment"), "state", None))
        start_states = ("", "CREATED", "SCOPE_CHECK")
        if existing is not None or state == "AWAITING_APPROVAL":
            claimed = _claim_checkpoint(host)
            if claimed is _CHECKPOINT_CLAIMED_ELSEWHERE:
                host.progress("approval resume already claimed by another worker")
                return CLAIMED_ELSEWHERE
            if existing is not None:
                self._resume(existing, host)
                return
            if not isinstance(claimed, dict):
                state = self._restart_lost(host, state)
                if state != "SCOPE_CHECK":
                    return CLAIMED_ELSEWHERE if state == CLAIMED_ELSEWHERE else None
            else:
                self._resume_from_checkpoint(host, claimed, target)
                return
        elif state not in start_states or (
            state == "SCOPE_CHECK"
            and getattr(getattr(getattr(host, "_job_ctx", None), "job", None), "attempts", 1) > 1
        ):
            if _claim_checkpoint(host) is _CHECKPOINT_CLAIMED_ELSEWHERE:
                # The approving worker already resumed in-process and moved the run on.
                host.progress("approval resume already claimed by another worker")
                return CLAIMED_ELSEWHERE
            state = self._restart_lost(host, state)
            if state != "SCOPE_CHECK":
                return CLAIMED_ELSEWHERE if state == CLAIMED_ELSEWHERE else None

        workdir = self._workdir(host)
        intel = self._build_intel(target, workdir, host)
        orchestrator = self._make_orchestrator(host, intel, workdir)
        # The host already performed the scope gate and the first transition.
        orchestrator.state = AssessmentState.CREATED
        result = orchestrator.run()
        self._settle(orchestrator)
        if result.state is AssessmentState.AWAITING_APPROVAL:
            self._resumable[host.assessment_id] = orchestrator
            _save_checkpoint(host, orchestrator, intel)
            host.progress("awaiting human remediation approval")

    @staticmethod
    def _restart_lost(host: AssessmentHostLike, state: str) -> str:
        restart = getattr(host, "restart_lost_state", None)
        if not callable(restart):
            raise PlatformError(
                ErrorCode.CONFLICT_STATE,
                f"cannot resume from {state}: paused orchestrator state was lost "
                "(worker restarted or another worker); start a new assessment",
            )
        outcome = restart(state)
        return "SCOPE_CHECK" if outcome == "restart" else outcome

    def _resume_from_checkpoint(
        self, host: AssessmentHostLike, checkpoint: dict[str, Any], target: Path | None
    ) -> None:
        """Rebuild an Orchestrator from the durable checkpoint after a worker restart."""
        from pydantic import TypeAdapter

        if checkpoint.get("version") != 1 or checkpoint.get("assessment_id") != host.assessment_id:
            raise PlatformError(ErrorCode.CONFLICT_STATE, "invalid approval checkpoint identity/version")
        workdir = self._workdir(host)
        intel = self._build_intel(target, workdir, host)
        if checkpoint.get("commit_sha") and checkpoint["commit_sha"] != intel.snapshot.commit_sha:
            raise PlatformError(ErrorCode.SNAPSHOT_NOT_READY, "approval snapshot changed")
        orchestrator = self._make_orchestrator(host, intel, workdir)
        orchestrator.state = AssessmentState.AWAITING_APPROVAL
        orchestrator.result.state = AssessmentState.AWAITING_APPROVAL
        orchestrator.result.findings = TypeAdapter(list[FindingRecord]).validate_python(
            checkpoint["findings"]
        )
        orchestrator.result.usage = Usage.model_validate(checkpoint["usage"])
        orchestrator._baseline_failing = list(checkpoint.get("baseline_failing") or [])
        baseline_audit = checkpoint.get("baseline_pytest_audit")
        orchestrator._baseline_audit = baseline_audit if isinstance(baseline_audit, dict) else None
        sink = orchestrator.sink
        if isinstance(sink, HostSink):
            sink.sandbox_charged = float(checkpoint.get("sandbox_charged") or 0)
        self._resume(orchestrator, host)

    def _make_orchestrator(self, host: AssessmentHostLike, intel: CodeIntel, workdir: Path) -> Orchestrator:
        scope = _scope_for_host(host, intel)
        config = OrchestratorConfig(
            assessment_id=host.assessment_id,
            project_id=getattr(host, "project_id", "prj_unknown"),
            org_id=getattr(host, "org_id", "org_unknown"),
            scope=scope,
            target_id=scope.targets[0].target_id,
            requested_by=scope.requested_by,
            budget=_adapt_budget(_val(host, "assessment")),
            max_hypotheses=self.max_hypotheses,
            max_hypotheses_to_validate=self.validate_count,
            probe_check_transport=os.environ.get("SECURITY_AGENTS_PROBE_TRANSPORT", "inprocess"),
            verify_check_transport=os.environ.get("SECURITY_AGENTS_VERIFY_TRANSPORT", "inprocess"),
            **_commit_change(host),
        )
        registry = self.registry or default_registry()
        sink = HostSink(host=host, commit_sha=intel.snapshot.commit_sha)
        orchestrator = Orchestrator(
            config,
            intel=intel,
            gateway=_NullGateway(),  # host.llm() is the only provider path here
            driver=self.driver or _driver(),
            library=default_library(),
            workdir=workdir / "work",
            registry=registry,
            sink=sink,
            step_runner=HostStepRunner(host=host, tool_factory=build_tools, run_ids=sink.run_ids),
            approve_remediation=_approver(host),
            cancelled=lambda: _cancelled(host),
            external_pending=lambda: _pending_remediation(host) or _has_approved_functional(host),
        )
        usage = getattr(_val(host, "assessment"), "usage", None)
        if usage is not None:
            orchestrator.result.usage = Usage(
                input_tokens=int(getattr(usage, "input_tokens", 0)),
                output_tokens=int(getattr(usage, "output_tokens", 0)),
                usd=float(getattr(usage, "usd", 0)),
                sandbox_seconds=float(getattr(usage, "sandbox_seconds", 0)),
            )
            sink.sandbox_charged = orchestrator.result.usage.sandbox_seconds
        sink.result = orchestrator.result
        return orchestrator

    def _workdir(self, host: AssessmentHostLike) -> Path:
        import tempfile

        base = self.workdir or _env_path("SECURITY_AGENTS_WORKDIR")
        if base is None:
            return Path(tempfile.mkdtemp(prefix=f"{host.assessment_id}-"))
        base.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=f"{host.assessment_id}-", dir=base))

    def _resume(self, orchestrator: Orchestrator, host: AssessmentHostLike) -> None:
        """Continue after a human decided, using the approvals the host holds.

        Stay paused while any remediation approval (security or functional) is still
        pending so a partial decision does not skip the rest.
        """
        from .scope_guard import ActionRequest, check

        scope = _scope_for_host(host, orchestrator.intel)
        decision = check(scope, ActionRequest(action="assessment_start"), utcnow())
        if not decision.allowed:
            raise PlatformError(ErrorCode.SCOPE_DENIED, decision.reason_code)
        orchestrator.config.scope = scope
        orchestrator.roe = orchestrator._issue_roe()
        if isinstance(orchestrator.sink, HostSink):
            orchestrator.sink.host = host
        if isinstance(orchestrator.step_runner, HostStepRunner):
            orchestrator.step_runner.host = host
        orchestrator.cancelled = lambda: _cancelled(host)
        if _pending_remediation(host):
            host.progress("awaiting the remaining remediation approval decisions")
            _save_checkpoint(host, orchestrator, orchestrator.intel)
            self._resumable[host.assessment_id] = orchestrator
            return
        orchestrator.approve_remediation = _approver(host)
        diagnosed = [f for f in orchestrator.result.findings if f.plan is not None]
        approved: list[FindingRecord] = []
        for finding in diagnosed:
            try:
                decision = _approver(host)(finding)
            except ApprovalPending:
                host.progress("awaiting human remediation approval")
                _save_checkpoint(host, orchestrator, orchestrator.intel)
                return
            if decision is not None and decision.decision == "approved":
                finding.approval = decision
                approved.append(finding)
        functional_approved = _has_approved_functional(host)
        if not approved and not functional_approved:
            orchestrator._goto(AssessmentState.REPORTING, "remediation declined")
            orchestrator._finish()
            self._settle(orchestrator)
            return
        orchestrator.remediate_and_harden(approved, lambda: _repair_functional(host, orchestrator))
        orchestrator._goto(AssessmentState.REPORTING)
        orchestrator._finish()
        self._settle(orchestrator)

    @staticmethod
    def _settle(orchestrator: Orchestrator) -> None:
        sink = orchestrator.sink
        if isinstance(sink, HostSink):
            sink.settle(orchestrator.result.findings, orchestrator.result.usage.sandbox_seconds)
            record_roe = getattr(sink.host, "record_roe", None)
            roe = orchestrator.result.roe
            if roe is not None and callable(record_roe):
                record_roe(roe.model_dump(mode="json"))

    def _build_intel(self, target: Path | None, workdir: Path, host: Any = None) -> CodeIntel:
        """Snapshot for this assessment.

        Preferred: the operator's trusted checkout from the backend's shared registry
        (``REPOSITORY_BINDINGS_FILE``) archived at exactly ``assessment.commit_sha``
        (``pinned_by="git"``), honoring ``build_recipe.json`` ``target_dir``.
        Fallback (dev/test only): ``SECURITY_AGENTS_TARGET_DIR`` copied as a directory
        snapshot (``pinned_by="directory-digest"``).

        ``labels.yaml`` is the evaluation answer key and is never read implicitly; an
        explicit ``labels_path`` / ``SECURITY_AGENTS_LABELS`` is a dev/test override.
        """
        import json
        import shutil

        snapshot = _pinned_snapshot(host, workdir) if host is not None else None
        strip_prefix = None
        if snapshot is None:
            if target is None:
                raise PlatformError(
                    ErrorCode.SNAPSHOT_NOT_READY,
                    "no trusted checkout bound for this repository (REPOSITORY_BINDINGS_FILE) "
                    "and no SECURITY_AGENTS_TARGET_DIR",
                )
            recipe_path = target / "build_recipe.json"
            source = target
            if recipe_path.is_file():
                recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
                if recipe.get("target_dir"):
                    source = target / str(recipe["target_dir"])
                    strip_prefix = f"{recipe['target_dir']}/"
            staging = workdir / "staging"
            copy_tree(source, staging)
            if recipe_path.is_file():
                shutil.copy2(recipe_path, staging / "build_recipe.json")
            snapshot = SnapshotHandle.from_directory(
                staging, workdir / "snapshot", snapshot_id=new_id("snp"), repo_id="rep_host"
            )
        labels = self.labels_path or _env_path("SECURITY_AGENTS_LABELS")
        if labels is None and host is not None:
            from .intel.host_service import shared_host_intel

            shared = shared_host_intel(host, snapshot, _bindings())
            if shared is not None:
                return shared
        expectations = None
        if labels is not None:
            expectations = load_expectations(
                labels,
                snapshot_id=snapshot.snapshot_id,
                commit_sha=snapshot.commit_sha,
                strip_prefix=strip_prefix,
            )
        return LocalIntel(snapshot, expectations=expectations)


def _bindings() -> list[dict[str, Any]]:
    """The backend's shared repository registry (also used by code_review)."""
    import json

    path: Path | None = None
    try:
        from backend.settings import get_settings  # optional: present inside the worker

        path = get_settings().repository_bindings_file
    except Exception:
        path = _env_path("REPOSITORY_BINDINGS_FILE")
    if not path or not Path(path).is_file():
        return []
    return list(json.loads(Path(path).read_text(encoding="utf-8")))


def _pinned_snapshot(host: Any, workdir: Path) -> SnapshotHandle | None:
    import json
    import shutil

    repo_id = getattr(host, "repo_id", None)
    binding = next((b for b in _bindings() if b.get("repo_id") == repo_id), None)
    if binding is None:
        return None
    assessment = _val(host, "assessment")
    sha = str(assessment.commit_sha)
    subdir = str(binding.get("subdir") or ".")
    snapshot_id = str(getattr(assessment, "snapshot_id", None) or new_id("snp"))
    raw = SnapshotHandle.from_git(
        Path(binding["local_path"]).expanduser().resolve(),
        sha,
        subdir,
        workdir / "checkout",
        snapshot_id=snapshot_id,
        repo_id=str(repo_id),
    )
    recipe = raw.root / "build_recipe.json"
    target_dir = (
        json.loads(recipe.read_text(encoding="utf-8")).get("target_dir") if recipe.is_file() else None
    )
    if not target_dir:
        return raw
    if str(target_dir).startswith("/") or ".." in Path(str(target_dir)).parts:
        raise PlatformError(
            ErrorCode.SCHEMA_INVALID, "build_recipe.json target_dir must stay inside the checkout"
        )
    root = workdir / "snapshot"
    copy_tree(raw.root / str(target_dir), root)
    shutil.copy2(recipe, root / "build_recipe.json")
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    return SnapshotHandle(
        snapshot_id=snapshot_id, repo_id=str(repo_id), commit_sha=raw.commit_sha, root=root, pinned_by="git"
    )


_PROPAGATE = {"CANCELLED", "BUDGET_EXHAUSTED", "SCOPE_DENIED"}


def _state_name(raw: Any) -> str:
    if raw is None:
        return ""
    value = getattr(raw, "value", raw)
    return str(value or "").upper()


def _load_checkpoint(host: AssessmentHostLike) -> dict[str, Any] | None:
    loader = getattr(host, "load_checkpoint", None)
    if not callable(loader):
        return None
    payload = loader(CHECKPOINT_NS)
    return payload if isinstance(payload, dict) else None


def _claim_checkpoint(host: AssessmentHostLike) -> dict[str, Any] | object | None:
    """Consume the durable pause payload, or signal that another worker already did.

    Returns the checkpoint dict, ``_CHECKPOINT_CLAIMED_ELSEWHERE`` if a previous
    caller already consumed it, or ``None`` if nothing was ever saved. Hosts
    without ``claim_checkpoint`` fall back to a non-atomic load.
    """
    claimer = getattr(host, "claim_checkpoint", None)
    if callable(claimer):
        try:
            payload = claimer(CHECKPOINT_NS)
        except Exception as exc:
            code = str(getattr(getattr(exc, "code", None), "value", getattr(exc, "code", "")) or "")
            if code == "CONFLICT_STATE":
                return None
            raise
        if payload is None:
            return _CHECKPOINT_CLAIMED_ELSEWHERE
        return payload if isinstance(payload, dict) else None
    return _load_checkpoint(host)


def _save_checkpoint(host: AssessmentHostLike, orchestrator: Orchestrator, intel: CodeIntel) -> None:
    saver = getattr(host, "save_checkpoint", None)
    if not callable(saver):
        return
    from pydantic import TypeAdapter

    sink = orchestrator.sink
    charged = sink.sandbox_charged if isinstance(sink, HostSink) else 0.0
    saver(
        CHECKPOINT_NS,
        {
            "version": 1,
            "assessment_id": host.assessment_id,
            "commit_sha": intel.snapshot.commit_sha,
            "snapshot_id": intel.snapshot.snapshot_id,
            "findings": TypeAdapter(list[FindingRecord]).dump_python(
                orchestrator.result.findings, mode="json"
            ),
            "usage": orchestrator.result.usage.model_dump(mode="json"),
            "baseline_failing": list(orchestrator._baseline_failing),
            "baseline_pytest_audit": orchestrator._baseline_audit,
            "sandbox_charged": charged,
        },
    )


def _functional_review(host: Any) -> None:
    """Pillar 1 inside the assessment: Sai's reviewer on the same commit, persisting its own
    run and findings through the host. Unavailable/failing review is recorded and the
    security run continues; cancel/budget/scope errors propagate."""
    try:
        from code_review import api as review_api
    except ImportError:
        host.progress("functional review unavailable: code_review is not installed in this worker")
        return
    intent = _change_intent(host)
    try:
        try:
            outcome = review_api.review_assessment(host, change_intent=intent)
        except Exception as exc:
            if str(getattr(getattr(exc, "code", None), "value", "")) != "SNAPSHOT_NOT_READY":
                raise
            outcome = _review_without_snapshot(host, intent, review_api)
        result = outcome.result
        host.progress(
            f"functional review {result.status.value}: {len(result.findings)} functional finding(s)"
        )
        requested = review_api.request_functional_approvals(host)
        if requested:
            host.progress(f"requested {len(requested)} functional remediation approval(s)")
    except Exception as exc:
        code = str(getattr(getattr(exc, "code", None), "value", type(exc).__name__))
        if code in _PROPAGATE:
            raise
        host.progress(f"functional review failed ({code}): {exc}"[:500])


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _pending_remediation(host: AssessmentHostLike) -> bool:
    """True when a human still owes a remediation decision (security or functional)."""
    return any(
        "remediation" in _enum_value(getattr(a, "subject_type", ""))
        and "pending" in _enum_value(getattr(a, "status", ""))
        for a in host.approvals()
    )


def _has_approved_functional(host: AssessmentHostLike) -> bool:
    approved = {
        str(getattr(a, "subject_id", ""))
        for a in host.approvals()
        if "remediation" in _enum_value(getattr(a, "subject_type", ""))
        and "approved" in _enum_value(getattr(a, "status", ""))
    }
    finder = getattr(host, "findings", None)
    findings = finder() if callable(finder) else []
    return any(
        _enum_value(getattr(f, "kind", "")) == "functional"
        and _enum_value(getattr(f, "status", "")) == "validated"
        and str(getattr(f, "id", "")) in approved
        for f in findings
    )


def _repair_functional(host: AssessmentHostLike, orchestrator: Orchestrator) -> None:
    """Run Sai's approval-gated repair for validated functional findings, then Verify."""
    try:
        from code_review import api as review_api
    except ImportError:
        return
    try:
        source = review_api.resolve_repo(
            str(getattr(host, "repo_id", "")),
            commit_sha=str(getattr(_val(host, "assessment"), "commit_sha", "") or ""),
        )
    except Exception as exc:
        host.progress(f"functional repair skipped: {exc}"[:500])
        return
    try:
        outcomes = review_api.repair_approved_functional_findings(
            host, repo_id=str(getattr(host, "repo_id", "")), source=source
        )
    except Exception as exc:
        code = str(getattr(getattr(exc, "code", None), "value", type(exc).__name__))
        if code in _PROPAGATE:
            raise
        host.progress(f"functional repair failed ({code}): {exc}"[:500])
        return
    for out in outcomes:
        title = getattr(getattr(out, "finding", None), "id", "")
        if getattr(out, "patch", None) is None:
            host.progress(f"functional repair produced no patch for {title}")
            continue
        host.progress(f"functional patch recorded for {title}")
        _verify_functional_patch(host, orchestrator, out)


def _tests_passed(result: dict[str, Any] | None) -> bool:
    if not result:
        return False
    return (
        not result.get("error")
        and result.get("exit_code") == 0
        and not result.get("timed_out")
        and bool(result.get("sandbox_run_id"))
        and not result.get("skipped")
        and not result.get("errors")
        and not result.get("failed")
        and not result.get("failing_tests")
        and bool(result.get("passed"))
    )


def _functional_reproduced(result: dict[str, Any] | None, tests: list[str]) -> bool:
    """Only a complete pytest-failed run counts as reproduction (shared predicate, see
    ``verify.clean_reproduction``)."""
    from .agents.verify import clean_reproduction

    return clean_reproduction(result, tests)


def _pytest_list_truncated(result: dict[str, Any] | None) -> bool:
    """True when pytest reported more failures than the retained node-id list."""
    from .agents.verify import pytest_list_truncated

    return pytest_list_truncated(result)


def _recipe_strip_prefix(root: Path, paths: list[str]) -> str:
    """Reviewer diffs are repository-relative (`target/...`); the intel snapshot is already
    the recipe ``target_dir``. Strip that complete directory, not only the first component."""
    import json

    from .sandbox.workspace import safe_rel_path

    recipe_dir = ""
    recipe = root / "build_recipe.json"
    if recipe.is_file():
        try:
            raw = str(json.loads(recipe.read_text(encoding="utf-8")).get("target_dir") or "").strip("/")
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            raw = ""
        if raw and not raw.startswith("/") and ".." not in Path(raw).parts:
            recipe_dir = raw

    for raw in paths:
        try:
            rel = safe_rel_path(str(raw))
        except Exception:
            continue
        if (
            recipe_dir
            and (rel == recipe_dir or rel.startswith(recipe_dir + "/"))
            and not (root / recipe_dir).exists()
        ):
            return recipe_dir
        first, _, rest = rel.partition("/")
        if rest and not (root / first).exists():
            return first
    return ""


def _snapshot_rel(rel: str, prefix: str) -> str:
    from .sandbox.workspace import safe_rel_path

    path = safe_rel_path(rel)
    if prefix and (path == prefix or path.startswith(prefix + "/")):
        path = path[len(prefix) :].lstrip("/")
    return path


def _diff_for_snapshot(diff_text: str, prefix: str) -> str:
    if not prefix:
        return diff_text
    token = prefix.strip("/") + "/"
    rewritten: list[str] = []
    for line in diff_text.splitlines(keepends=True):
        if line.startswith(("diff --git ", "--- ", "+++ ")):
            line = line.replace(f"a/{token}", "a/").replace(f"b/{token}", "b/")
        rewritten.append(line)
    return "".join(rewritten)


def _write_generated_tests(root: Path, tests: list[Any], prefix: str) -> list[str]:
    from .sandbox.workspace import resolve_under

    paths: list[str] = []
    for test in tests:
        path = str(getattr(test, "path", "") or "")
        content = str(getattr(test, "content", "") or "")
        if not path or not content:
            continue
        rel = _snapshot_rel(path, prefix)
        dest = resolve_under(root, rel)
        if dest.exists() or not ("tests" in Path(rel).parts or Path(rel).name.startswith("test_")):
            raise PlatformError(ErrorCode.SCHEMA_INVALID, "generated tests must be new test files")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        paths.append(rel)
    return paths


def _verify_functional_patch(host: AssessmentHostLike, orchestrator: Orchestrator, outcome: Any) -> None:
    """Independent Verify of a reviewer PatchCandidate (fresh checkout; not Patch's worktree).

    Uses Sai's `functional_verification_inputs` so the recorded artifact diff, generated
    tests, protected paths, and baseline failures are integrity-checked before any
    verdict. Functional findings have no Probe template; the original-check analogue is
    that the generated tests fail on the unpatched commit and pass after the diff.
    """
    from shared.base import utcnow
    from shared.evidence import ArtifactKind, EvidenceKind, EvidenceRef
    from shared.findings import (
        VerificationCheck,
        VerificationCheckName,
        VerificationResult,
        VerificationVerdict,
    )
    from shared.ids import IdPrefix, new_id

    from .agents import verify as verify_agent
    from .sandbox.workspace import apply_diff, diff_paths, fresh_checkout

    try:
        from code_review import api as review_api
    except ImportError:
        host.progress("functional Verify skipped: code_review is not installed")
        return

    patch = outcome.patch
    try:
        inputs = review_api.functional_verification_inputs(host, patch)
    except Exception as exc:
        host.progress(f"functional Verify skipped: {exc}"[:500])
        return
    diff_text = inputs.diff_text
    if not diff_text:
        host.progress(f"functional Verify skipped: empty diff for {patch.finding_id}")
        return
    # This run makes NO model call: the verdict is computed from executed tests. Label it
    # truthfully so a live assessment never shows it as a live-provider agent turn.
    run_id = host.start_agent_run(
        _host_agent(AgentName.VERIFY),
        f"functional-verify:{patch.finding_id}",
        attempt=1,
        provider_mode="none",
        model_id="deterministic:functional-verify",
        prompt_version=verify_agent.SPEC.version,
        input_payload={"patch_id": patch.id, "finding_id": patch.finding_id, "kind": "functional"},
    )
    try:
        dest = orchestrator.workdir / f"verify-functional-{patch.id}"
        root = fresh_checkout(orchestrator.intel.snapshot, dest)
        prefix = _recipe_strip_prefix(
            root,
            [str(p) for p in (patch.files_changed or [])]
            + [str(getattr(t, "path", "") or "") for t in inputs.generated_tests],
        )
        tests_added = _write_generated_tests(root, list(inputs.generated_tests), prefix)
        snapshot_diff = _diff_for_snapshot(diff_text, prefix)
        ctx = orchestrator._context(
            AgentName.VERIFY,
            f"functional-verify:{patch.finding_id}",
            services=orchestrator._read_services(),
            read_root=root,
        )
        ctx.run_id = run_id
        ctx.artifacts["tests_added"] = tests_added
        runner = orchestrator._test_runner(lambda _ctx: root)
        unpatched = runner.run(ctx, "added_tests", tests_added) if tests_added else None
        ok, err = apply_diff(root, snapshot_diff)
        touched = [_snapshot_rel(p, prefix) for p in diff_paths(diff_text)]
        plan_files = [_snapshot_rel(str(p), prefix) for p in (patch.files_changed or [])]
        roots = recipe_test_roots(root)
        out_of_scope = [p for p in touched if p not in plan_files and not under_test_roots(p, roots)]
        from .regressions import RegressionEvidenceUnavailable, read_sources, verified_bundle

        try:
            regression_sources = read_sources(root, tests_added) if ok else None
        except (RegressionEvidenceUnavailable, PlatformError):
            regression_sources = None
        patched = runner.run(ctx, "added_tests", tests_added) if tests_added and ok else None
        suite = runner.run(ctx, "project_suite", []) if ok else {}
        failing = set(suite.get("failing_tests") or [])
        new_failures = sorted(failing - set(inputs.baseline_failing or []))
        unpatched_failed = _functional_reproduced(unpatched, tests_added)
        patched_passed = bool(tests_added) and _tests_passed(patched)
        suite_ok = (
            bool(ok)
            and suite.get("exit_code") in (0, 1)
            and not suite.get("timed_out")
            and bool(suite.get("sandbox_run_id"))
            and not suite.get("error")
            and not suite.get("errors")
            and not new_failures
            and bool(suite.get("passed"))
            and not _pytest_list_truncated(suite)
        )
        scope_ok = bool(ok) and not out_of_scope
        tamper_ok = not list(inputs.protected_paths)
        sandbox_ids = [
            str(r.get("sandbox_run_id")) for r in (unpatched, patched, suite) if r and r.get("sandbox_run_id")
        ]
        if not sandbox_ids:
            raise PlatformError(
                ErrorCode.SANDBOX_START_FAILED, "functional Verify has no executed sandbox records"
            )
        report = host.store_json(
            {
                "sandbox_run_ids": sandbox_ids,
                "unpatched": unpatched,
                "patched": patched,
                "suite": suite,
                "protected_paths": list(inputs.protected_paths),
                "baseline_failing": list(inputs.baseline_failing),
                "out_of_scope": out_of_scope,
            },
            kind=ArtifactKind.test_report,
        )
        evidence = [
            EvidenceRef(
                kind=EvidenceKind.sandbox_run,
                artifact_id=report.id,
                sha256=getattr(report, "sha256", None),
                provenance="agent:verify",
            )
        ]
        checks = [
            VerificationCheck(name=VerificationCheckName.diff_scope_ok, passed=scope_ok, evidence=evidence),
            VerificationCheck(
                name=VerificationCheckName.no_test_tampering, passed=tamper_ok, evidence=evidence
            ),
            VerificationCheck(
                name=VerificationCheckName.original_check_now_fails_to_reproduce,
                passed=unpatched_failed,
                evidence=evidence,
            ),
            VerificationCheck(
                name=VerificationCheckName.functional_suite_passes,
                passed=suite_ok,
                evidence=evidence,
            ),
            VerificationCheck(
                name=VerificationCheckName.generated_tests_pass, passed=patched_passed, evidence=evidence
            ),
        ]
        accepted = all(c.passed for c in checks)
        duration = sum(float((r or {}).get("duration_s") or 0) for r in (unpatched, patched, suite))
        orchestrator.result.usage.sandbox_seconds += duration
        host.finish_agent_run(run_id, _host_run_status("succeeded"))
        verification = VerificationResult(
            id=new_id(IdPrefix.ver),
            patch_id=patch.id,
            verdict=VerificationVerdict.accepted if accepted else VerificationVerdict.rejected,
            checks=checks,
            sandbox_run_ids=sandbox_ids,
            reasons=[]
            if accepted
            else [
                f"apply={ok} err={err}; out_of_scope={out_of_scope}; protected={list(inputs.protected_paths)}; "
                f"unpatched_failed={unpatched_failed}; patched={patched_passed}; "
                f"suite={suite_ok} new_failures={new_failures}"
            ],
            produced_by_run_id=run_id,
            created_at=utcnow(),
        )
        host.record_verification(verification)
        setter = getattr(host, "set_finding_status", None)
        if callable(setter):
            try:
                setter(
                    patch.finding_id,
                    "verified" if accepted else "patch_rejected",
                    actor="agent:verify",
                    reason=(
                        "independent functional Verify accepted"
                        if accepted
                        else "independent functional Verify rejected"
                    ),
                )
            except Exception as exc:
                host.progress(f"finding status not applied: {str(exc)[:200]}")
        if accepted and regression_sources is not None:
            from types import SimpleNamespace

            from shared.findings import HardeningRecommendation

            try:
                archive = verified_bundle(
                    SimpleNamespace(id=patch.id, finding_id=patch.finding_id, tests_added=tests_added),
                    verification,
                    root,
                    regression_sources,
                )
                archive["producer"] = "deterministic:functional-verify"
                artifact = host.store_json(archive, kind=ArtifactKind.test_report)
                host.record(
                    patch.finding_id,
                    HardeningRecommendation(
                        id=new_id(IdPrefix.hdn),
                        finding_id=patch.finding_id,
                        category="policy_test",
                        effort="small",
                        recommendation="Retain regression sources associated with the accepted independent functional verification.",
                        proposed_test_artifact_id=artifact.id,
                    ),
                )
            except (RegressionEvidenceUnavailable, PlatformError) as exc:
                host.progress(f"functional regression artifact unavailable: {exc}"[:500])
        host.progress(f"functional Verify {'accepted' if accepted else 'rejected'} for {patch.finding_id}")
    except Exception as exc:
        with contextlib.suppress(Exception):
            host.finish_agent_run(
                run_id,
                _host_run_status("failed"),
                error=_host_error(ErrorCode.INTERNAL, str(exc)[:500]),
            )
        host.progress(f"functional Verify failed: {exc}"[:500])


def _review_without_snapshot(host: Any, intent: Any, review_api: Any) -> Any:
    """No indexed snapshot row for this commit: review the pinned checkout directly."""
    from shared.intelligence import SnapshotRef
    from shared.review import ReviewRequest

    a = _val(host, "assessment")
    request = ReviewRequest(
        assessment_id=a.id,
        snapshot_ref=SnapshotRef(
            project_id=a.project_id,
            repo_id=host.repo_id,
            commit_sha=a.commit_sha,
            # Never invent a random id: with no indexed snapshot, use a documented synthetic id
            # derived from the commit, labelled by index_version so nothing mistakes it for an index.
            snapshot_id=a.snapshot_id or f"snp_unindexed-{a.commit_sha[:40]}",
            index_version="unindexed-checkout",
        ),
        change_intent=intent or _default_intent(a),
        budget=a.budget,
    )
    source = review_api.resolve_repo(host.repo_id, commit_sha=a.commit_sha)
    return review_api.review_full(host, request, source=source)


def _default_intent(a: Any) -> Any:
    from shared.review import ChangeIntent, ChangeIntentSource

    return ChangeIntent(
        source=ChangeIntentSource.manual, title=f"assessment of {a.commit_sha[:12]}", head_sha=a.commit_sha
    )


def _commit_change(host: Any) -> dict[str, Any]:
    """Changed paths and diff of the commit under review (empty when unknown)."""
    intent = _change_intent(host)
    base = getattr(intent, "base_sha", None)
    binding = next((b for b in _bindings() if b.get("repo_id") == getattr(host, "repo_id", None)), None)
    if not base or binding is None:
        return {}
    repo, head = str(Path(binding["local_path"]).expanduser()), str(_val(host, "assessment").commit_sha)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", repo, *args], capture_output=True, text=True, timeout=30, check=True
        ).stdout

    try:
        paths = tuple(p for p in git("diff", "--name-only", base, head).splitlines() if p)
        diff = git("diff", "--no-color", "-U5", base, head)
    except (OSError, subprocess.SubprocessError):
        return {}
    return {"change_paths": paths[:200], "change_diff": diff[:20_000]}


def _change_intent(host: Any) -> Any:
    """Push/PR intent (commit range, PR title/body as untrusted text) of the monitoring event
    that triggered this assessment; None for manual assessments."""
    a = _val(host, "assessment")
    if not getattr(a, "reassessment_request_id", None):
        return None
    try:
        import sqlalchemy as sa
        from backend.db import monitoring_events, reassessment_requests, transaction
        from shared.review import ChangeIntent, ChangeIntentSource
    except ImportError:
        return None
    with transaction() as conn:
        rrq = conn.execute(
            sa.select(reassessment_requests.c.doc).where(
                reassessment_requests.c.id == a.reassessment_request_id
            )
        ).scalar()
        ev = conn.execute(
            sa.select(monitoring_events.c.doc).where(
                monitoring_events.c.id == ((rrq or {}).get("event_ids") or [""])[0]
            )
        ).scalar()
    if not ev or not ev.get("commit_range"):
        return None
    kind = ChangeIntentSource.pull_request if ev.get("type") == "pull_request" else ChangeIntentSource.push
    return ChangeIntent(
        source=kind,
        title=f"{kind.value} {(ev.get('ref') or '')}"[:500],
        base_sha=ev["commit_range"].get("base_sha"),
        head_sha=a.commit_sha,
    )


def _driver() -> SandboxDriver:
    """Docker by default. The local-process driver only in development/test, for the
    owned demo target (no Docker on the host)."""
    from .sandbox.driver import DockerSandboxDriver

    mode, env = "docker", ""
    try:
        from backend.settings import get_settings

        settings = get_settings()
        mode, env = settings.security_sandbox, settings.app_env
    except Exception:
        mode, env = os.environ.get("SECURITY_SANDBOX", "docker"), os.environ.get("APP_ENV", "")
    if mode == "local-dev":
        if env not in ("development", "test"):
            raise PlatformError(
                ErrorCode.SANDBOX_POLICY_VIOLATION, "local-dev sandbox outside development/test"
            )
        return LocalProcessDriver(allow=True)
    if mode == "vercel":
        from .sandbox.vercel_driver import VercelSandboxDriver

        return VercelSandboxDriver()
    return DockerSandboxDriver()


class _NullGateway:
    """Placeholder gateway: in host mode every model call goes through the host."""

    mode = "host"

    def model_for(self, agent: AgentName) -> str:  # pragma: no cover - never called
        return "host"

    def complete(self, request: Any) -> Any:  # pragma: no cover - never called
        raise PlatformError(ErrorCode.CONFLICT_STATE, "host mode must route model calls through host.llm()")

    def cost_usd(self, model: str, usage: Any) -> float:  # pragma: no cover
        return 0.0


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else None


def _cancelled(host: AssessmentHostLike) -> bool:
    try:
        host.check_cancelled()  # type: ignore[attr-defined]
    except AttributeError:
        return False
    except Exception:
        return True
    return False


def _approver(host: AssessmentHostLike):
    """Approval policy: read the host's approvals; request one if none exists."""

    def approve(finding: FindingRecord) -> Approval | None:
        if finding.plan is None:
            return None
        subject_id = finding.plan.id
        for approval in host.approvals():
            if str(getattr(approval, "subject_id", "")) != subject_id:
                continue
            status = str(getattr(approval, "status", getattr(approval, "decision", ""))).lower()
            subject_type = str(getattr(approval, "subject_type", ""))
            if subject_type != "remediation":
                continue
            if status == "approved":
                actor = getattr(approval, "decided_by", None)
                if not actor or not str(actor).startswith("usr_"):
                    raise PlatformError(
                        ErrorCode.APPROVAL_REQUIRED, "remediation requires an identified human approver"
                    )
                return Approval(
                    id=str(getattr(approval, "id", new_id("apv"))),
                    project_id=str(getattr(host, "project_id", "prj_unknown")),
                    subject_type="remediation",
                    subject_id=subject_id,
                    decision="approved",
                    decided_by=str(approval.decided_by),
                )
            if status in {"declined", "rejected"}:
                return Approval(
                    id=str(getattr(approval, "id", new_id("apv"))),
                    project_id=str(getattr(host, "project_id", "prj_unknown")),
                    subject_type="remediation",
                    subject_id=subject_id,
                    decision="declined",
                    decided_by=str(approval.decided_by),
                )
            raise ApprovalPending(finding.finding_id, str(getattr(approval, "id", "")))
        requested = host.request_approval(
            _approval_subject(), subject_id, f"remediate {finding.hypothesis.title}"[:500]
        )
        raise ApprovalPending(finding.finding_id, str(getattr(requested, "id", "")))

    return approve


def _approval_subject() -> Any:
    import importlib

    try:
        scope_module = importlib.import_module("shared.scope")
    except ModuleNotFoundError:
        return "remediation"
    for attr in ("ApprovalSubject", "ApprovalSubjectType"):
        enum = getattr(scope_module, attr, None)
        if enum is not None:
            return enum("remediation")
    return "remediation"


def _scope_for_host(host: AssessmentHostLike, intel: CodeIntel) -> Any:
    """Adapt the current host scope and keep only targets for ``host.repo_id``."""
    scope = _adapt_scope(_val(host, "scope"), intel)
    repo_id = getattr(host, "repo_id", None)
    if repo_id:
        scope.targets = [target for target in scope.targets if target.repo_id == repo_id]
    if not scope.targets:
        raise PlatformError(
            ErrorCode.SCOPE_DENIED, "host scope has no authorized targets for this repository"
        )
    return scope


def _adapt_scope(host_scope: Any, intel: CodeIntel) -> Any:
    """Map the host's AuthorizedScope onto this package's scope model."""
    from .contracts import AuthorizedScope, ScopeStatus, ScopeTarget

    targets = []
    for index, target in enumerate(getattr(host_scope, "targets", []) or []):
        targets.append(
            ScopeTarget(
                target_id=str(
                    getattr(target, "target_id", None) or getattr(target, "id", None) or f"tgt_{index}"
                ),
                repo_id=str(getattr(target, "repo_id", None) or intel.snapshot.repo_id),
                build_recipe_ref=str(getattr(target, "build_recipe_ref", None) or "build_recipe.json"),
                exposed_ports=list(getattr(target, "exposed_ports", []) or []),
            )
        )
    raw_status = getattr(host_scope, "status", None)
    status_raw = str(getattr(raw_status, "value", raw_status)).lower()
    try:
        status = ScopeStatus(status_raw)
    except ValueError:
        status = ScopeStatus.PENDING
    approved_at = getattr(host_scope, "approved_at", None)
    return AuthorizedScope(
        id=str(getattr(host_scope, "id", new_id("scp"))),
        project_id=str(getattr(host_scope, "project_id", "prj_unknown")),
        name=str(getattr(host_scope, "name", "host scope")),
        targets=targets,
        allowed_check_categories=list(getattr(host_scope, "allowed_check_categories", None) or []),
        status=status,
        requested_by=str(getattr(host_scope, "requested_by", None) or "usr_host"),
        approved_by=getattr(host_scope, "approved_by", None),
        approved_at=approved_at,
        expires_at=getattr(host_scope, "expires_at", None) or utcnow(),
        notes="adapted from the host scope",
    )


def _adapt_budget(assessment: Any) -> Budget:
    budget = getattr(assessment, "budget", None)
    if getattr(budget, "max_tokens", 1) <= 0 or getattr(budget, "max_usd", 1) <= 0:
        raise PlatformError(ErrorCode.BUDGET_EXHAUSTED, "host budget does not permit model calls")
    return Budget(
        max_tokens=int(getattr(budget, "max_tokens", 1_000_000)),
        max_usd=float(getattr(budget, "max_usd", 10.0)),
        max_wall_clock_s=float(getattr(budget, "max_wall_clock_s", 0) or 3600.0),
    )


def build_orchestrator(**kwargs: Any) -> HostOrchestrator:
    """Factory the backend worker loads (`ORCHESTRATOR_FACTORY`)."""
    return HostOrchestrator(**kwargs)


SPEC_TABLE = {name.value: spec.version for name, spec in ALL_SPECS.items()}


def _finding_standards(finding: FindingRecord) -> Any:
    from . import standards_bridge

    return standards_bridge.finding_standards(finding, now=utcnow())
