"""The backend `AssessmentHost` integration (backend/INTEGRATION.md §Alan).

Driven against a fake host that implements the documented interface, with a
fake gateway whose `run_tool_loop` really executes the tools this package
passes it. That proves:

  * every agent turn goes through `host.llm()` — there is no second provider
    path in host mode;
  * tool permission enforcement still happens here (a denied tool is reported
    to the model, never executed);
  * state transitions, agent runs, findings, patches and verifications are
    mirrored into the host;
  * a remediation approval is requested and the orchestrator returns in
    `AWAITING_APPROVAL`, then completes on re-entry once the host holds an
    approval.

UNVERIFIED against the real backend: these tests do not run `backend.host`
(Postgres + artifact store). The shapes come from `backend/INTEGRATION.md` and
`backend/backend/{host,llm}.py` as of the merge recorded in the coordination
message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from security_agents.agents import ALL_SPECS
from security_agents.contracts import AgentName, ErrorCode, PlatformError
from security_agents.host_adapter import HostOrchestrator, build_orchestrator
from security_agents.workflow import AssessmentState

from . import fake_agents
from .conftest import PAYMENTS_LITE_DIR

shared = pytest.importorskip("shared", reason="canonical shared package not importable")
import shared.assessment as shared_assessment  # noqa: E402
import shared.findings as shared_findings  # noqa: E402


def test_failed_transition_preserves_orchestrator_error() -> None:
    from shared.errors import ErrorCode as SharedErrorCode

    from security_agents.host_adapter import HostSink

    class Host:
        def terminate(self, state, code, message):
            self.terminated = (state, code, message)

    host = Host()
    sink = HostSink(host=host, commit_sha="a" * 40)
    sink.result = SimpleNamespace(error_code=ErrorCode.SNAPSHOT_NOT_READY, error_detail="index unavailable")
    sink.on_transition(AssessmentState.INDEXING, AssessmentState.FAILED, "index unavailable")
    assert host.terminated == (
        shared_assessment.AssessmentState.FAILED,
        SharedErrorCode.SNAPSHOT_NOT_READY,
        "index unavailable",
    )


def test_failure_details_are_available_when_transition_is_emitted() -> None:
    from security_agents.workflow.orchestrator import Orchestrator

    result = SimpleNamespace(error_code=None, error_detail=None)

    def goto(state, note):
        assert state is AssessmentState.FAILED
        assert result.error_code is ErrorCode.SNAPSHOT_NOT_READY
        assert result.error_detail == note == "index unavailable"

    fake = SimpleNamespace(
        result=result,
        _goto=goto,
        _audit=lambda *args, **kwargs: None,
        config=SimpleNamespace(assessment_id="asm_test"),
    )
    assert Orchestrator._fail(fake, ErrorCode.SNAPSHOT_NOT_READY, "index unavailable") is result


SUBMIT = "submit_result"


# --------------------------------------------------------------------------- fakes


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    usd: float = 0.001


@dataclass
class FakeLoopResult:
    final: Any
    turns: int
    usage: FakeUsage
    tool_calls: list[Any]
    transcript: list[dict[str, Any]]
    model: str
    provider_mode: str


@dataclass
class FakeGateway:
    """Stands in for `backend.llm.LLMGateway`, running the scripted trajectories.

    It executes the handlers of the tools it is given, exactly as the real
    gateway does, so the registry's enforcement is exercised.
    """

    scripts: dict[AgentName, list]
    calls: list[dict[str, Any]] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)

    def provider_mode(self) -> str:
        return "fake"

    def model_for(self, agent: str) -> str:
        return "claude-opus-5-5"

    def run_tool_loop(
        self, *, agent, system, user_content, tools, output_model, max_turns=12, max_tokens=16_000
    ):
        from security_agents.llm.types import LLMRequest

        registry = {t.name: t for t in tools}
        self.calls.append({"agent": agent, "system": system, "tools": sorted(registry)})
        script = self.scripts[AgentName(agent)]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
        transcript: list[dict[str, Any]] = []
        for turn in range(max_turns):
            request = LLMRequest(
                agent=AgentName(agent),
                model="fake",
                system=system,
                messages=messages,
                tools=[{"name": n} for n in registry],
                max_tokens=max_tokens,
            )
            step = script[min(turn, len(script) - 1)]
            fake_turn = step(request) if callable(step) else step
            results: list[dict[str, Any]] = []
            assistant: list[dict[str, Any]] = []
            if fake_turn.text:
                assistant.append({"type": "text", "text": fake_turn.text})
            for name, args in fake_turn.tool_calls:
                block_id = f"toolu_{turn}_{name}"
                assistant.append({"type": "tool_use", "id": block_id, "name": name, "input": args})
                if name == SUBMIT or name.startswith("submit_"):
                    messages.append({"role": "assistant", "content": assistant})
                    transcript.extend(assistant)
                    return FakeLoopResult(
                        final=output_model.model_validate(args),
                        turns=turn + 1,
                        usage=FakeUsage(),
                        tool_calls=[],
                        transcript=transcript,
                        model="fake:claude-opus-5-5",
                        provider_mode="fake",
                    )
                tool = registry.get(name)
                if tool is None:
                    # Same behaviour as the real gateway: deny, report back, continue.
                    self.denied.append(name)
                    payload: Any = {"error": ErrorCode.TOOL_NOT_ALLOWED.value, "tool": name}
                else:
                    payload = tool.handler(tool.input_model.model_validate(args))
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block_id,
                        "content": json.dumps(payload, default=str),
                    }
                )
            messages.append({"role": "assistant", "content": assistant})
            transcript.extend(assistant)
            if results:
                messages.append({"role": "user", "content": results})
        raise RuntimeError(f"{agent} did not submit within {max_turns} turns")


@dataclass
class FakeHost:
    """Implements the documented subset of `backend.host.AssessmentHost`."""

    assessment_id: str = "asm_01HOSTADAPTER"
    project_id: str = "prj_01HOSTADAPTER"
    org_id: str = "org_01HOSTADAPTER"
    repo_id: str = "rep_payments_lite"
    gateway: FakeGateway | None = None
    states: list[str] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)
    finished: list[dict[str, Any]] = field(default_factory=list)
    recorded_findings: list[Any] = field(default_factory=list)
    records: list[tuple[str, Any]] = field(default_factory=list)
    patches: list[Any] = field(default_factory=list)
    verifications: list[Any] = field(default_factory=list)
    approval_requests: list[tuple[str, str]] = field(default_factory=list)
    stored: list[Any] = field(default_factory=list)
    sealed_diffs: list[tuple[str, str]] = field(default_factory=list)
    progress_messages: list[str] = field(default_factory=list)
    checkpoints: dict[str, dict[str, Any]] = field(default_factory=dict)
    consumed_checkpoints: set[str] = field(default_factory=set)
    charged: list[Any] = field(default_factory=list)
    _approvals: list[Any] = field(default_factory=list)
    cancelled: bool = False

    # -- state / events
    def assessment(self) -> Any:
        state = self.states[-1] if self.states else "CREATED"
        return type(
            "A",
            (),
            {
                "id": self.assessment_id,
                "project_id": self.project_id,
                "commit_sha": "b" * 40,
                "snapshot_id": None,
                "reassessment_request_id": None,
                "state": type("St", (), {"value": state})(),
                "budget": type(
                    "B", (), {"max_tokens": 2_000_000, "max_usd": 50.0, "max_wall_clock_s": 900.0}
                )(),
            },
        )()

    def scope(self) -> Any:
        return type(
            "S",
            (),
            {
                "id": "scp_01HOSTADAPTER",
                "project_id": self.project_id,
                "name": "host scope",
                "status": "active",
                "requested_by": "usr_requester",
                "approved_by": "usr_approver",
                "approved_at": datetime.now(UTC),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
                "allowed_check_categories": ["access_control.ownership", "input_validation.bounds"],
                "targets": [
                    type(
                        "T",
                        (),
                        {
                            "target_id": "tgt_payments_lite",
                            "repo_id": "rep_payments_lite",
                            "build_recipe_ref": "build_recipe.json",
                            "exposed_ports": [8000],
                        },
                    )()
                ],
            },
        )()

    def approvals(self) -> list[Any]:
        return list(self._approvals)

    def approve_all(self) -> None:
        for _subject_type, subject_id in self.approval_requests:
            self._approvals.append(
                type(
                    "Ap",
                    (),
                    {
                        "id": "apv_01HOSTADAPTER",
                        "subject_id": subject_id,
                        "status": "approved",
                        "subject_type": "remediation",
                        "decided_by": "usr_human",
                    },
                )()
            )

    def decline_all(self) -> None:
        for _subject_type, subject_id in self.approval_requests:
            self._approvals.append(
                type(
                    "Ap",
                    (),
                    {
                        "id": "apv_01DECLINED",
                        "subject_id": subject_id,
                        "status": "declined",
                        "subject_type": "remediation",
                        "decided_by": "usr_human",
                    },
                )()
            )

    def transition(self, target: Any, message: str = "") -> Any:
        self.states.append(getattr(target, "value", str(target)))
        return self.assessment()

    def progress(self, message: str, *, agent: Any = None, **data: Any) -> None:
        self.progress_messages.append(message)

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise RuntimeError("cancelled")

    # -- llm
    def llm(self) -> Any:
        assert self.gateway is not None
        return self.gateway

    # -- artifacts
    def store_text(self, text: str, *args: Any, **kwargs: Any) -> Any:
        self.stored.append(text)
        return type("Art", (), {"id": f"art_{len(self.stored):08d}", "sha256": "0" * 64})()

    def store_json(self, value: Any, kind: Any = None) -> Any:
        self.stored.append(value)
        return type("Art", (), {"id": f"art_{len(self.stored):08d}", "sha256": "0" * 64})()

    def seal_diff(self, diff: str, expected_sha256: str) -> Any:
        self.sealed_diffs.append((diff, expected_sha256))
        return type("Art", (), {"id": f"sealed_{len(self.sealed_diffs):08d}"})()

    # -- runs
    def start_agent_run(self, agent: Any, step_key: str, **kwargs: Any) -> str:
        run_id = f"run_{len(self.runs):08d}HOST"
        self.runs.append(
            {"id": run_id, "agent": getattr(agent, "value", str(agent)), "step_key": step_key, **kwargs}
        )
        return run_id

    def finish_agent_run(self, run_id: str, status: Any, **kwargs: Any) -> Any:
        self.finished.append({"id": run_id, "status": getattr(status, "value", str(status)), **kwargs})
        return None

    # -- records
    def findings(self) -> list[Any]:
        return list(self.recorded_findings)

    def save_checkpoint(self, namespace: str, payload: dict[str, Any]) -> None:
        self.checkpoints[namespace] = json.loads(json.dumps(payload, default=str))
        self.consumed_checkpoints.discard(namespace)

    def load_checkpoint(self, namespace: str) -> dict[str, Any] | None:
        return self.checkpoints.get(namespace)

    def claim_checkpoint(self, namespace: str) -> dict[str, Any] | None:
        payload = self.checkpoints.pop(namespace, None)
        if payload is None:
            if namespace in self.consumed_checkpoints:
                return None
            raise PlatformError(ErrorCode.CONFLICT_STATE, "no checkpoint to claim")
        self.consumed_checkpoints.add(namespace)
        return payload

    def charge(self, usage: Any) -> None:
        self.charged.append(usage)

    def set_finding_status(
        self, finding_id: str, status: str, *, actor: str, reason: str | None = None
    ) -> Any:
        from shared.base import utcnow

        target = shared_findings.FindingStatus(status)
        for index, finding in enumerate(self.recorded_findings):
            if finding.id != finding_id:
                continue
            if finding.status is target:
                return finding
            change = shared_findings.StatusChange(
                from_status=finding.status,
                to_status=target,
                at=utcnow(),
                actor=actor,
                reason=reason,
            )
            updated = finding.model_copy(
                update={
                    "status": target,
                    "history": [*finding.history, change],
                    "updated_at": utcnow(),
                }
            )
            self.recorded_findings[index] = updated
            return updated
        return None

    def record_finding(self, finding: Any, *, actor: str) -> Any:
        for index, existing in enumerate(self.recorded_findings):
            if existing.id == finding.id:
                self.recorded_findings[index] = finding
                return finding
        self.recorded_findings.append(finding)
        return finding

    def record(self, finding_id: str, record: Any) -> None:
        self.records.append((finding_id, record))

    def record_patch(self, patch: Any) -> Any:
        self.patches.append(patch)
        return patch

    def record_verification(self, result: Any) -> Any:
        run_id = getattr(result, "produced_by_run_id", None)
        if run_id and not any(item.get("id") == run_id for item in self.finished):
            raise AssertionError(f"record_verification before finish_agent_run for {run_id}")
        self.verifications.append(result)
        return result

    def request_approval(self, subject_type: Any, subject_id: str, reason: str) -> Any:
        self.approval_requests.append((getattr(subject_type, "value", str(subject_type)), subject_id))
        return type("Ap", (), {"id": f"apv_{len(self.approval_requests):08d}"})()


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def host_and_orchestrator(tmp_path, monkeypatch):
    from security_agents.sandbox.driver import LocalProcessDriver

    target_root = tmp_path / "snapshot_source"
    # the scripted Patch agent needs the real source to build its fix from
    from security_agents.sandbox.workspace import copy_tree

    copy_tree(PAYMENTS_LITE_DIR / "target", target_root)
    scripts = fake_agents.all_scripts(target_root)
    gateway = FakeGateway(scripts=scripts)
    host = FakeHost(gateway=gateway)
    orchestrator = build_orchestrator(
        target_dir=PAYMENTS_LITE_DIR,
        labels_path=PAYMENTS_LITE_DIR / "labels.yaml",
        workdir=tmp_path / "work",
        driver=LocalProcessDriver(allow=True),
    )
    return host, orchestrator, gateway


def test_fresh_orchestrator_carries_host_ledger(host_and_orchestrator, tmp_path, monkeypatch):
    host, adapter, _ = host_and_orchestrator
    assessment = host.assessment()
    assessment.usage = SimpleNamespace(input_tokens=123, output_tokens=45, usd=0.25, sandbox_seconds=12)
    monkeypatch.setattr(host, "assessment", lambda: assessment)
    workdir = tmp_path / "fresh"
    workdir.mkdir()
    intel = adapter._build_intel(adapter.target_dir, workdir, host)
    orchestrator = adapter._make_orchestrator(host, intel, workdir)
    assert orchestrator.result.usage.input_tokens == 123
    assert orchestrator.result.usage.output_tokens == 45
    assert orchestrator.result.usage.usd == 0.25
    assert orchestrator.result.usage.sandbox_seconds == 12
    assert orchestrator.sink.sandbox_charged == 12
    orchestrator.sink.settle([], 12)
    assert host.charged == []
    orchestrator.sink.settle([], 15)
    assert host.charged[-1].sandbox_seconds == 3


# --------------------------------------------------------------------------- tests


def test_factory_returns_the_documented_shape():
    orchestrator = build_orchestrator()
    assert isinstance(orchestrator, HostOrchestrator)
    assert callable(orchestrator.run)


def test_missing_target_fails_with_a_clear_error(monkeypatch):
    monkeypatch.delenv("SECURITY_AGENTS_TARGET_DIR", raising=False)
    with pytest.raises(PlatformError) as exc:
        build_orchestrator().run(FakeHost(gateway=FakeGateway(scripts={})))
    assert exc.value.code is ErrorCode.SNAPSHOT_NOT_READY  # no bound checkout, no target dir
    assert "SECURITY_AGENTS_TARGET_DIR" in exc.value.message


def test_runs_the_pre_approval_phases_and_requests_approval(host_and_orchestrator):
    host, orchestrator, gateway = host_and_orchestrator
    orchestrator.run(host)

    assert host.states[:7] == [
        "SCOPE_CHECK",
        "INDEXING",
        "FUNCTIONAL_REVIEW",
        "RECON",
        "VALIDATION",
        "ANALYSIS",
        "DIAGNOSIS",
    ]
    assert host.states[7] == "AWAITING_APPROVAL"
    assert "COMPLETED" not in host.states, "must stop for the human decision"
    assert len(host.approval_requests) == 1
    subject_type, subject_id = host.approval_requests[0]
    assert subject_type == "remediation"
    assert subject_id.startswith("rmp_")
    assert any("awaiting human" in m for m in host.progress_messages)


def test_every_agent_turn_goes_through_the_host_gateway(host_and_orchestrator):
    host, orchestrator, gateway = host_and_orchestrator
    orchestrator.run(host)
    agents = [c["agent"] for c in gateway.calls]
    assert agents[:2] == ["scout", "probe"] and sorted(agents[2:]) == ["diagnose", "trace"], agents
    # each role sends its own system instruction
    assert len({c["system"] for c in gateway.calls}) == 4
    # and its own tool list, built from the registry allowlist
    assert (
        "checks__run_template" in dict(zip(agents, [c["tools"] for c in gateway.calls], strict=True))["probe"]
    )
    assert (
        "checks__run_template"
        not in dict(zip(agents, [c["tools"] for c in gateway.calls], strict=True))["scout"]
    )
    assert gateway.denied == [], "no scripted call should hit an ungranted tool here"


def test_agent_runs_are_persisted_through_the_host(host_and_orchestrator):
    host, orchestrator, gateway = host_and_orchestrator
    orchestrator.run(host)
    order = [r["agent"] for r in host.runs]
    assert order[:2] == ["scout", "probe"] and sorted(order[2:]) == ["diagnose", "trace"], order
    assert all(r["provider_mode"] == "fake" for r in host.runs)
    assert all(r["model_id"] == "claude-opus-5-5" for r in host.runs)
    assert all(r["prompt_version"] == ALL_SPECS[AgentName(r["agent"])].version for r in host.runs)
    assert [f["status"] for f in host.finished] == ["succeeded"] * 4
    assert all("transcript" in f for f in host.finished)


def test_findings_and_records_are_mirrored_as_canonical_models(host_and_orchestrator):
    host, orchestrator, gateway = host_and_orchestrator
    orchestrator.run(host)
    assert len(host.recorded_findings) == 1
    finding = host.recorded_findings[0]
    assert isinstance(finding, shared_findings.Finding)
    assert finding.kind is shared_findings.FindingKind.security
    assert finding.assessment_id == host.assessment_id
    kinds = [type(record).__name__ for _fid, record in host.records]
    assert "Hypothesis" in kinds
    assert "ValidationResult" in kinds
    assert "AttackPath" in kinds
    assert "RootCause" in kinds
    assert "RemediationPlan" in kinds
    assert finding.status is shared_findings.FindingStatus.diagnosed
    path = [(h.from_status, h.to_status) for h in finding.history]
    assert path == [
        (shared_findings.FindingStatus.hypothesis, shared_findings.FindingStatus.validated),
        (shared_findings.FindingStatus.validated, shared_findings.FindingStatus.diagnosed),
    ]


def test_resume_after_approval_completes_remediation_and_hardening(host_and_orchestrator):
    host, orchestrator, gateway = host_and_orchestrator
    orchestrator.run(host)
    host.approve_all()
    orchestrator.run(host)

    assert host.states[-4:] == ["REMEDIATION", "HARDENING", "REPORTING", "COMPLETED"]
    from .test_e2e_replay import assert_agent_order

    assert_agent_order([r["agent"] for r in host.runs])
    assert len(host.patches) == 1
    patch = host.patches[0]
    assert isinstance(patch, shared_findings.PatchCandidate)
    assert "payments_lite/app.py" in patch.files_changed
    assert patch.diff_artifact_id.startswith("art_")
    assert len(host.sealed_diffs) == 1
    binding = next(
        value for value in host.stored if isinstance(value, dict) and value.get("patch_id") == patch.id
    )
    assert binding["sealed_diff_artifact_id"] == "sealed_00000001"
    assert len(host.verifications) == 1
    verification = host.verifications[0]
    assert isinstance(verification, shared_findings.VerificationResult)
    assert verification.verdict is shared_findings.VerificationVerdict.accepted
    assert verification.produced_by_run_id != patch.produced_by_run_id, "Verify must be its own run"
    assert any("hardened" in m for m in host.progress_messages)
    finding = host.recorded_findings[0]
    assert finding.status is shared_findings.FindingStatus.hardened
    path = [(h.from_status, h.to_status) for h in finding.history]
    assert path == [
        (shared_findings.FindingStatus.hypothesis, shared_findings.FindingStatus.validated),
        (shared_findings.FindingStatus.validated, shared_findings.FindingStatus.diagnosed),
        (shared_findings.FindingStatus.diagnosed, shared_findings.FindingStatus.patch_proposed),
        (shared_findings.FindingStatus.patch_proposed, shared_findings.FindingStatus.verified),
        (shared_findings.FindingStatus.verified, shared_findings.FindingStatus.hardened),
    ]
    assert "security_agents.approval.v1" in host.consumed_checkpoints
    assert host.checkpoints.get("security_agents.approval.v1") is None
    assert host.charged, "sandbox seconds must reach host.charge"


def test_resume_after_losing_in_memory_state(host_and_orchestrator, tmp_path):
    """A new HostOrchestrator (worker restart) must resume from the durable checkpoint."""
    host, first, _ = host_and_orchestrator
    first.run(host)
    assert host.states[-1] == "AWAITING_APPROVAL"
    assert "security_agents.approval.v1" in host.checkpoints
    assert host.checkpoints["security_agents.approval.v1"]["baseline_pytest_audit"]["complete"] is True
    host.approve_all()
    replacement = build_orchestrator(
        target_dir=first.target_dir,
        labels_path=first.labels_path,
        workdir=tmp_path / "fresh-worker",
        driver=first.driver,
    )
    assert replacement._resumable == {}
    replacement.run(host)
    assert host.states[-1] == "COMPLETED"
    assert [run["agent"] for run in host.runs].count("scout") == 1
    assert len(host.patches) == 1
    assert host.recorded_findings[0].status is shared_findings.FindingStatus.hardened


def test_resume_refuses_changed_snapshot(host_and_orchestrator, tmp_path):
    host, first, _ = host_and_orchestrator
    first.run(host)
    host.approve_all()
    host.checkpoints["security_agents.approval.v1"]["commit_sha"] = "0" * 40
    replacement = build_orchestrator(
        target_dir=first.target_dir,
        labels_path=first.labels_path,
        workdir=tmp_path / "other",
        driver=first.driver,
    )
    with pytest.raises(PlatformError) as exc:
        replacement.run(host)
    assert exc.value.code is ErrorCode.SNAPSHOT_NOT_READY
    assert not host.patches


def test_resume_waits_when_another_remediation_is_still_pending(host_and_orchestrator):
    """A security approval must not skip a still-pending functional remediation."""
    from shared.scope import ApprovalStatus, ApprovalSubject

    host, orchestrator, _ = host_and_orchestrator
    orchestrator.run(host)
    host.approve_all()
    host._approvals.append(
        type(
            "Ap",
            (),
            {
                "id": "apv_func_pending",
                "subject_id": "fnd_functional",
                "subject_type": ApprovalSubject.remediation,
                "status": ApprovalStatus.pending,
                "decided_by": None,
            },
        )()
    )
    orchestrator.run(host)
    assert host.states[-1] == "AWAITING_APPROVAL"
    assert not host.patches


def test_declined_approval_reports_without_patching(host_and_orchestrator):
    host, orchestrator, gateway = host_and_orchestrator
    orchestrator.run(host)
    host.decline_all()
    orchestrator.run(host)
    assert host.states[-2:] == ["REPORTING", "COMPLETED"]
    assert not host.patches
    assert not any(r["agent"] == "patch" for r in host.runs)


def test_diff_is_stored_as_an_artifact_not_inline(host_and_orchestrator):
    host, orchestrator, gateway = host_and_orchestrator
    orchestrator.run(host)
    host.approve_all()
    orchestrator.run(host)
    diffs = [s for s in host.stored if isinstance(s, str) and s.startswith("diff --git")]
    assert diffs, "the candidate diff must be stored as an artifact"
    assert "require_owner(p.customer_id, customer)" in diffs[0]


def test_tool_denial_still_enforced_in_host_mode(tmp_path):
    """A model asking for an ungranted tool is refused here, not by the host."""
    from security_agents.llm import FakeTurn
    from security_agents.sandbox.driver import LocalProcessDriver
    from security_agents.sandbox.workspace import copy_tree

    target_root = tmp_path / "src"
    copy_tree(PAYMENTS_LITE_DIR / "target", target_root)
    scripts = fake_agents.all_scripts(target_root)
    # make Scout try a tool it does not have before submitting
    original = scripts[AgentName.SCOUT]
    scripts[AgentName.SCOUT] = [
        FakeTurn(tool_calls=[("worktree__write_file", {"path": "payments_lite/app.py", "content": "x"})]),
        *original,
    ]
    gateway = FakeGateway(scripts=scripts)
    host = FakeHost(gateway=gateway)
    build_orchestrator(
        target_dir=PAYMENTS_LITE_DIR,
        labels_path=PAYMENTS_LITE_DIR / "labels.yaml",
        workdir=tmp_path / "work",
        driver=LocalProcessDriver(allow=True),
    ).run(host)
    assert "worktree__write_file" in gateway.denied
    assert (PAYMENTS_LITE_DIR / "target" / "payments_lite" / "app.py").read_text().startswith('"""HTTP API')
    # the step still completed: a refusal is data for the model
    assert any(r["agent"] == "scout" for r in host.runs)
    assert next(f["status"] for f in host.finished) == "succeeded"


