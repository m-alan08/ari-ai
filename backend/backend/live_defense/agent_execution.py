"""Phase 7C: run authorized investigative agents for the first-responder.

Scout (and Diagnose after Scout) execute the real ``security_agents`` loop against
an owner-attested mapped snapshot. Probe, Patch, Verify, and Trace stay gated:
this module does not widen their privileges. Typed output is persisted as
unverified evidence. LLM prose cannot confirm a threat.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Connection

from backend.artifacts import ArtifactStore
from backend.live_defense import provider_ledger
from backend.settings import get_settings
from shared.assessment import AgentName
from shared.base import utcnow
from shared.errors import ErrorCode as SharedErrorCode
from shared.evidence import ArtifactKind
from shared.live_defense import (
    FIRST_RESPONDER_DEADLINE_S,
    FIRST_RESPONDER_MAX_USD,
    AgentExecutionRecord,
    AgentInvocationStatus,
    ExecutionIntegrity,
    FirstResponderDecision,
    FirstResponderInvocation,
)

log = logging.getLogger("backend.live_defense.agent_execution")

# Test hook. Never set in production code.
GATEWAY_OVERRIDE: Any | None = None

INVESTIGATIVE_LIVE = frozenset({AgentName.scout, AgentName.diagnose})
MIN_PROVIDER_START_S = 1.0
FIXTURE_LABELS_ENV = "SECURITY_AGENTS_LABELS"


class PrerequisiteNotReady(RuntimeError):
    """Diagnose ran before Scout finished. Worker retries this without burning attempts."""


@dataclass(frozen=True)
class ExecutionOutcome:
    status: AgentInvocationStatus
    reason: str
    provider_mode: str
    execution: AgentExecutionRecord
    confirmed: bool = False


def _fake_scripts_root(snapshot_root: Path) -> Path:
    return snapshot_root


def _fake_gateway(snapshot_root: Path) -> Any:
    import security_agents
    from security_agents.llm import FakeLLM, ProviderGateway

    tests_dir = Path(security_agents.__file__).resolve().parents[1]
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))
    from tests.fake_agents import all_scripts

    return ProviderGateway(FakeLLM(all_scripts(_fake_scripts_root(snapshot_root))))


def _sa_error_code(code: Any) -> Any:
    from security_agents.contracts import ErrorCode

    name = getattr(code, "value", str(code or ""))
    mapping = {
        SharedErrorCode.LLM_UNAVAILABLE.value: ErrorCode.LLM_UNAVAILABLE,
        SharedErrorCode.LLM_REFUSAL.value: ErrorCode.LLM_UNAVAILABLE,
        SharedErrorCode.LLM_OUTPUT_TRUNCATED.value: ErrorCode.SCHEMA_INVALID,
        SharedErrorCode.BUDGET_EXHAUSTED.value: ErrorCode.BUDGET_EXHAUSTED,
        SharedErrorCode.CANCELLED.value: ErrorCode.CANCELLED,
        SharedErrorCode.SCHEMA_INVALID.value: ErrorCode.SCHEMA_INVALID,
        SharedErrorCode.SNAPSHOT_NOT_READY.value: ErrorCode.SNAPSHOT_NOT_READY,
    }
    if name in mapping:
        return mapping[name]
    try:
        return ErrorCode(name)
    except ValueError:
        return ErrorCode.LLM_UNAVAILABLE


class _BackendGateway:
    """Adapter so first-responder live calls go through ``backend.llm`` (ADR-0007)."""

    def __init__(self, inner: Any, *, cancelled: Any | None = None) -> None:
        self._inner = inner
        self.mode = str(getattr(inner, "provider_mode", None) or getattr(inner, "mode", "live"))
        self._cancelled = cancelled
        if cancelled is not None:
            inner.check_cancelled = self._raise_if_cancelled

    def _raise_if_cancelled(self) -> None:
        from backend.llm import LLMError

        if self._cancelled and self._cancelled():
            raise LLMError(SharedErrorCode.CANCELLED, "first-responder cancelled or OFF")

    def model_for(self, agent: Any) -> str:
        name = getattr(agent, "value", agent)
        return str(self._inner.model_for(str(name)))

    def complete(self, request: Any) -> Any:
        from backend.llm import LLMError
        from security_agents.contracts import ErrorCode
        from security_agents.llm.types import LLMResponse, LLMUnavailable

        try:
            self._raise_if_cancelled()
        except LLMError as exc:
            raise LLMUnavailable(str(exc), code=_sa_error_code(exc.code)) from exc
        remaining = float(getattr(request, "timeout_s", 0.0) or 0.0)
        if remaining < MIN_PROVIDER_START_S:
            raise LLMUnavailable(
                "DEADLINE_EXCEEDED: not enough time to start a provider request",
                code=ErrorCode.BUDGET_EXHAUSTED,
            )
        deadline = time.monotonic() + remaining
        key = (getattr(request, "metadata", None) or {}).get("idempotency_key")
        try:
            resp = self._inner.create_message(
                agent=getattr(request.agent, "value", str(request.agent)),
                system=request.system,
                messages=request.messages,
                tools=request.tools,
                max_tokens=request.max_tokens,
                timeout_s=remaining,
                deadline_monotonic=deadline,
                idempotency_key=key if isinstance(key, str) and key else None,
            )
        except LLMError as exc:
            raise LLMUnavailable(str(exc), code=_sa_error_code(exc.code)) from exc
        usage = dict(resp.usage or {})
        if self.mode == "live" and not _usage_is_billable(usage):
            raise LLMUnavailable(
                "BUDGET_EXHAUSTED: live provider returned no required usage; refusing silent $0",
                code=ErrorCode.BUDGET_EXHAUSTED,
            )
        try:
            parsed = _llm_usage(usage)
        except (TypeError, ValueError) as exc:
            raise LLMUnavailable(
                "BUDGET_EXHAUSTED: malformed live usage; refusing silent $0",
                code=ErrorCode.BUDGET_EXHAUSTED,
            ) from exc
        return LLMResponse(
            content=list(resp.content),
            stop_reason=str(resp.stop_reason or "end_turn"),
            usage=parsed,
            model=str(resp.model),
            provider_mode=self.mode if self.mode in {"fake", "live", "replay"} else "live",
            response_id=resp.id,
            stop_details=resp.stop_details,
        )

    def cost_usd(self, model: str, usage: Any) -> float:
        from backend.llm import cost_usd

        payload = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            "cache_creation_input_tokens": int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            ),
        }
        return cost_usd(model, payload)


def _usage_is_billable(usage: dict[str, Any]) -> bool:
    return any(
        key in usage and usage[key] is not None
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    )


def _llm_usage(usage: dict[str, Any]) -> Any:
    from security_agents.llm.types import LLMUsage

    return LLMUsage(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
        cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
    )


class BillableGateway:
    """Reserve each provider complete() before send. Does not wrap intel reads."""

    def __init__(
        self,
        inner: Any,
        *,
        invocation_id: str,
        deadline_at: Any | None,
        cancelled: Any | None,
    ) -> None:
        self._inner = inner
        self._invocation_id = invocation_id
        self._deadline_at = deadline_at
        self._cancelled = cancelled
        self.mode = str(getattr(inner, "mode", None) or getattr(inner, "provider_mode", "fake"))
        if cancelled is not None and hasattr(inner, "_cancelled"):
            inner._cancelled = cancelled

    def model_for(self, agent: Any) -> str:
        return str(self._inner.model_for(agent))

    def cost_usd(self, model: str, usage: Any) -> float:
        if hasattr(self._inner, "cost_usd"):
            return float(self._inner.cost_usd(model, usage))
        from backend.llm import cost_usd as priced

        payload = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            "cache_creation_input_tokens": int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            ),
        }
        return priced(model, payload)

    def _raise_if_cancelled(self) -> None:
        from security_agents.contracts import ErrorCode
        from security_agents.llm.types import LLMUnavailable

        if self._cancelled and self._cancelled():
            raise LLMUnavailable("first-responder cancelled or OFF", code=ErrorCode.CANCELLED)

    def _raise_if_deadline(self) -> None:
        from security_agents.contracts import ErrorCode
        from security_agents.llm.types import LLMUnavailable

        remain = remaining_deadline_s(self._deadline_at)
        if remain is not None and remain < MIN_PROVIDER_START_S:
            raise LLMUnavailable(
                "DEADLINE_EXCEEDED: overall investigation deadline refuses another provider call",
                code=ErrorCode.BUDGET_EXHAUSTED,
            )

    def complete(self, request: Any) -> Any:
        from dataclasses import replace

        from security_agents.contracts import ErrorCode
        from security_agents.llm.types import LLMUnavailable

        self._raise_if_cancelled()
        self._raise_if_deadline()
        model_id = str(getattr(request, "model", None) or self.model_for(request.agent))
        try:
            attempt = provider_ledger.reserve_attempt(self._invocation_id, model_id=model_id)
        except RuntimeError as exc:
            raise LLMUnavailable(str(exc), code=ErrorCode.LLM_UNAVAILABLE) from exc
        wrapped = request
        try:
            meta = dict(getattr(request, "metadata", None) or {})
            meta["idempotency_key"] = attempt.idempotency_key
            wrapped = replace(request, metadata=meta)
        except (TypeError, ValueError):
            wrapped = request
        try:
            resp = self._inner.complete(wrapped)
        except LLMUnavailable as exc:
            code = str(getattr(exc.code, "value", exc.code) or "")
            if code in {"CANCELLED", "KILL_SWITCH"}:
                provider_ledger.mark_attempt_cancelled(self._invocation_id, attempt.id)
            else:
                provider_ledger.mark_attempt_uncertain(
                    self._invocation_id, attempt.id, error_code=code or "LLM_UNAVAILABLE"
                )
            raise
        except BaseException:
            provider_ledger.mark_attempt_uncertain(
                self._invocation_id, attempt.id, error_code="UNCERTAIN_PROVIDER_OUTCOME"
            )
            raise
        usage = getattr(resp, "usage", None)
        cost = None
        try:
            if usage is not None:
                cost = float(self.cost_usd(str(getattr(resp, "model", None) or model_id), usage))
        except (TypeError, ValueError):
            cost = None
        # Known response: settle before any cancel/OFF check so costs are not lost.
        # The persist path refuses to upgrade a cancelled invocation to executed.
        provider_ledger.settle_attempt(
            self._invocation_id,
            attempt.id,
            response_id=getattr(resp, "response_id", None),
            model_id=str(getattr(resp, "model", None) or model_id),
            usage=usage,
            cost_usd=cost,
        )
        return resp


def remaining_deadline_s(deadline_at: Any | None) -> float | None:
    if deadline_at is None:
        return None
    instant: datetime
    if isinstance(deadline_at, str):
        instant = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
    elif isinstance(deadline_at, datetime):
        instant = deadline_at
    else:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return (instant - utcnow()).total_seconds()


def build_gateway(
    snapshot_root: Path,
    *,
    cancelled: Any | None = None,
    invocation_id: str | None = None,
    deadline_at: Any | None = None,
) -> Any:
    if GATEWAY_OVERRIDE is not None:
        inner = GATEWAY_OVERRIDE()
        if cancelled is not None and hasattr(inner, "_cancelled"):
            inner._cancelled = cancelled
    else:
        from backend import llm

        if llm.TRANSPORT_OVERRIDE is not None:
            inner = _fake_gateway(snapshot_root)
        elif llm.llm_mode() == "live":
            inner = _BackendGateway(llm.build_gateway(), cancelled=cancelled)
        else:
            raise RuntimeError("LLM_UNAVAILABLE: no first-responder provider (no key, not fake)")
    if invocation_id is None:
        return inner
    return BillableGateway(
        inner,
        invocation_id=invocation_id,
        deadline_at=deadline_at,
        cancelled=cancelled,
    )


def _binding_for(repo_id: str) -> dict[str, Any] | None:
    path = get_settings().repository_bindings_file
    if path is None or not Path(path).is_file():
        return None
    entries = json.loads(Path(path).read_text(encoding="utf-8") or "[]")
    return next((item for item in entries if item.get("repo_id") == repo_id), None)


def pin_snapshot(*, repo_id: str, commit_sha: str, snapshot_id: str, dest: Path) -> Any:
    """Materialize the mapped commit the same way host mode does."""
    from security_agents.sandbox.workspace import SnapshotHandle, copy_tree

    binding = _binding_for(repo_id)
    if binding is None:
        raise RuntimeError(
            "SNAPSHOT_NOT_READY: repository is not bound for first-responder execution"
        )
    local = Path(binding["local_path"]).expanduser().resolve()
    subdir = str(binding.get("subdir") or ".")
    raw = SnapshotHandle.from_git(
        local,
        commit_sha,
        subdir,
        dest / "checkout",
        snapshot_id=snapshot_id,
        repo_id=repo_id,
    )
    recipe = raw.root / "build_recipe.json"
    target_dir = None
    if recipe.is_file():
        target_dir = json.loads(recipe.read_text(encoding="utf-8")).get("target_dir")
    if not target_dir:
        return raw
    if str(target_dir).startswith("/") or ".." in Path(str(target_dir)).parts:
        raise RuntimeError(
            "SCHEMA_INVALID: build_recipe.json target_dir must stay inside the checkout"
        )
    root = dest / "snapshot"
    copy_tree(raw.root / str(target_dir), root)
    shutil.copy2(recipe, root / "build_recipe.json")
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    return SnapshotHandle(
        snapshot_id=snapshot_id,
        repo_id=repo_id,
        commit_sha=raw.commit_sha,
        root=root,
        pinned_by="git",
    )


def _explicit_fixture_labels() -> Path | None:
    env = os.environ.get(FIXTURE_LABELS_ENV)
    if not env:
        return None
    path = Path(env)
    return path if path.is_file() else None


def _recipe_strip_prefix(snapshot: Any) -> str | None:
    """Match host/CLI: labels.yaml paths keep target_dir/; the snapshot root does not."""
    recipe_path = snapshot.root / "build_recipe.json"
    if not recipe_path.is_file():
        return None
    try:
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    target_dir = recipe.get("target_dir")
    if not target_dir:
        return None
    return f"{target_dir}/"


def _assert_authorized_tree(
    snapshot: Any, *, repo_id: str, commit_sha: str, snapshot_id: str
) -> None:
    if (
        str(snapshot.repo_id) != repo_id
        or str(snapshot.commit_sha) != commit_sha
        or str(snapshot.snapshot_id) != snapshot_id
    ):
        raise RuntimeError("SNAPSHOT_NOT_READY: pinned tree does not match the authorized snapshot")


def load_intel(
    snapshot: Any,
    *,
    project_id: str,
    repo_id: str,
    commit_sha: str,
    snapshot_id: str,
) -> Any:
    """Canonical intelligence for indexed repos. LocalIntel is fixture/test only."""
    from security_agents.intel import LocalIntel, load_expectations

    _assert_authorized_tree(
        snapshot, repo_id=repo_id, commit_sha=commit_sha, snapshot_id=snapshot_id
    )
    labels = _explicit_fixture_labels()
    if labels is not None:
        expectations = load_expectations(
            labels,
            snapshot_id=snapshot.snapshot_id,
            commit_sha=snapshot.commit_sha,
            strip_prefix=_recipe_strip_prefix(snapshot),
        )
        return LocalIntel(snapshot, expectations=expectations)
    return _canonical_intel(
        snapshot,
        project_id=project_id,
        repo_id=repo_id,
        commit_sha=commit_sha,
        snapshot_id=snapshot_id,
    )


def _canonical_intel(
    snapshot: Any,
    *,
    project_id: str,
    repo_id: str,
    commit_sha: str,
    snapshot_id: str,
) -> Any:
    from backend.intelligence import repository_service, snapshot_ref
    from security_agents.contracts import PlatformError
    from security_agents.intel.code_intelligence_adapter import CodeIntelligenceAdapter

    try:
        ref = snapshot_ref(snapshot_id)
    except (KeyError, ValueError) as exc:
        raise RuntimeError("SNAPSHOT_NOT_READY: authoritative snapshot context is missing") from exc
    if (
        ref.project_id != project_id
        or ref.repo_id != repo_id
        or ref.commit_sha != commit_sha
        or ref.snapshot_id != snapshot_id
    ):
        raise RuntimeError(
            "SNAPSHOT_NOT_READY: snapshot is not bound to the authorized project/repo/commit"
        )
    binding = _binding_for(repo_id) or {}
    prefix = str(binding.get("subdir") or "").strip("/")
    if prefix == ".":
        prefix = ""
    recipe_prefix = _recipe_strip_prefix(snapshot)
    if recipe_prefix:
        prefix = "/".join(p for p in (prefix, recipe_prefix.strip("/")) if p)
    try:
        service = repository_service(project_id, repo_id, include_prefix=prefix)
        adapter = CodeIntelligenceAdapter(
            service=service, ref=ref, workspace=snapshot.root, path_prefix=prefix
        )
        adapter.verify_workspace()
        adapter.symbols()
    except PlatformError as exc:
        raise RuntimeError(f"{exc.code.value}: {exc.message}") from exc
    except Exception as exc:
        raise RuntimeError(
            "SNAPSHOT_NOT_READY: canonical intelligence is missing for this snapshot"
        ) from exc
    if adapter.snapshot.repo_id != repo_id or adapter.snapshot.commit_sha != commit_sha:
        raise RuntimeError("SNAPSHOT_NOT_READY: intelligence adapter drifted from the mapping")
    return adapter


def _sa_name(agent: AgentName) -> Any:
    from security_agents.contracts import AgentName as SAName

    return SAName(agent.value)


def _tool_context(
    agent: AgentName, intel: Any, *, assessment_id: str, run_id: str, cancelled: Any
) -> Any:
    from security_agents.intel import FindingsHistory
    from security_agents.tools import ToolContext

    return ToolContext(
        assessment_id=assessment_id,
        agent=_sa_name(agent),
        run_id=run_id,
        step_key=f"first-responder:{agent.value}",
        read_root=intel.snapshot.root,
        services={"intel": intel, "history": FindingsHistory()},
        kill_switch=cancelled,
    )


def _persist_json(conn: Connection, project_id: str, value: Any, kind: ArtifactKind) -> str:
    return ArtifactStore().put_json(conn, value, kind=kind, project_id=project_id).id


def _record(
    *,
    provider_mode: str,
    model_id: str | None,
    cost_usd: float | None,
    latency_ms: int,
    llm_turns: int | None,
    tool_call_count: int | None,
    started_at: Any,
    ended_at: Any,
    transcript_id: str | None,
    output_id: str | None,
    error_code: str | None,
    typed_output: bool,
    hypothesis_count: int | None,
    integrity: ExecutionIntegrity,
) -> AgentExecutionRecord:
    mode = provider_mode if provider_mode in {"fake", "live", "none"} else "none"
    return AgentExecutionRecord(
        provider_mode=mode,
        model_id=model_id,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        llm_turns=llm_turns,
        tool_call_count=tool_call_count,
        started_at=started_at,
        ended_at=ended_at,
        transcript_artifact_id=transcript_id,
        output_artifact_id=output_id,
        error_code=error_code,
        typed_output=typed_output,
        hypothesis_count=hypothesis_count,
        execution_integrity=integrity,
    )


def _wall_clock_s(step_timeout_s: float, remain: float | None) -> float:
    wall = float(min(FIRST_RESPONDER_DEADLINE_S, step_timeout_s))
    if remain is not None:
        return max(0.0, min(wall, remain))
    return wall


def _with_ledger(invocation_id: str, execution: AgentExecutionRecord) -> AgentExecutionRecord:
    import sqlalchemy as sa

    from backend.db import first_responder_invocations
    from backend.db import transaction as db_transaction

    with db_transaction() as conn:
        doc = conn.execute(
            sa.select(first_responder_invocations.c.doc).where(
                first_responder_invocations.c.id == invocation_id
            )
        ).scalar()
    if not doc:
        return execution
    current = FirstResponderInvocation.model_validate(doc)
    if current.execution is None:
        return execution
    return execution.model_copy(
        update={
            "provider_attempts": current.execution.provider_attempts,
            "uncertain_provider_outcome": current.execution.uncertain_provider_outcome,
        }
    )


def _normalize_provider_mode(mode: str | None, *, processed: bool) -> str:
    if mode in {"fake", "live"}:
        return mode
    return "none" if not processed else "live"


def _scout_input(decision: FirstResponderDecision, intel: Any) -> Any:
    from security_agents.agents import scout as scout_agent
    from security_agents.checks.library import default_library

    library = default_library()
    categories = sorted(library.categories())
    return scout_agent.ScoutInput(
        assessment_id=decision.id,
        snapshot_id=intel.snapshot.snapshot_id,
        commit_sha=intel.snapshot.commit_sha,
        expectation_ids=[e.id for e in intel.expectations()],
        max_hypotheses=5,
        available_check_categories=categories,
    )


def _diagnose_input(decision: FirstResponderDecision, scout_output: dict[str, Any]) -> Any:
    from security_agents.agents import diagnose as diagnose_agent

    hypotheses = list(scout_output.get("hypotheses") or [])
    if not hypotheses:
        raise RuntimeError("Diagnose requires Scout hypotheses; none were persisted")
    top = hypotheses[0]
    cited = top.get("cited_spans") or []
    paths = [str(item.get("path")) for item in cited if item.get("path")]
    return diagnose_agent.DiagnoseInput(
        assessment_id=decision.id,
        finding_id=str(top.get("id") or "fnd_first_responder_unverified"),
        weakness_class=str(top.get("weakness_class") or "CWE-639"),
        hypothesis_title=str(top.get("title") or "Scout hypothesis (unverified)"),
        severity_level="unverified",
        impact_summary=(
            "First-responder Diagnose input is a Scout hypothesis, not a confirmed finding. "
            "Do not treat this as independent verification."
        ),
        affected_paths=paths[:8],
        violated_expectation_ids=list(top.get("expectation_ids") or [])[:6],
    )


def _persist_loop_artifacts(
    *,
    project_id: str,
    agent: AgentName,
    outcome: Any,
    scout_data: Any,
    integrity: ExecutionIntegrity,
    hypotheses: list[dict[str, Any]],
) -> tuple[str, str]:
    from backend.db import transaction as db_transaction

    with db_transaction() as persist:
        transcript_id = _persist_json(
            persist,
            project_id,
            outcome.record.to_json(),
            ArtifactKind.llm_transcript,
        )
    payload = {
        "agent": agent.value,
        "ok": outcome.ok,
        "typed_output": outcome.output is not None,
        "output": outcome.output.model_dump(mode="json") if outcome.output is not None else None,
        "hypotheses": hypotheses,
        "execution_integrity": integrity.value,
        "llm_prose_is_not_confirmation": True,
        "predicted_probability": None,
        "error_code": outcome.run.error_code.value if outcome.run.error_code else None,
        "error_detail": outcome.run.error_detail,
        "provider_mode": outcome.record.provider_mode,
        "model": outcome.record.model,
        "usage": outcome.record.usage.model_dump(mode="json") if outcome.record.usage else None,
    }
    with db_transaction() as persist:
        output_id = _persist_json(persist, project_id, payload, ArtifactKind.tool_output)
    return transcript_id, output_id


def run_investigative(
    *,
    agent: AgentName,
    decision: FirstResponderDecision,
    invocation: FirstResponderInvocation,
    repo_id: str,
    commit_sha: str,
    snapshot_id: str,
    scout_output: dict[str, Any] | None,
    cancelled: Any,
) -> ExecutionOutcome:
    """Execute Scout or Diagnose. Never sets confirmation=confirmed."""
    started = utcnow()
    t0 = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix=f"fr-{agent.value}-"))
    provider_mode = "none"
    processed = False
    try:
        snapshot = pin_snapshot(
            repo_id=repo_id, commit_sha=commit_sha, snapshot_id=snapshot_id, dest=work
        )
        intel = load_intel(
            snapshot,
            project_id=decision.project_id,
            repo_id=repo_id,
            commit_sha=commit_sha,
            snapshot_id=snapshot_id,
        )
        deadline_at = invocation.deadline_at or decision.deadline_at
        remain = remaining_deadline_s(deadline_at)
        if remain is not None and remain < MIN_PROVIDER_START_S:
            raise RuntimeError(
                "DEADLINE_EXCEEDED: overall investigation deadline refuses another provider call"
            )
        gateway = build_gateway(
            snapshot.root,
            cancelled=cancelled,
            invocation_id=invocation.id,
            deadline_at=deadline_at,
        )
        provider_mode = _normalize_provider_mode(getattr(gateway, "mode", None), processed=False)
        from security_agents.agents import ALL_SPECS
        from security_agents.agents import diagnose as diagnose_agent
        from security_agents.agents import scout as scout_agent
        from security_agents.contracts import Budget
        from security_agents.runtime import run_agent
        from security_agents.tools import default_registry

        spec = ALL_SPECS[_sa_name(agent)]
        if agent is AgentName.scout:
            data = _scout_input(decision, intel)
            prompt = scout_agent.build_prompt(data, intel)
        elif agent is AgentName.diagnose:
            if not scout_output:
                raise PrerequisiteNotReady("Diagnose waiting for Scout hypotheses")
            data = _diagnose_input(decision, scout_output)
            prompt = (
                "This input is a first-responder Scout hypothesis, not a confirmed finding.\n\n"
                + diagnose_agent.build_prompt(data, intel)
            )
        else:
            raise RuntimeError(f"{agent.value} is not a Phase 7C live investigative agent")

        ctx = _tool_context(
            agent,
            intel,
            assessment_id=decision.id,
            run_id=invocation.id,
            cancelled=cancelled,
        )
        outcome = run_agent(
            spec,
            gateway=gateway,
            registry=default_registry(),
            ctx=ctx,
            task_prompt=prompt,
            budget=Budget(
                max_tokens=80_000,
                max_usd=min(FIRST_RESPONDER_MAX_USD, 1.5),
                max_wall_clock_s=_wall_clock_s(spec.step_timeout_s, remain),
            ),
            input_ref=f"first-responder:{decision.id}:{agent.value}",
            cancelled=cancelled,
        )
        processed = True
        latency_ms = int((time.monotonic() - t0) * 1000)
        provider_mode = _normalize_provider_mode(str(outcome.record.provider_mode), processed=True)
        usage = outcome.record.usage
        hypotheses: list[dict[str, Any]] = []
        integrity = ExecutionIntegrity.failed
        typed = outcome.output is not None
        if agent is AgentName.scout and isinstance(outcome.output, scout_agent.ScoutOutput):
            hyps = scout_agent.to_hypotheses(outcome.output, data, intel)
            hypotheses = [h.model_dump(mode="json") for h in hyps]
            integrity = ExecutionIntegrity.checked if hyps else ExecutionIntegrity.failed
        elif agent is AgentName.diagnose and outcome.output is not None:
            try:
                _plan = diagnose_agent.to_plan(outcome.output, data, intel)
                integrity = ExecutionIntegrity.checked if _plan[2] else ExecutionIntegrity.failed
            except Exception:
                integrity = ExecutionIntegrity.failed
        transcript_id, output_id = _persist_loop_artifacts(
            project_id=decision.project_id,
            agent=agent,
            outcome=outcome,
            scout_data=data,
            integrity=integrity,
            hypotheses=hypotheses,
        )
        error_code = outcome.run.error_code.value if outcome.run.error_code else None
        usd = float(usage.usd) if usage else None
        execution = _with_ledger(
            invocation.id,
            _record(
                provider_mode=provider_mode,
                model_id=outcome.record.model,
                cost_usd=usd,
                latency_ms=latency_ms,
                llm_turns=outcome.run.llm_turns,
                tool_call_count=outcome.run.tool_calls,
                started_at=started,
                ended_at=utcnow(),
                transcript_id=transcript_id,
                output_id=output_id,
                error_code=error_code,
                typed_output=typed,
                hypothesis_count=len(hypotheses) if agent is AgentName.scout else None,
                integrity=integrity,
            ),
        )
        if not outcome.ok:
            status = (
                AgentInvocationStatus.cancelled
                if error_code in {"CANCELLED", "KILL_SWITCH"}
                else AgentInvocationStatus.failed
            )
            return ExecutionOutcome(
                status=status,
                reason=(
                    f"{agent.value} agent loop failed ({error_code or 'unknown'}). "
                    "Not a confirmed threat."
                )[:2000],
                provider_mode=provider_mode,
                execution=execution,
            )
        reason = (
            f"{agent.value} executed a real agent loop ({provider_mode}). "
            f"execution_integrity={integrity.value}. "
            "Typed output is not independent verification and not a confirmed threat."
        )
        return ExecutionOutcome(
            status=AgentInvocationStatus.executed,
            reason=reason[:2000],
            provider_mode=provider_mode,
            execution=execution,
        )
    except PrerequisiteNotReady:
        raise
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        message = str(exc)
        if "SNAPSHOT_NOT_READY" in message:
            code = "SNAPSHOT_NOT_READY"
        elif "LLM_UNAVAILABLE" in message:
            code = "LLM_UNAVAILABLE"
        elif "DEADLINE_EXCEEDED" in message:
            code = "BUDGET_EXHAUSTED"
        else:
            code = type(exc).__name__
            for catalog in (
                "LLM_UNAVAILABLE",
                "BUDGET_EXHAUSTED",
                "CANCELLED",
                "SCHEMA_INVALID",
                "SNAPSHOT_NOT_READY",
            ):
                if catalog in message:
                    code = catalog
                    break
        log.exception("first-responder %s execution failed", agent.value)
        provider_mode = _normalize_provider_mode(provider_mode, processed=processed)
        execution = _with_ledger(
            invocation.id,
            _record(
                provider_mode=provider_mode,
                model_id=None,
                cost_usd=None if not processed else 0.0,
                latency_ms=latency_ms,
                llm_turns=None,
                tool_call_count=None,
                started_at=started,
                ended_at=utcnow(),
                transcript_id=None,
                output_id=None,
                error_code=code[:200],
                typed_output=False,
                hypothesis_count=None,
                integrity=ExecutionIntegrity.failed,
            ),
        )
        status = (
            AgentInvocationStatus.cancelled
            if code in {"CANCELLED", "KILL_SWITCH"}
            else AgentInvocationStatus.failed
        )
        return ExecutionOutcome(
            status=status,
            reason=f"{agent.value} did not execute: {message[:400]}",
            provider_mode=provider_mode,
            execution=execution,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def load_scout_output(conn: Connection, artifact_id: str | None) -> dict[str, Any] | None:
    if not artifact_id:
        return None
    try:
        _meta, data = ArtifactStore().get(conn, artifact_id)
    except (KeyError, OSError):
        return None
    try:
        payload = json.loads(data.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
