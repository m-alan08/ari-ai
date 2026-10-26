"""Phase 7C security-review regressions. FakeLLM/FakeTransport only — not live Anthropic."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from helpers import setup_project

from backend import llm, worker
from backend.db import first_responder_invocations, snapshots, transaction
from backend.db import jobs as jobs_t
from backend.live_defense import agent_execution
from backend.live_defense.agent_execution import _BackendGateway, load_intel
from backend.llm import FakeTransport, LLMError, LLMGateway, cost_usd
from backend.settings import Settings
from shared.assessment import AgentName
from shared.base import utcnow
from shared.errors import ErrorCode
from shared.ids import IdPrefix, new_id
from shared.live_defense import FIRST_RESPONDER_JOB_KINDS, AgentInvocationStatus

pytest.importorskip("security_agents")

from test_live_defense_phase7c import (  # noqa: E402
    _batch,
    _bind_and_fake,
    _owned_repo,
    _ready_snapshot,
    _register,
)


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, anthropic_api_key=None, **kw)


def _inner(
    script: list, *, mode: str = "live", llm_max_retries: int = 2, **hooks: object
) -> LLMGateway:
    transport = FakeTransport(script)
    transport.mode = mode  # type: ignore[misc]
    return LLMGateway(
        settings=_settings(llm_max_retries=llm_max_retries), transport=transport, **hooks
    )


def test_cache_creation_is_included_in_adapter_cost() -> None:
    gw = _BackendGateway(_inner([]))
    usage = SimpleNamespace(
        input_tokens=0,
        output_tokens=100,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=10_000,
    )
    assert gw.cost_usd("claude-opus-5-5", usage) == pytest.approx(
        cost_usd(
            "claude-opus-5-5",
            {
                "input_tokens": 0,
                "output_tokens": 100,
                "cache_creation_input_tokens": 10_000,
            },
        )
    )
    assert gw.cost_usd("claude-opus-5-5", usage) == pytest.approx(0.052)


def test_cache_read_and_mixed_usage_are_not_double_counted() -> None:
    gw = _BackendGateway(_inner([]))
    usage = SimpleNamespace(
        input_tokens=1_000,
        output_tokens=50,
        cache_read_input_tokens=8_000,
        cache_creation_input_tokens=2_000,
    )
    expected = cost_usd(
        "claude-haiku-5-5",
        {
            "input_tokens": 1_000,
            "output_tokens": 50,
            "cache_read_input_tokens": 8_000,
            "cache_creation_input_tokens": 2_000,
        },
    )
    assert gw.cost_usd("claude-haiku-5-5", usage) == pytest.approx(expected)
    assert expected > cost_usd("claude-haiku-5-5", {"input_tokens": 1_000, "output_tokens": 50})


def test_live_missing_usage_fails_closed_not_silent_zero() -> None:
    from security_agents.contracts import AgentName as SAName
    from security_agents.contracts import ErrorCode as SAError
    from security_agents.llm.types import LLMRequest, LLMUnavailable

    gw = _BackendGateway(
        _inner(
            [
                {
                    "content": [{"type": "text", "text": "x"}],
                    "stop_reason": "end_turn",
                    "usage": {},
                }
            ]
        )
    )
    request = LLMRequest(
        agent=SAName.SCOUT,
        model="claude-haiku-5-5",
        system="s",
        messages=[{"role": "user", "content": "q"}],
        tools=[],
        max_tokens=16,
        timeout_s=30,
    )
    with pytest.raises(LLMUnavailable) as exc:
        gw.complete(request)
    assert exc.value.code is SAError.BUDGET_EXHAUSTED


def test_deadline_refuses_to_start_without_enough_time() -> None:
    from security_agents.contracts import AgentName as SAName
    from security_agents.contracts import ErrorCode as SAError
    from security_agents.llm.types import LLMRequest, LLMUnavailable

    calls: list[int] = []

    class Counting(FakeTransport):
        def create(self, request):  # type: ignore[no-untyped-def]
            calls.append(1)
            return super().create(request)

    inner = LLMGateway(settings=_settings(), transport=Counting([]))
    inner.transport.mode = "live"  # type: ignore[misc]
    gw = _BackendGateway(inner)
    request = LLMRequest(
        agent=SAName.SCOUT,
        model="claude-haiku-5-5",
        system="s",
        messages=[{"role": "user", "content": "q"}],
        tools=[],
        max_tokens=16,
        timeout_s=0.2,
    )
    with pytest.raises(LLMUnavailable) as exc:
        gw.complete(request)
    assert exc.value.code is SAError.BUDGET_EXHAUSTED
    assert "DEADLINE_EXCEEDED" in str(exc.value)
    assert calls == []


def test_retry_exhaustion_and_deadline_are_distinct_from_cancel() -> None:
    from security_agents.contracts import AgentName as SAName
    from security_agents.contracts import ErrorCode as SAError
    from security_agents.llm.types import LLMRequest, LLMUnavailable

    retryable = LLMError(ErrorCode.LLM_UNAVAILABLE, "rate limited", retryable=True)
    inner = _inner([retryable, retryable, retryable], llm_max_retries=1)
    gw = _BackendGateway(inner)
    request = LLMRequest(
        agent=SAName.SCOUT,
        model="claude-haiku-5-5",
        system="s",
        messages=[{"role": "user", "content": "q"}],
        tools=[],
        max_tokens=16,
        timeout_s=30,
    )
    with pytest.raises(LLMUnavailable) as exc:
        gw.complete(request)
    assert exc.value.code is SAError.LLM_UNAVAILABLE
    assert "attempt" in str(exc.value)

    cancelled = {"flag": False}

    def check() -> None:
        if cancelled["flag"]:
            raise LLMError(ErrorCode.CANCELLED, "off")

    inner2 = _inner(
        [{"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}],
        check_cancelled=check,
    )
    gw2 = _BackendGateway(inner2, cancelled=lambda: cancelled["flag"])
    cancelled["flag"] = True
    with pytest.raises(LLMUnavailable) as exc2:
        gw2.complete(request)
    assert exc2.value.code is SAError.CANCELLED


def test_partial_transcript_survives_later_provider_failure(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    from security_agents.llm.types import LLMResponse, LLMUnavailable, LLMUsage

    calls = {"n": 0}

    class SucceedThenFail:
        mode = "live"

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return "claude-haiku-5-5"

        def complete(self, request):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] == 1:
                return LLMResponse(
                    content=[
                        {
                            "type": "tool_use",
                            "id": "tu_partial",
                            "name": "code.search",
                            "input": {"pattern": "payments"},
                        }
                    ],
                    stop_reason="tool_use",
                    usage=LLMUsage(
                        input_tokens=11,
                        output_tokens=7,
                        cache_creation_input_tokens=40,
                    ),
                    model="claude-haiku-5-5",
                    provider_mode="live",
                )
            raise LLMUnavailable("later live failure", code="LLM_UNAVAILABLE")

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return cost_usd(
                model,
                {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_input_tokens": usage.cache_read_input_tokens,
                    "cache_creation_input_tokens": usage.cache_creation_input_tokens,
                },
            )

    repo, sha = _owned_repo(tmp_path)
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: SucceedThenFail())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": rid,
            "commit_sha": sha,
            "deployed_at": (utcnow() - timedelta(minutes=5)).isoformat(),
            "source": "owner_attested",
        },
    )
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    assert (
        alice.post(
            f"/api/v1/ingest/runtime/{pid}",
            json=_batch(utcnow()),
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 202
    )
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.failed.value
    assert scout["provider_mode"] == "live"
    assert scout["execution"]["error_code"] == "LLM_UNAVAILABLE"
    assert scout["execution"]["evidence_verified"] is False
    assert scout["execution"]["transcript_artifact_id"]
    assert scout["execution"]["llm_turns"] and scout["execution"]["llm_turns"] >= 1
    assert scout["execution"]["cost_usd"] and scout["execution"]["cost_usd"] > 0
    from backend.artifacts import ArtifactStore

    with transaction() as conn:
        _meta, raw = ArtifactStore().get(conn, scout["execution"]["transcript_artifact_id"])
    payload = json.loads(raw.decode())
    assert payload["provider_mode"] == "live"
    assert payload["turns"]
    assert payload["turns"][0]["usage"]["cache_creation_input_tokens"] == 40
    diagnose = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "diagnose"
    )
    assert diagnose["status"] == AgentInvocationStatus.skipped_no_prerequisite.value


def test_canonical_intel_fails_closed_without_index(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    repo, sha = _owned_repo(tmp_path)
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    bindings = tmp_path / "bindings.json"
    bindings.write_text(json.dumps([{"repo_id": rid, "local_path": str(repo), "subdir": "."}]))
    monkeypatch.delenv("SECURITY_AGENTS_LABELS", raising=False)
    settings_override(
        repository_bindings_file=str(bindings),
        security_sandbox="local-dev",
        security_workdir=str(tmp_path / "work"),
    )
    llm.TRANSPORT_OVERRIDE = lambda: FakeTransport([])
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": rid,
            "commit_sha": sha,
            "deployed_at": (utcnow() - timedelta(minutes=5)).isoformat(),
            "source": "owner_attested",
        },
    )
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    alice.post(
        f"/api/v1/ingest/runtime/{pid}",
        json=_batch(utcnow()),
        headers={"Authorization": f"Bearer {token}"},
    )
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.failed.value
    assert scout["execution"]["error_code"] == "SNAPSHOT_NOT_READY"
    assert scout["execution"]["confirmation"] == "unverified"


def test_local_intel_rejected_for_cross_project_snapshot(tmp_path) -> None:
    handle = SimpleNamespace(
        repo_id="rep_other",
        commit_sha="a" * 40,
        snapshot_id="snp_other",
        root=tmp_path,
        pinned_by="git",
    )
    with pytest.raises(RuntimeError, match="authorized snapshot"):
        load_intel(
            handle,
            project_id="prj_one",
            repo_id="rep_mine",
            commit_sha="b" * 40,
            snapshot_id="snp_mine",
        )


def test_diagnose_is_not_claimed_while_scout_runs(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    from pathlib import Path as P

    import security_agents
    from security_agents.llm import FakeLLM, ProviderGateway

    tests_dir = P(security_agents.__file__).resolve().parents[1]
    import sys

    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))
    from tests.fake_agents import all_scripts

    repo, sha = _owned_repo(tmp_path)
    started = threading.Event()
    release = threading.Event()

    class SlowScout:
        mode = "fake"

        def __init__(self) -> None:
            self._inner = ProviderGateway(FakeLLM(all_scripts(repo / "target")))

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return self._inner.model_for(agent)

        def complete(self, request):  # type: ignore[no-untyped-def]
            started.set()
            release.wait(timeout=5)
            return self._inner.complete(request)

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return self._inner.cost_usd(model, usage)

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: SlowScout())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": rid,
            "commit_sha": sha,
            "deployed_at": (utcnow() - timedelta(minutes=5)).isoformat(),
            "source": "owner_attested",
        },
    )
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    alice.post(
        f"/api/v1/ingest/runtime/{pid}",
        json=_batch(utcnow()),
        headers={"Authorization": f"Bearer {token}"},
    )
    worker.drain(max_jobs=20, kinds=["live_defense_shadow"])
    thread = threading.Thread(
        target=lambda: worker.drain(
            max_jobs=20, kinds=["live_defense_first_responder", "live_defense_agent_step"]
        ),
        daemon=True,
    )
    thread.start()
    assert started.wait(timeout=15)
    with transaction() as conn:
        payloads = [row[0] for row in conn.execute(sa.select(jobs_t.c.payload))]
    assert [item for item in payloads if item.get("agent") == "diagnose"] == []
    release.set()
    thread.join(timeout=30)
    worker.drain(max_jobs=20, kinds=["live_defense_agent_step"])
    items = alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
    by_agent = {item["agent"]: item for item in items}
    assert by_agent["scout"]["status"] == AgentInvocationStatus.executed.value
    assert by_agent["diagnose"]["status"] == AgentInvocationStatus.executed.value


def test_redelivery_after_success_does_not_rerun(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    calls = {"n": 0}
    import sys
    from pathlib import Path as P

    import security_agents
    from security_agents.llm import FakeLLM, ProviderGateway

    repo, sha = _owned_repo(tmp_path)
    tests_dir = P(security_agents.__file__).resolve().parents[1]
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))
    from tests.fake_agents import all_scripts

    class Counting:
        mode = "fake"

        def __init__(self) -> None:
            self._inner = ProviderGateway(FakeLLM(all_scripts(repo / "target")))

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return self._inner.model_for(agent)

        def complete(self, request):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return self._inner.complete(request)

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return self._inner.cost_usd(model, usage)

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: Counting())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": rid,
            "commit_sha": sha,
            "deployed_at": (utcnow() - timedelta(minutes=5)).isoformat(),
            "source": "owner_attested",
        },
    )
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    alice.post(
        f"/api/v1/ingest/runtime/{pid}",
        json=_batch(utcnow()),
        headers={"Authorization": f"Bearer {token}"},
    )
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    first = calls["n"]
    assert first > 0
    with transaction() as conn:
        rows = list(
            conn.execute(sa.select(jobs_t.c.id).where(jobs_t.c.kind == "live_defense_agent_step"))
        )
        for (jid,) in rows:
            conn.execute(
                sa.update(jobs_t)
                .where(jobs_t.c.id == jid)
                .values(
                    status="running",
                    lease_owner="dead-worker",
                    lease_expires_at=sa.func.now() - sa.text("interval '1 second'"),
                    cancel_requested=False,
                )
            )
    worker.drain(max_jobs=40, kinds=["live_defense_agent_step"])
    assert calls["n"] == first
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.executed.value
    assert scout["execution"]["confirmation"] == "unverified"


def test_failed_retry_cannot_replace_successful_result(client_factory) -> None:
    from backend.db import first_responder_decisions
    from backend.live_defense.first_responder import _commit_invocation
    from shared.live_defense import (
        AgentCapabilityClass,
        AgentExecutionRecord,
        ExecutionIntegrity,
        FirstResponderInvocation,
        OrchestratorAction,
    )

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    now = utcnow()
    decision_id = new_id(IdPrefix.frd)
    inv = FirstResponderInvocation(
        id=new_id(IdPrefix.fri),
        decision_id=decision_id,
        project_id=ids["project"],
        agent=AgentName.scout,
        capability_class=AgentCapabilityClass.investigative_readonly,
        status=AgentInvocationStatus.executed,
        provider_mode="fake",
        reason="done",
        created_at=now,
        execution=AgentExecutionRecord(
            provider_mode="fake",
            execution_integrity=ExecutionIntegrity.checked,
            cost_usd=0.01,
        ),
    )
    with transaction() as conn:
        conn.execute(
            sa.insert(first_responder_decisions).values(
                id=decision_id,
                project_id=ids["project"],
                dedupe_key=f"review:{decision_id}",
                action=OrchestratorAction.investigate.value,
                doc={"id": decision_id},
            )
        )
        conn.execute(
            sa.insert(first_responder_invocations).values(
                id=inv.id,
                decision_id=decision_id,
                project_id=inv.project_id,
                agent="scout",
                status=inv.status.value,
                doc=inv.model_dump(mode="json"),
            )
        )
        failed = inv.model_copy(
            update={
                "status": AgentInvocationStatus.failed,
                "reason": "retry failed",
                "provider_mode": "none",
                "execution": AgentExecutionRecord(
                    provider_mode="none", execution_integrity=ExecutionIntegrity.failed
                ),
            }
        )
        kept = _commit_invocation(conn, failed, status=AgentInvocationStatus.failed)
    assert kept.status is AgentInvocationStatus.executed
    assert kept.provider_mode == "fake"
    assert kept.execution and kept.execution.cost_usd == 0.01


def test_budget_exhaustion_persists_unverified(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    from security_agents.contracts import ErrorCode as SAError
    from security_agents.llm.types import LLMUnavailable

    class Exhaust:
        mode = "live"

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return "claude-haiku-5-5"

        def complete(self, request):  # type: ignore[no-untyped-def]
            raise LLMUnavailable("usd budget reached", code=SAError.BUDGET_EXHAUSTED)

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return 9.0

    repo, sha = _owned_repo(tmp_path)
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: Exhaust())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": rid,
            "commit_sha": sha,
            "deployed_at": (utcnow() - timedelta(minutes=5)).isoformat(),
            "source": "owner_attested",
        },
    )
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    alice.post(
        f"/api/v1/ingest/runtime/{pid}",
        json=_batch(utcnow()),
        headers={"Authorization": f"Bearer {token}"},
    )
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.failed.value
    assert scout["execution"]["error_code"] == "BUDGET_EXHAUSTED"
    assert scout["provider_mode"] == "live"
    assert scout["execution"]["evidence_verified"] is False
    decisions = alice.get(f"/api/v1/projects/{pid}/first-responder/decisions").json()["items"]
    assert decisions[0]["executed"] is False


def test_off_during_loop_cancels_and_does_not_confirm(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    import sys
    from pathlib import Path as P

    import security_agents
    from security_agents.llm import FakeLLM, ProviderGateway

    repo, sha = _owned_repo(tmp_path)
    tests_dir = P(security_agents.__file__).resolve().parents[1]
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))
    from tests.fake_agents import all_scripts

    started = threading.Event()
    release = threading.Event()

    class Slow:
        mode = "fake"

        def __init__(self) -> None:
            self._inner = ProviderGateway(FakeLLM(all_scripts(repo / "target")))

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return self._inner.model_for(agent)

        def complete(self, request):  # type: ignore[no-untyped-def]
            started.set()
            release.wait(timeout=8)
            return self._inner.complete(request)

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return self._inner.cost_usd(model, usage)

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: Slow())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": rid,
            "commit_sha": sha,
            "deployed_at": (utcnow() - timedelta(minutes=5)).isoformat(),
            "source": "owner_attested",
        },
    )
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    alice.post(
        f"/api/v1/ingest/runtime/{pid}",
        json=_batch(utcnow()),
        headers={"Authorization": f"Bearer {token}"},
    )
    worker.drain(max_jobs=20, kinds=["live_defense_shadow"])
    thread = threading.Thread(
        target=lambda: worker.drain(max_jobs=20, kinds=list(FIRST_RESPONDER_JOB_KINDS)),
        daemon=True,
    )
    thread.start()
    assert started.wait(timeout=15)
    off = alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "off"})
    assert off.status_code == 200
    release.set()
    thread.join(timeout=30)
    worker.drain(max_jobs=20, kinds=list(FIRST_RESPONDER_JOB_KINDS))
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] in {
        AgentInvocationStatus.cancelled.value,
        AgentInvocationStatus.failed.value,
    }
    if scout.get("execution"):
        assert scout["execution"]["evidence_verified"] is False
        assert scout["execution"]["confirmation"] == "unverified"
    decisions = alice.get(f"/api/v1/projects/{pid}/first-responder/decisions").json()["items"]
    assert decisions[0]["executed"] is False


def test_model_cannot_escalate_to_probe_or_deploy(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    from security_agents.contracts import ErrorCode as SAError
    from security_agents.llm.types import LLMResponse, LLMUnavailable, LLMUsage

    class ProbeThenSubmit:
        mode = "fake"
        calls = 0

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return "claude-haiku-5-5"

        def complete(self, request):  # type: ignore[no-untyped-def]
            ProbeThenSubmit.calls += 1
            if ProbeThenSubmit.calls == 1:
                return LLMResponse(
                    content=[
                        {
                            "type": "tool_use",
                            "id": "tu_probe",
                            "name": "sandbox.exec",
                            "input": {"command": "curl http://169.254.169.254"},
                        }
                    ],
                    stop_reason="tool_use",
                    usage=LLMUsage(input_tokens=4, output_tokens=4),
                    model="claude-haiku-5-5",
                    provider_mode="fake",
                )
            raise LLMUnavailable("stop after denied tool", code=SAError.LLM_UNAVAILABLE)

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return 0.0

    repo, sha = _owned_repo(tmp_path)
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: ProbeThenSubmit())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "assisted_response"})
    alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": rid,
            "commit_sha": sha,
            "deployed_at": (utcnow() - timedelta(minutes=5)).isoformat(),
            "source": "owner_attested",
        },
    )
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    alice.post(
        f"/api/v1/ingest/runtime/{pid}",
        json=_batch(utcnow()),
        headers={"Authorization": f"Bearer {token}"},
    )
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    items = alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
    scout = next(item for item in items if item["agent"] == "scout")
    probe = next(item for item in items if item["agent"] == "probe")
    patch = next(item for item in items if item["agent"] == "patch")
    assert scout["execution"]["transcript_artifact_id"]
    from backend.artifacts import ArtifactStore

    with transaction() as conn:
        _meta, raw = ArtifactStore().get(conn, scout["execution"]["transcript_artifact_id"])
    payload = json.loads(raw.decode())
    denied = [t for t in payload["tool_invocations"] if t["name"] == "sandbox.exec"]
    assert denied and denied[0]["ok"] is False
    assert probe["provider_mode"] == "none"
    assert probe["execution"] is None
    assert patch["worktree_only"] is True
    assert patch["silent_deploy"] is False
    auto = alice.post(
        f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "auto_execute"}
    )
    assert auto.status_code in {400, 422}


def _arm_first_responder(alice, pid, rid, sha, app_id) -> None:
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    alice.post(
        f"/api/v1/applications/{app_id}/deploy-mappings",
        json={
            "repo_id": rid,
            "commit_sha": sha,
            "deployed_at": (utcnow() - timedelta(minutes=5)).isoformat(),
            "source": "owner_attested",
        },
    )
    token = alice.post(f"/api/v1/applications/{app_id}/ingest-tokens").json()["token"]
    alice.post(
        f"/api/v1/ingest/runtime/{pid}",
        json=_batch(utcnow()),
        headers={"Authorization": f"Bearer {token}"},
    )


def _scripted_gateway(repo):
    import sys
    from pathlib import Path as P

    import security_agents
    from security_agents.llm import FakeLLM, ProviderGateway

    tests_dir = P(security_agents.__file__).resolve().parents[1]
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))
    from tests.fake_agents import all_scripts

    return ProviderGateway(FakeLLM(all_scripts(repo / "target")))


def test_malformed_usage_fails_closed_not_silent_zero() -> None:
    from security_agents.contracts import AgentName as SAName
    from security_agents.contracts import ErrorCode as SAError
    from security_agents.llm.types import LLMRequest, LLMUnavailable

    gw = _BackendGateway(
        _inner(
            [
                {
                    "content": [{"type": "text", "text": "x"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": "not-a-number", "output_tokens": 4},
                }
            ]
        )
    )
    request = LLMRequest(
        agent=SAName.SCOUT,
        model="claude-haiku-5-5",
        system="s",
        messages=[{"role": "user", "content": "q"}],
        tools=[],
        max_tokens=16,
        timeout_s=30,
    )
    with pytest.raises(LLMUnavailable) as exc:
        gw.complete(request)
    assert exc.value.code is SAError.BUDGET_EXHAUSTED


def test_stale_revoked_mapping_skips_scout(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    from backend.db import deploy_commit_mappings

    repo, sha = _owned_repo(tmp_path)
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    _arm_first_responder(alice, pid, rid, sha, app["id"])
    worker.drain(max_jobs=20, kinds=["live_defense_shadow", "live_defense_first_responder"])
    with transaction() as conn:
        conn.execute(sa.update(deploy_commit_mappings).values(revoked_at=sa.func.now()))
    worker.drain(max_jobs=20, kinds=["live_defense_agent_step"])
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.skipped_no_mapping.value
    assert scout["provider_mode"] == "none"
    assert scout["execution"] is None


def test_snapshot_mismatch_fails_closed(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    from shared.intelligence import Snapshot, SnapshotStats, SnapshotStatus

    repo, sha = _owned_repo(tmp_path)
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    bindings = tmp_path / "bindings.json"
    bindings.write_text(json.dumps([{"repo_id": rid, "local_path": str(repo), "subdir": "."}]))
    monkeypatch.delenv("SECURITY_AGENTS_LABELS", raising=False)
    settings_override(
        repository_bindings_file=str(bindings),
        security_sandbox="local-dev",
        security_workdir=str(tmp_path / "work"),
    )
    llm.TRANSPORT_OVERRIDE = lambda: FakeTransport([])
    sid = new_id(IdPrefix.snp)
    other_sha = "c" * 40
    now = utcnow()
    snap = Snapshot(
        id=sid,
        repo_id=rid,
        commit_sha=other_sha,
        status=SnapshotStatus.ready,
        index_version="phase7c",
        stats=SnapshotStats(),
        created_at=now,
        completed_at=now,
    )
    with transaction() as conn:
        conn.execute(
            sa.insert(snapshots).values(
                id=sid,
                repo_id=rid,
                commit_sha=sha,
                index_version="phase7c",
                status="ready",
                doc=snap.model_dump(mode="json"),
                created_at=now,
            )
        )
    app = _register(alice, pid)
    _arm_first_responder(alice, pid, rid, sha, app["id"])
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.failed.value
    assert scout["execution"]["error_code"] == "SNAPSHOT_NOT_READY"
    assert scout["execution"]["confirmation"] == "unverified"


def test_canonical_intel_uses_adapter_bound_to_authorized_ref(monkeypatch, tmp_path) -> None:
    from shared.intelligence import SnapshotRef

    captured: dict[str, object] = {}

    class FakeAdapter:
        def __init__(self, *, service, ref, workspace, path_prefix):  # type: ignore[no-untyped-def]
            captured["ref"] = ref
            captured["workspace"] = workspace
            self.snapshot = SimpleNamespace(
                repo_id=ref.repo_id,
                commit_sha=ref.commit_sha,
                snapshot_id=ref.snapshot_id,
                root=workspace,
            )

        def verify_workspace(self) -> None:
            captured["verified"] = True

        def symbols(self) -> list:
            captured["symbols"] = True
            return []

    ref = SnapshotRef(
        project_id=new_id(IdPrefix.prj),
        repo_id=new_id(IdPrefix.rep),
        commit_sha="d" * 40,
        snapshot_id=new_id(IdPrefix.snp),
        index_version="intel-0.1",
    )
    monkeypatch.delenv("SECURITY_AGENTS_LABELS", raising=False)
    monkeypatch.setattr("backend.intelligence.snapshot_ref", lambda sid: ref)
    monkeypatch.setattr("backend.intelligence.repository_service", lambda *a, **k: object())
    monkeypatch.setattr(
        "security_agents.intel.code_intelligence_adapter.CodeIntelligenceAdapter",
        FakeAdapter,
    )
    handle = SimpleNamespace(
        repo_id=ref.repo_id,
        commit_sha=ref.commit_sha,
        snapshot_id=ref.snapshot_id,
        root=tmp_path,
        pinned_by="git",
    )
    intel = load_intel(
        handle,
        project_id=ref.project_id,
        repo_id=ref.repo_id,
        commit_sha=ref.commit_sha,
        snapshot_id=ref.snapshot_id,
    )
    assert captured["verified"] is True
    assert captured["symbols"] is True
    assert captured["ref"] == ref
    assert intel.snapshot.repo_id == ref.repo_id


def test_crash_in_progress_reruns_and_later_redelivery_is_idempotent(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    from shared.live_defense import AgentExecutionRecord, ExecutionIntegrity

    calls = {"n": 0}
    repo, sha = _owned_repo(tmp_path)
    inner = _scripted_gateway(repo)

    class Counting:
        mode = "fake"

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return inner.model_for(agent)

        def complete(self, request):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return inner.complete(request)

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return inner.cost_usd(model, usage)

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: Counting())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    _arm_first_responder(alice, pid, rid, sha, app["id"])
    worker.drain(max_jobs=20, kinds=["live_defense_shadow", "live_defense_first_responder"])
    with transaction() as conn:
        inv_row = (
            conn.execute(
                sa.select(first_responder_invocations).where(
                    first_responder_invocations.c.agent == "scout"
                )
            )
            .mappings()
            .one()
        )
        inv_doc = inv_row["doc"]
        inv_doc["execution"] = AgentExecutionRecord(
            started_at=utcnow(),
            execution_integrity=ExecutionIntegrity.not_run,
        ).model_dump(mode="json")
        conn.execute(
            sa.update(first_responder_invocations)
            .where(first_responder_invocations.c.id == inv_row["id"])
            .values(doc=inv_doc, status="queued")
        )
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.kind == "live_defense_agent_step")
            .values(
                status="running",
                lease_owner="dead-worker",
                lease_expires_at=sa.func.now() - sa.text("interval '1 second'"),
                cancel_requested=False,
            )
        )
    worker.drain(max_jobs=40, kinds=["live_defense_agent_step"])
    recovered = calls["n"]
    assert recovered > 0
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.executed.value
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.kind == "live_defense_agent_step")
            .values(
                status="running",
                lease_owner="dead-worker",
                lease_expires_at=sa.func.now() - sa.text("interval '1 second'"),
                cancel_requested=False,
            )
        )
    worker.drain(max_jobs=40, kinds=["live_defense_agent_step"])
    assert calls["n"] == recovered


def test_redelivery_after_failure_does_not_rerun(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    from security_agents.contracts import ErrorCode as SAError
    from security_agents.llm.types import LLMUnavailable

    calls = {"n": 0}

    class FailOnce:
        mode = "live"

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return "claude-haiku-5-5"

        def complete(self, request):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            raise LLMUnavailable("provider down", code=SAError.LLM_UNAVAILABLE)

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return 0.0

    repo, sha = _owned_repo(tmp_path)
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: FailOnce())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    _arm_first_responder(alice, pid, rid, sha, app["id"])
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    first = calls["n"]
    assert first > 0
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.failed.value
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.kind == "live_defense_agent_step")
            .values(
                status="running",
                lease_owner="dead-worker",
                lease_expires_at=sa.func.now() - sa.text("interval '1 second'"),
                cancel_requested=False,
            )
        )
    worker.drain(max_jobs=40, kinds=["live_defense_agent_step"])
    assert calls["n"] == first
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.failed.value
    assert scout["provider_mode"] == "live"


def test_diagnose_wait_does_not_burn_retry_budget(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    from backend import jobs as jobq

    repo, sha = _owned_repo(tmp_path)
    started = threading.Event()
    release = threading.Event()
    inner = _scripted_gateway(repo)

    class SlowScout:
        mode = "fake"

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return inner.model_for(agent)

        def complete(self, request):  # type: ignore[no-untyped-def]
            started.set()
            release.wait(timeout=8)
            return inner.complete(request)

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return inner.cost_usd(model, usage)

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: SlowScout())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    _arm_first_responder(alice, pid, rid, sha, app["id"])
    worker.drain(max_jobs=20, kinds=["live_defense_shadow", "live_defense_first_responder"])
    with transaction() as conn:
        diagnose = (
            conn.execute(
                sa.select(first_responder_invocations).where(
                    first_responder_invocations.c.agent == "diagnose"
                )
            )
            .mappings()
            .one()
        )
        jid, _ = jobq.enqueue(
            conn,
            "live_defense_agent_step",
            {
                "project_id": pid,
                "decision_id": diagnose["decision_id"],
                "invocation_id": diagnose["id"],
                "agent": "diagnose",
                "capability_class": "investigative_readonly",
            },
            dedupe_key=f"live_defense_agent_step:{diagnose['decision_id']}:diagnose",
            max_attempts=2,
        )
    thread = threading.Thread(
        target=lambda: worker.drain(max_jobs=20, kinds=["live_defense_agent_step"]),
        daemon=True,
    )
    thread.start()
    assert started.wait(timeout=15)
    worker.drain(max_jobs=6, kinds=["live_defense_agent_step"])
    with transaction() as conn:
        row = dict(conn.execute(sa.select(jobs_t).where(jobs_t.c.id == jid)).mappings().one())
    assert row["status"] in {"queued", "running"}
    assert row["attempts"] <= 1
    assert row["status"] != "dead"
    release.set()
    thread.join(timeout=30)
    worker.drain(max_jobs=20, kinds=["live_defense_agent_step"])


def test_concurrent_workers_keep_first_executed(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    repo, sha = _owned_repo(tmp_path)
    started = threading.Event()
    release = threading.Event()
    inner = _scripted_gateway(repo)
    calls = {"n": 0}

    class Slow:
        mode = "fake"

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return inner.model_for(agent)

        def complete(self, request):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            started.set()
            release.wait(timeout=8)
            return inner.complete(request)

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return inner.cost_usd(model, usage)

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: Slow())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    _arm_first_responder(alice, pid, rid, sha, app["id"])
    worker.drain(max_jobs=20, kinds=["live_defense_shadow", "live_defense_first_responder"])
    t1 = threading.Thread(
        target=lambda: worker.drain(max_jobs=10, kinds=["live_defense_agent_step"]),
        daemon=True,
    )
    t1.start()
    assert started.wait(timeout=15)
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.kind == "live_defense_agent_step")
            .values(lease_expires_at=sa.func.now() - sa.text("interval '1 second'"))
        )
    t2 = threading.Thread(
        target=lambda: worker.drain(max_jobs=10, kinds=["live_defense_agent_step"]),
        daemon=True,
    )
    t2.start()
    release.set()
    t1.join(timeout=30)
    t2.join(timeout=30)
    worker.drain(max_jobs=20, kinds=["live_defense_agent_step"])
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.executed.value
    assert scout["execution"]["confirmation"] == "unverified"
    assert scout["execution"]["evidence_verified"] is False
    decisions = alice.get(f"/api/v1/projects/{pid}/first-responder/decisions").json()["items"]
    assert decisions[0]["executed"] is False


class _WorkerCrash(BaseException):
    """Models process death after send, before a response is recorded."""


def test_crash_during_provider_request_requires_recovery_before_replay(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    repo, sha = _owned_repo(tmp_path)
    inner = _scripted_gateway(repo)
    provider = {"calls": 0}

    class CrashAfterSending:
        mode = "fake"

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return inner.model_for(agent)

        def complete(self, request):  # type: ignore[no-untyped-def]
            provider["calls"] += 1
            if provider["calls"] == 1:
                raise _WorkerCrash("request sent; provider outcome is unknown")
            return inner.complete(request)

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return inner.cost_usd(model, usage)

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: CrashAfterSending())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    _arm_first_responder(alice, pid, rid, sha, app["id"])
    with pytest.raises(_WorkerCrash):
        worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.kind == "live_defense_agent_step")
            .values(lease_expires_at=sa.func.now() - sa.text("interval '1 second'"))
        )
    worker.drain(max_jobs=40, kinds=["live_defense_agent_step"])
    assert provider["calls"] == 1
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.failed.value
    assert scout["execution"]["uncertain_provider_outcome"] is True
    assert scout["execution"]["error_code"] == "UNCERTAIN_PROVIDER_OUTCOME"
    assert scout["execution"]["provider_attempts"]
    assert scout["execution"]["provider_attempts"][0]["status"] == "uncertain"
    assert scout["execution"]["evidence_verified"] is False
    decisions = alice.get(f"/api/v1/projects/{pid}/first-responder/decisions").json()["items"]
    assert decisions[0]["executed"] is False


def test_first_responder_step_has_a_durable_queue_deadline(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    repo, sha = _owned_repo(tmp_path)
    inner = _scripted_gateway(repo)
    calls = {"n": 0}

    class Counting:
        mode = "fake"

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return inner.model_for(agent)

        def complete(self, request):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return inner.complete(request)

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return inner.cost_usd(model, usage)

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]
    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: Counting())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    _arm_first_responder(alice, pid, rid, sha, app["id"])
    worker.drain(max_jobs=20, kinds=["live_defense_shadow", "live_defense_first_responder"])
    decisions = alice.get(f"/api/v1/projects/{pid}/first-responder/decisions").json()["items"]
    assert decisions and decisions[0]["deadline_at"]
    original = decisions[0]["deadline_at"]
    invocations = alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
    scouts = [item for item in invocations if item["agent"] == "scout"]
    assert scouts and scouts[0]["deadline_at"] == original
    with transaction() as conn:
        steps = list(
            conn.execute(sa.select(jobs_t).where(jobs_t.c.kind == "live_defense_agent_step"))
            .mappings()
            .all()
        )
        assert steps
        assert all((row["payload"] or {}).get("deadline_at") for row in steps)
        scout_row = (
            conn.execute(
                sa.select(first_responder_invocations).where(
                    first_responder_invocations.c.agent == "scout"
                )
            )
            .mappings()
            .one()
        )
        doc = dict(scout_row["doc"])
        expired = (utcnow() - timedelta(seconds=2)).isoformat()
        doc["deadline_at"] = expired
        conn.execute(
            sa.update(first_responder_invocations)
            .where(first_responder_invocations.c.id == scout_row["id"])
            .values(doc=doc)
        )
        for row in steps:
            payload = dict(row["payload"] or {})
            payload["deadline_at"] = expired
            conn.execute(sa.update(jobs_t).where(jobs_t.c.id == row["id"]).values(payload=payload))
    worker.drain(max_jobs=40, kinds=["live_defense_agent_step"])
    assert calls["n"] == 0
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.failed.value
    assert scout["execution"]["error_code"] == "BUDGET_EXHAUSTED"
    persisted = datetime.fromisoformat(str(scout["deadline_at"]).replace("Z", "+00:00"))
    assert persisted < utcnow(), "worker retry must not reset the overall investigation deadline"


def test_off_during_final_submit_cannot_upgrade_cancelled_invocation(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    from security_agents.agents.scout import SPEC

    repo, sha = _owned_repo(tmp_path)
    inner = _scripted_gateway(repo)
    flipped = {"ok": False}

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]

    class OffOnSubmit:
        mode = "fake"

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return inner.model_for(agent)

        def complete(self, request):  # type: ignore[no-untyped-def]
            response = inner.complete(request)
            agent = getattr(getattr(request, "agent", None), "value", request.agent)
            uses = response.tool_uses() if hasattr(response, "tool_uses") else []
            if agent == "scout" and any(
                block.get("name") == SPEC.submit_tool_name() for block in uses
            ):
                assert (
                    alice.post(
                        f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "off"}
                    ).status_code
                    == 200
                )
                flipped["ok"] = True
            return response

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return inner.cost_usd(model, usage)

    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: OffOnSubmit())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    _arm_first_responder(alice, pid, rid, sha, app["id"])
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    assert flipped["ok"]
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.cancelled.value
    assert scout["execution"]
    assert scout["execution"]["evidence_verified"] is False
    assert scout["execution"]["confirmation"] == "unverified"
    decisions = alice.get(f"/api/v1/projects/{pid}/first-responder/decisions").json()["items"]
    assert decisions[0]["executed"] is False


def test_job_cancel_during_final_submit_cannot_upgrade_cancelled_invocation(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    from security_agents.agents.scout import SPEC

    repo, sha = _owned_repo(tmp_path)
    inner = _scripted_gateway(repo)
    cancelled_job = {"ok": False}

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]

    class CancelOnSubmit:
        mode = "fake"

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return inner.model_for(agent)

        def complete(self, request):  # type: ignore[no-untyped-def]
            response = inner.complete(request)
            uses = response.tool_uses() if hasattr(response, "tool_uses") else []
            agent = getattr(getattr(request, "agent", None), "value", request.agent)
            if agent == "scout" and any(
                block.get("name") == SPEC.submit_tool_name() for block in uses
            ):
                with transaction() as conn:
                    conn.execute(
                        sa.update(jobs_t)
                        .where(jobs_t.c.kind == "live_defense_agent_step")
                        .values(cancel_requested=True)
                    )
                cancelled_job["ok"] = True
            return response

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return inner.cost_usd(model, usage)

    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: CancelOnSubmit())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    _arm_first_responder(alice, pid, rid, sha, app["id"])
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    assert cancelled_job["ok"]
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.cancelled.value
    assert scout["execution"]["evidence_verified"] is False
    decisions = alice.get(f"/api/v1/projects/{pid}/first-responder/decisions").json()["items"]
    assert decisions[0]["executed"] is False


def test_repository_disconnect_stops_next_provider_turn(
    client_factory, settings_override, monkeypatch, tmp_path
) -> None:
    """Codex P1 R2: disconnecting the mapped repo must not send another provider turn."""
    from backend.live_defense.first_responder import apply_repository_disconnect_to_investigations

    repo, sha = _owned_repo(tmp_path)
    inner = _scripted_gateway(repo)
    seen = {"n": 0, "tools": False}

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid, rid = ids["project"], ids["repo"]

    class DisconnectAfterFirstTurn:
        mode = "fake"

        def model_for(self, agent):  # type: ignore[no-untyped-def]
            return inner.model_for(agent)

        def complete(self, request):  # type: ignore[no-untyped-def]
            seen["n"] += 1
            response = inner.complete(request)
            uses = response.tool_uses() if hasattr(response, "tool_uses") else []
            if seen["n"] == 1:
                assert uses, "scout must have a later provider turn for this regression"
                seen["tools"] = True
                with transaction() as conn:
                    apply_repository_disconnect_to_investigations(conn, rid)
            return response

        def cost_usd(self, model, usage):  # type: ignore[no-untyped-def]
            return inner.cost_usd(model, usage)

    _bind_and_fake(tmp_path, repo, rid, settings_override, monkeypatch)
    monkeypatch.setattr(agent_execution, "GATEWAY_OVERRIDE", lambda: DisconnectAfterFirstTurn())
    _ready_snapshot(rid, sha)
    app = _register(alice, pid)
    _arm_first_responder(alice, pid, rid, sha, app["id"])
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    assert seen["tools"]
    assert seen["n"] == 1, "disconnect must stop the next billable complete()"
    scout = next(
        item
        for item in alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
        if item["agent"] == "scout"
    )
    assert scout["status"] == AgentInvocationStatus.cancelled.value
    assert scout["execution"]
    assert scout["execution"]["evidence_verified"] is False
    assert scout["execution"]["confirmation"] == "unverified"
    assert scout["execution"].get("cost_usd") is not None or scout["execution"].get(
        "provider_attempts"
    )
    decisions = alice.get(f"/api/v1/projects/{pid}/first-responder/decisions").json()["items"]
    assert decisions[0]["executed"] is False