def test_pauses_for_pending_functional_when_scout_finds_nothing(host_and_orchestrator):
    """Srihith design (b): AWAITING_APPROVAL even when there are no security hypotheses."""
    from shared.scope import ApprovalStatus, ApprovalSubject

    from security_agents.llm import FakeTurn

    host, orchestrator, gateway = host_and_orchestrator
    gateway.scripts[AgentName.SCOUT] = [
        FakeTurn(
            tool_calls=[
                (
                    SUBMIT,
                    {
                        "hypotheses": [],
                        "coverage": {
                            "entry_points_reviewed": [],
                            "looks_correct": [],
                            "skipped_with_reason": [],
                        },
                        "suspicious_instructions": [],
                    },
                )
            ]
        )
    ]
    host._approvals.append(
        type(
            "Ap",
            (),
            {
                "id": "apv_functional",
                "subject_id": "fnd_functional",
                "subject_type": ApprovalSubject.remediation,
                "status": ApprovalStatus.pending,
                "decided_by": None,
            },
        )()
    )
    orchestrator.run(host)
    assert host.states[-1] == "AWAITING_APPROVAL"
    assert "COMPLETED" not in host.states
    assert "security_agents.approval.v1" in host.checkpoints


def test_empty_scout_completes_when_no_external_approval_is_pending(host_and_orchestrator):
    from security_agents.llm import FakeTurn

    host, orchestrator, gateway = host_and_orchestrator
    gateway.scripts[AgentName.SCOUT] = [
        FakeTurn(
            tool_calls=[
                (
                    SUBMIT,
                    {
                        "hypotheses": [],
                        "coverage": {
                            "entry_points_reviewed": [],
                            "looks_correct": [],
                            "skipped_with_reason": [],
                        },
                        "suspicious_instructions": [],
                    },
                )
            ]
        )
    ]
    orchestrator.run(host)
    assert host.states[-1] == "COMPLETED"
    assert not host.patches


def test_recipe_prefix_is_stripped_for_functional_verify(tmp_path):
    from security_agents.host_adapter import (
        _diff_for_snapshot,
        _recipe_strip_prefix,
        _snapshot_rel,
    )

    root = tmp_path / "snap"
    (root / "payments_lite").mkdir(parents=True)
    (root / "payments_lite" / "app.py").write_text("x")
    (root / "tests").mkdir()
    paths = ["target/payments_lite/app.py", "target/tests/generated/t.py"]
    prefix = _recipe_strip_prefix(root, paths)
    assert prefix == "target"
    assert _snapshot_rel("target/tests/generated/t.py", prefix) == "tests/generated/t.py"
    diff = (
        "diff --git a/target/payments_lite/app.py b/target/payments_lite/app.py\n"
        "--- a/target/payments_lite/app.py\n"
        "+++ b/target/payments_lite/app.py\n"
    )
    rewritten = _diff_for_snapshot(diff, prefix)
    assert "a/payments_lite/app.py" in rewritten
    assert "a/target/" not in rewritten

    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "app.py").write_text("x")
    (nested / "tests").mkdir()
    (nested / "build_recipe.json").write_text('{"target_dir": "services/api"}')
    nested_paths = ["services/api/app.py", "services/api/tests/generated/t.py"]
    nested_prefix = _recipe_strip_prefix(nested, nested_paths)
    assert nested_prefix == "services/api"
    assert _snapshot_rel("services/api/tests/generated/t.py", nested_prefix) == "tests/generated/t.py"
    nested_diff = (
        "diff --git a/services/api/app.py b/services/api/app.py\n"
        "--- a/services/api/app.py\n"
        "+++ b/services/api/app.py\n"
    )
    assert "a/app.py" in _diff_for_snapshot(nested_diff, nested_prefix)
    assert "a/services/" not in _diff_for_snapshot(nested_diff, nested_prefix)


def test_pinned_snapshot_escaping_target_dir_is_schema_invalid(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from security_agents.host_adapter import _pinned_snapshot
    from security_agents.sandbox.workspace import SnapshotHandle

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "build_recipe.json").write_text('{"target_dir": "../outside"}', encoding="utf-8")
    monkeypatch.setattr(
        "security_agents.host_adapter._bindings",
        lambda: [{"repo_id": "rep_x", "local_path": str(tmp_path), "subdir": "."}],
    )
    monkeypatch.setattr(
        SnapshotHandle,
        "from_git",
        staticmethod(lambda *args, **kwargs: SimpleNamespace(root=checkout, commit_sha="a" * 40)),
    )
    host = SimpleNamespace(
        repo_id="rep_x",
        assessment=SimpleNamespace(commit_sha="a" * 40, snapshot_id=None),
    )
    with pytest.raises(PlatformError) as exc:
        _pinned_snapshot(host, tmp_path / "work")
    assert exc.value.code is ErrorCode.SCHEMA_INVALID


def test_host_state_names_cover_every_state():
    from security_agents.host_adapter import HOST_STATE_NAMES

    for state in AssessmentState:
        if state is AssessmentState.CREATED:
            continue  # the host creates the assessment itself
        assert state in HOST_STATE_NAMES, state
        member = shared_assessment.AssessmentState(HOST_STATE_NAMES[state])
        assert member.name == HOST_STATE_NAMES[state]


@pytest.mark.parametrize(
    "field,value",
    [
        ("approved_by", None),
        ("approved_at", None),
        ("expires_at", None),
        ("status", "inactive"),
    ],
)
def test_host_scope_adapter_cannot_invent_approval(host_and_orchestrator, field, value):
    from types import SimpleNamespace

    from security_agents.host_adapter import _adapt_scope
    from security_agents.scope_guard import ActionRequest, check

    host, _, _ = host_and_orchestrator
    scope = host.scope()
    setattr(scope, field, value)
    intel = SimpleNamespace(snapshot=SimpleNamespace(repo_id="rep_host"))
    mapped = _adapt_scope(scope, intel)
    assert not check(mapped, ActionRequest(action="assessment_start"), datetime.now(UTC)).allowed


def test_resume_rechecks_revoked_scope_before_any_patch(host_and_orchestrator, monkeypatch):
    host, orchestrator, _ = host_and_orchestrator
    orchestrator.run(host)
    host.approve_all()
    scope = host.scope()
    scope.status = "revoked"
    monkeypatch.setattr(host, "scope", lambda: scope)
    with pytest.raises(PlatformError) as exc:
        orchestrator.run(host)
    assert exc.value.code is ErrorCode.SCOPE_DENIED
    assert not host.patches


def _empty_scout(gateway) -> None:
    from security_agents.llm import FakeTurn

    gateway.scripts[AgentName.SCOUT] = [
        FakeTurn(
            tool_calls=[
                (
                    SUBMIT,
                    {
                        "hypotheses": [],
                        "coverage": {
                            "entry_points_reviewed": [],
                            "looks_correct": [],
                            "skipped_with_reason": [],
                        },
                        "suspicious_instructions": [],
                    },
                )
            ]
        )
    ]


def test_resume_reapplies_host_repo_filter(host_and_orchestrator, monkeypatch):
    from security_agents.host_adapter import HostOrchestrator

    host, orchestrator, _ = host_and_orchestrator
    orchestrator.run(host)
    host.approve_all()
    scope = host.scope()
    extra = type(
        "T",
        (),
        {
            "target_id": "tgt_other",
            "repo_id": "rep_other",
            "build_recipe_ref": "build_recipe.json",
            "exposed_ports": [],
        },
    )()
    scope.targets = [*list(scope.targets), extra]
    monkeypatch.setattr(host, "scope", lambda: scope)
    seen: dict[str, list[str]] = {}
    original = HostOrchestrator._resume

    def wrapped(self, orch, h):
        original(self, orch, h)
        seen["repos"] = [target.repo_id for target in orch.config.scope.targets]

    monkeypatch.setattr(HostOrchestrator, "_resume", wrapped)
    orchestrator.run(host)
    assert seen["repos"] == [host.repo_id]


def test_resume_denies_when_no_scope_target_matches_repo(host_and_orchestrator, monkeypatch):
    host, orchestrator, _ = host_and_orchestrator
    orchestrator.run(host)
    host.approve_all()
    scope = host.scope()
    scope.targets[0].repo_id = "rep_other"
    monkeypatch.setattr(host, "scope", lambda: scope)
    with pytest.raises(PlatformError) as exc:
        orchestrator.run(host)
    assert exc.value.code is ErrorCode.SCOPE_DENIED
    assert not host.patches


def test_second_worker_noops_after_checkpoint_is_claimed(host_and_orchestrator, tmp_path):
    host, first, _ = host_and_orchestrator
    first.run(host)
    host.approve_all()
    assert host.claim_checkpoint("security_agents.approval.v1") is not None
    first._resumable.clear()
    replacement = build_orchestrator(
        target_dir=first.target_dir,
        labels_path=first.labels_path,
        workdir=tmp_path / "other-worker",
        driver=first.driver,
    )
    replacement.run(host)
    assert not host.patches
    assert host.states[-1] == "AWAITING_APPROVAL"
    assert any("already claimed" in message for message in host.progress_messages)


def test_queued_resume_noops_after_in_process_resume_moved_on(host_and_orchestrator, tmp_path):
    """Prod race: the approving worker resumed in-process and reached REMEDIATION before the
    queued resume job ran; that job must leave the run alone, not fail it."""
    from security_agents.host_adapter import CLAIMED_ELSEWHERE

    host, first, _ = host_and_orchestrator
    first.run(host)
    host.approve_all()
    assert host.claim_checkpoint("security_agents.approval.v1") is not None
    host.states.append("REMEDIATION")
    first._resumable.clear()
    replacement = build_orchestrator(
        target_dir=first.target_dir,
        labels_path=first.labels_path,
        workdir=tmp_path / "other-worker",
        driver=first.driver,
    )
    assert replacement.run(host) == CLAIMED_ELSEWHERE
    assert not host.patches
    assert host.states[-1] == "REMEDIATION"


def test_empty_scout_pauses_when_functional_is_already_approved(host_and_orchestrator):
    """Codex r4302039275: approved functional work is outstanding, not only pending decisions."""
    from shared.scope import ApprovalStatus, ApprovalSubject

    host, orchestrator, gateway = host_and_orchestrator
    _empty_scout(gateway)
    finding_id = "fnd_01FUNCTIONALAPPROVED"
    host.recorded_findings.append(
        type("F", (), {"id": finding_id, "kind": "functional", "status": "validated"})()
    )
    host._approvals.append(
        type(
            "Ap",
            (),
            {
                "id": "apv_func_approved",
                "subject_id": finding_id,
                "subject_type": ApprovalSubject.remediation,
                "status": ApprovalStatus.approved,
                "decided_by": "usr_human",
            },
        )()
    )
    orchestrator.run(host)
    assert host.states[-1] == "AWAITING_APPROVAL"
    assert "COMPLETED" not in host.states
    assert "security_agents.approval.v1" in host.checkpoints


def test_issued_rules_of_engagement_are_handed_to_the_host(host_and_orchestrator):
    host, orchestrator, gateway = host_and_orchestrator
    recorded: list[dict] = []
    host.record_roe = recorded.append
    orchestrator.run(host)
    assert recorded, "the worker must hand the issued RoE to host.record_roe"
    doc = recorded[-1]
    assert doc["id"].startswith("roe_") and doc["digest"]
    assert doc["permitted_check_templates"]
    assert doc["alignment"].startswith("aligned with NIST SP 800-115")


def test_hosts_without_record_roe_still_run(host_and_orchestrator):
    host, orchestrator, gateway = host_and_orchestrator
    assert not hasattr(host, "record_roe")
    orchestrator.run(host)
    order = [r["agent"] for r in host.runs]
    assert order[:2] == ["scout", "probe"] and sorted(order[2:]) == ["diagnose", "trace"], order


def test_hosted_run_stores_a_valid_sarif_log_and_the_rules_of_engagement(host_and_orchestrator):
    import jsonschema

    from security_agents.standards.sarif import schema

    host, orchestrator, gateway = host_and_orchestrator
    orchestrator.run(host)
    texts = [json.loads(t) for t in host.stored if isinstance(t, str) and t.lstrip().startswith("{")]
    sarif_logs = [t for t in texts if t.get("version") == "2.1.0" and "runs" in t]
    roes = [t for t in texts if str(t.get("id", "")).startswith("roe_")]
    assert sarif_logs and roes
    jsonschema.Draft4Validator(schema()).validate(sarif_logs[-1])
    assert sarif_logs[-1]["runs"][0]["properties"]["run_mode"] == "hosted"
    assert any(m.startswith("standards artifacts stored") for m in host.progress_messages)


def test_only_read_and_sandboxed_run_tools_may_run_concurrently(intel, registry):
    from security_agents.agents import patch as patch_agent
    from security_agents.agents import verify as verify_agent
    from security_agents.host_adapter import build_tools
    from security_agents.runtime.loop import StepRecord

    from .conftest import make_ctx

    flags = {}
    for spec in (patch_agent.SPEC, verify_agent.SPEC):
        ctx = make_ctx(spec.name, intel=intel)
        record = StepRecord(
            run_id="run_t", agent=spec.name, spec_version="0", model="m", provider_mode="fake"
        )
        flags.update({t.name: t.parallel_safe for t in build_tools(spec, ctx, registry, record)})
    assert flags["tests__run"] and flags["code__read_span"] and flags["worktree__diff"]
    assert not flags["worktree__edit_file"] and not flags["worktree__write_file"]
    assert not flags["patch__apply_fresh"]
