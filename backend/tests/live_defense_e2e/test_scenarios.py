"""Scenarios A–H: Live Defense owned-staging end-to-end integration tests.

Drives real HTTP routes, the real database, the real durable job queue
(``worker.drain``), and the real ``security_agents`` runtime. The model is
scripted (FakeLLM / injected wrappers); every record is labelled
``provider_mode="fake"``. Nothing here validates real Anthropic execution —
the single opt-in live test at the bottom is marked ``live_llm``.

Markers: ``live_defense_e2e`` (all), ``live_defense_smoke`` (fast CI subset),
``live_defense_fault`` (failure-injection suite), ``live_llm`` (opt-in live).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import timedelta

import pytest
from helpers import add_member

from backend import llm
from backend.live_defense import first_responder
from security_agents.contracts import AgentName as RuntimeAgentName
from security_agents.llm.providers import FakeTurn
from shared.base import utcnow
from shared.live_defense import (
    AgentInvocationStatus,
    AnalystHandoffState,
    ConfirmationState,
    ExecutionIntegrity,
)

from .transports import (
    FailAfterCalls,
    HookProvider,
    adversarial_scout_script,
    all_role_scripts,
    fake_provider,
    gateway_factory,
)

pytest.importorskip("security_agents")

pytestmark = pytest.mark.live_defense_e2e

INV = AgentInvocationStatus


# ---------------------------------------------------------------------------
# Scenario A — successful investigation: telemetry -> decision -> Scout ->
# Diagnose -> analyst handoff, with every persisted state/artifact verified.
# ---------------------------------------------------------------------------


@pytest.mark.live_defense_smoke
def test_a_successful_investigation(staging, client_factory, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob, approve_scope=True)
    repo, sha = staging.checkout_owned_repo("repo-a")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)

    assert staging.set_mode(alice, app.project_id, "assisted_response").status_code == 200
    resp = staging.ingest(alice, app)
    assert resp.status_code == 202, resp.text
    report.check("ingest accepted", 202, resp.status_code)

    staging.drain()

    decisions = staging.decisions(alice, app.project_id)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision["action"] == "confirm_with_probe"
    assert decision["executed"] is False
    assert decision["confirmation"] == ConfirmationState.unverified.value
    assert decision["predicted_probability"] is None
    assert decision["correlation"]["commit_sha"] == sha
    assert decision["trigger_event_ids"]
    report.check("orchestrator action", "confirm_with_probe", decision["action"])

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    scout = inv["scout"]
    assert scout["status"] == INV.executed.value
    assert scout["provider_mode"] == "fake"
    ex = scout["execution"]
    assert ex["typed_output"] is True
    assert ex["execution_integrity"] == ExecutionIntegrity.checked.value
    assert ex["evidence_verified"] is False
    assert ex["confirmation"] == ConfirmationState.unverified.value
    assert ex["hypothesis_count"] >= 1
    assert ex["transcript_artifact_id"] and ex["output_artifact_id"]
    assert inv["diagnose"]["status"] == INV.executed.value
    assert inv["diagnose"]["execution"]["evidence_verified"] is False
    assert inv["fortify"]["status"] == INV.skipped_no_prerequisite.value
    assert inv["probe"]["status"] == INV.completed_unverified.value
    assert inv["patch"]["status"] == INV.skipped_no_approval.value
    report.check("scout outcome", INV.executed.value, scout["status"])

    # Artifact integrity: read back through the store (sha256 verified).
    meta, transcript = staging.artifact_json(ex["transcript_artifact_id"])
    assert meta["project_id"] == app.project_id
    assert transcript.get("turns") or transcript.get("llm_turns") is not None or len(transcript)
    report.artifact(ex["transcript_artifact_id"], meta["kind"], "scout transcript")
    _, output = staging.artifact_json(ex["output_artifact_id"])
    assert output.get("hypotheses"), "scout output artifact must carry validated hypotheses"
    report.artifact(ex["output_artifact_id"], "tool_output", "scout hypotheses")

    handoffs = staging.handoffs(alice, app.project_id)
    assert len(handoffs) == 1
    handoff = handoffs[0]
    assert handoff["state"] == AnalystHandoffState.delivery_pending.value
    assert handoff["confirmation"] == ConfirmationState.unverified.value

    hid = handoff["id"]
    ack = alice.post(
        f"/api/v1/first-responder/handoffs/{hid}/delivery-ack",
        json={
            "provider": "slack",
            "provider_message_id": "e2e-msg-1",
            "received_at": utcnow().isoformat(),
        },
    )
    assert ack.status_code == 200, ack.text
    confirm = alice.post(f"/api/v1/first-responder/handoffs/{hid}/confirm")
    assert confirm.status_code == 409, confirm.text
    report.check("handoff confirm refused without executed probe", 409, confirm.status_code)

    assert staging.table_count(staging.T.runtime_windows, app.project_id) >= 1
    assert staging.table_count(staging.T.monitoring_events, app.project_id) >= 1
    assert "live_defense.first_responder.decided" in staging.audit_actions(app.project_id)


# ---------------------------------------------------------------------------
# Scenario B — missing authorization: bad token refused, no mapping means no
# investigation, no approved scope means no Probe, RBAC enforced.
# ---------------------------------------------------------------------------


@pytest.mark.live_defense_smoke
def test_b_missing_authorization(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    carol, dave = staging.login("carol"), staging.login("dave")

    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-b")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    token = staging.issue_token(app)
    # Deliberately no deploy mapping.

    assert staging.set_mode(alice, app.project_id, "monitor").status_code == 200

    # B1 — invalid ingest token is refused; nothing is persisted or investigated.
    bad = staging.ingest(alice, app, token="hkt_bad_token")
    assert bad.status_code != 202
    report.check("invalid ingest token refused", "not 202", bad.status_code)
    assert staging.table_count(staging.T.first_responder_decisions, app.project_id) == 0

    # B2 — valid token, no deploy mapping: correlation unknown -> correlate,
    # every investigative agent is skipped_no_mapping, zero code access.
    ok = staging.ingest(alice, app, token=token)
    assert ok.status_code == 202, ok.text
    staging.drain()
    decisions = staging.decisions(alice, app.project_id)
    assert decisions and decisions[0]["action"] == "correlate"
    assert decisions[0]["correlation"]["confidence"] == "unknown"
    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    for agent in ("scout", "diagnose", "fortify"):
        assert inv[agent]["status"] == INV.skipped_no_mapping.value
    assert not staging.artifact_rows(app.project_id, kind="llm_transcript")
    report.check(
        "no mapping -> scout blocked", INV.skipped_no_mapping.value, inv["scout"]["status"]
    )

    # B3 — assisted response without an approved scope: Probe is skipped_no_scope.
    app2 = staging.create_project(alice, bob, name="payments-lite-2", approve_scope=False)
    repo2, sha2 = staging.checkout_owned_repo("repo-b2")
    staging.bind_repo(app2, repo2)
    staging.index_snapshot(app2.repo_id, sha2)
    staging.register_app(app2)
    staging.issue_token(app2)
    staging.attest_mapping(app2, commit_sha=sha2)
    staging.set_mode(alice, app2.project_id, "assisted_response")
    assert staging.ingest(alice, app2).status_code == 202
    staging.drain()
    inv2 = staging.by_agent(staging.invocations(alice, app2.project_id))
    assert inv2["probe"]["status"] == INV.skipped_no_scope.value
    assert inv2["scout"]["status"] == INV.executed.value
    report.check(
        "no approved scope -> probe blocked", INV.skipped_no_scope.value, inv2["probe"]["status"]
    )

    # B4 — RBAC: non-owner member gets 403, outsider gets 404, auto-execute rejected.
    add_member(app.org_id, dave.me["user"]["id"], role="member")
    assert staging.set_mode(dave, app.project_id, "monitor").status_code == 403
    assert staging.set_mode(carol, app.project_id, "monitor").status_code == 404
    assert (
        carol.get(f"/api/v1/projects/{app.project_id}/first-responder/invocations").status_code
        == 404
    )
    bad_intent = staging.set_mode(alice, app.project_id, "auto_execute")
    assert bad_intent.status_code >= 400
    report.check("RBAC on mode API", "member 403 / outsider 404 / auto_execute 4xx", "403/404/4xx")


# ---------------------------------------------------------------------------
# Scenario C — stale snapshot: telemetry mapped to an old commit must not be
# investigated against a newer/HEAD commit.
# ---------------------------------------------------------------------------


def test_c_stale_snapshot(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha_new = staging.checkout_owned_repo(
        "repo-c", extra_commit={"target/app/extra.py": "x = 1\n"}
    )
    # Commit history is [old, new]; the deployed mapping covers the window but
    # points at the OLD commit, while only the NEWER commit was indexed.
    sha_old = staging.git(repo, "rev-parse", "HEAD~1")
    staging.bind_repo(app, repo)
    staging.register_app(app)
    staging.issue_token(app)
    # The deployed mapping covers the window but points at the OLD commit.
    staging.attest_mapping(app, commit_sha=sha_old)
    # Only the NEWER commit was ever indexed.
    staging.index_snapshot(app.repo_id, sha_new)
    staging.set_mode(alice, app.project_id, "monitor")

    assert staging.ingest(alice, app).status_code == 202
    staging.drain()
    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    # Scout must NOT silently investigate the wrong commit.
    assert inv["scout"]["status"] == INV.skipped_no_snapshot.value
    assert all(i["status"] != INV.executed.value for i in inv.values())
    assert not staging.artifact_rows(app.project_id, kind="llm_transcript")
    report.check(
        "missing snapshot for mapped sha", INV.skipped_no_snapshot.value, inv["scout"]["status"]
    )

    # Once the *mapped* commit is indexed, the next window is investigated —
    # pinned to the mapped sha, not HEAD.
    staging.index_snapshot(app.repo_id, sha_old)
    assert staging.ingest(alice, app).status_code == 202
    staging.drain()
    # A second decision was recorded for the new window; its scout executed.
    decisions = staging.decisions(alice, app.project_id)
    latest = decisions[0]
    scout2 = next(
        i
        for i in staging.invocations(alice, app.project_id)
        if i["decision_id"] == latest["id"] and i["agent"] == "scout"
    )
    assert scout2["status"] == INV.executed.value
    assert latest["correlation"]["commit_sha"] == sha_old
    assert latest["correlation"]["commit_sha"] != sha_new
    report.check(
        "investigated commit == mapped commit", sha_old, decisions[0]["correlation"]["commit_sha"]
    )


# ---------------------------------------------------------------------------
# Scenario D — provider failure after some successful calls (fault injection).
# ---------------------------------------------------------------------------


@pytest.mark.live_defense_fault
def test_d_provider_failure(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-d")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")

    # Scout runs fully; Diagnose gets one successful turn then the provider dies.
    flaky = FailAfterCalls(fake_provider(repo / "target"), agents={"diagnose"}, succeed=1)
    staging.use_gateway(gateway_factory(flaky))

    assert staging.ingest(alice, app).status_code == 202
    staging.drain()

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    assert inv["scout"]["status"] == INV.executed.value
    diagnose = inv["diagnose"]
    assert diagnose["status"] == INV.failed.value
    assert diagnose["execution"]["error_code"] == "LLM_UNAVAILABLE"
    # Partial evidence retained: the failed run still persisted its transcript.
    assert diagnose["execution"]["transcript_artifact_id"]
    meta, transcript = staging.artifact_json(diagnose["execution"]["transcript_artifact_id"])
    assert meta["project_id"] == app.project_id
    report.artifact(
        diagnose["execution"]["transcript_artifact_id"], meta["kind"], "diagnose partial transcript"
    )
    report.check(
        "provider failure recorded", "LLM_UNAVAILABLE", diagnose["execution"]["error_code"]
    )
    # Failure reporting is honest: not faked into a skip or a success.
    assert diagnose["execution"]["evidence_verified"] is False
    assert diagnose["execution"]["confirmation"] == "unverified"
    decisions = staging.decisions(alice, app.project_id)
    assert decisions and decisions[0]["executed"] is False


# ---------------------------------------------------------------------------
# Scenario E — worker failure mid-investigation (fault injection): crash after
# Scout's execution artifacts persist; retry must keep partial evidence and
# recover the execution state idempotently.
# ---------------------------------------------------------------------------


@pytest.mark.live_defense_fault
def test_e_worker_failure(staging, monkeypatch, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-e")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")

    # Count physical provider calls separately from logical invocations —
    # a single logical invocation row does NOT prove a billable loop ran once.
    fake = fake_provider(repo / "target")
    staging.use_gateway(gateway_factory(fake))

    calls = {"scout": 0}
    orig = first_responder.run_investigative

    def crash_after_scout_execution(**kwargs):
        result = orig(**kwargs)
        if kwargs["agent"].value == "scout" and calls["scout"] == 0:
            calls["scout"] += 1
            raise RuntimeError("simulated worker crash after scout execution persisted")
        return result

    monkeypatch.setattr(first_responder, "run_investigative", crash_after_scout_execution)

    assert staging.ingest(alice, app).status_code == 202
    staging.drain()

    # Post-F1 crash contract: the step's artifacts persisted before the crash,
    # but the invocation is committed ``failed`` — a crash during a billable step
    # refuses automatic replay rather than re-running uncertain provider work.
    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    assert inv["scout"]["status"] == INV.failed.value
    step_jobs = [j for j in staging.job_rows() if j["kind"] == "live_defense_agent_step"]
    crashed = next(
        (j for j in step_jobs if "simulated worker crash" in json.dumps(j.get("last_error") or {})),
        None,
    )
    assert crashed is not None, "expected the crashed scout job to record its error"
    report.check(
        "partial evidence retained",
        "crash recorded",
        json.dumps(crashed.get("last_error") or {})[:120],
    )
    scout_physical_before = sum(1 for c in fake.calls if c.agent.value == "scout")

    # Recovery: make the requeued job claimable and drain again.
    staging.requeue_now(crashed["id"])
    staging.drain()

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    assert inv["scout"]["status"] == INV.failed.value
    # The loop's transcript was retained — nothing was lost or deduplicated away.
    transcripts = staging.artifact_rows(app.project_id, kind="llm_transcript")
    assert len(transcripts) >= 1
    # Exactly one decision, one scout invocation — crash + redelivery did not
    # double-record.
    assert len(staging.decisions(alice, app.project_id)) == 1
    assert sum(1 for i in inv.values() if i["agent"] == "scout") == 1
    # Post-F1 contract: redelivery did NOT re-run the physical provider loop —
    # uncertain billable outcomes are never replayed automatically.
    # Scout failed, so Diagnose is committed skipped_no_prerequisite — never
    # enqueued, never billable.
    if "diagnose" in inv:
        assert inv["diagnose"]["status"] == INV.skipped_no_prerequisite.value
    scout_physical_after = sum(1 for c in fake.calls if c.agent.value == "scout")
    assert scout_physical_after == scout_physical_before
    report.check(
        "physical provider calls (scout)",
        "not replayed on redelivery (F1)",
        f"{scout_physical_before} -> {scout_physical_after}",
    )
    report.check("recovery outcome", INV.failed.value, inv["scout"]["status"])


# ---------------------------------------------------------------------------
# Scenario F — OFF-mode interruption during an active investigation (fault).
# ---------------------------------------------------------------------------


@pytest.mark.live_defense_fault
def test_f_off_mode_interruption(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-f")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")

    # Flip mode OFF inside the second model call — mid-investigation.
    fake = fake_provider(repo / "target")
    hooked = HookProvider(
        fake,
        when=2,
        on_call=lambda _req: staging.set_mode(alice, app.project_id, "off"),
    )
    staging.use_gateway(gateway_factory(hooked))

    assert staging.ingest(alice, app).status_code == 202
    staging.drain()

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    assert inv["scout"]["status"] == INV.cancelled.value
    # No further agent step ran after OFF took effect.
    assert inv["diagnose"]["status"] in {INV.cancelled.value, INV.queued.value}
    assert inv["diagnose"]["status"] != INV.executed.value
    # The provider saw no calls beyond the hook point.
    assert len(fake.calls) == 2
    report.check("calls after OFF", "<=2", len(fake.calls), ok=len(fake.calls) <= 2)
    # OFF produced no execution artifacts for diagnose and no control jobs.
    assert not any(j["kind"].startswith("live_defense_execute") for j in staging.job_rows())
    mode = alice.get(f"/api/v1/projects/{app.project_id}/first-responder/mode").json()
    assert mode["intent"] == "off"
    report.check("mode effective", "off", mode["intent"])


# ---------------------------------------------------------------------------
# Scenario G — multi-project isolation: two projects' workflows stay disjoint.
# ---------------------------------------------------------------------------


def test_g_multi_project_isolation(staging, report):
    alice, bob, dave = staging.login("alice"), staging.login("bob"), staging.login("dave")
    carol = staging.login("carol")

    app1 = staging.create_project(alice, bob, name="payments-lite-1")
    repo1, sha1 = staging.checkout_owned_repo("repo-g1")
    staging.bind_repo(app1, repo1)
    staging.index_snapshot(app1.repo_id, sha1)
    staging.register_app(app1, name="staging-1")
    staging.issue_token(app1)
    staging.attest_mapping(app1, commit_sha=sha1)
    staging.set_mode(alice, app1.project_id, "monitor")

    app2 = staging.create_project(alice, dave, name="payments-lite-2")
    repo2, sha2 = staging.checkout_owned_repo(
        "repo-g2", extra_commit={"target/payments_lite/tenant.py": "TENANT = 'g2'\n"}
    )
    staging.bind_repo(app2, repo2)
    staging.index_snapshot(app2.repo_id, sha2)
    staging.register_app(app2, name="staging-2")
    staging.issue_token(app2)
    staging.attest_mapping(app2, commit_sha=sha2)
    staging.set_mode(alice, app2.project_id, "monitor")

    assert staging.ingest(alice, app1).status_code == 202
    assert staging.ingest(alice, app2).status_code == 202
    staging.drain(max_jobs=80)

    d1 = staging.decisions(alice, app1.project_id)
    d2 = staging.decisions(alice, app2.project_id)
    assert len(d1) == 1 and len(d2) == 1
    assert d1[0]["correlation"]["commit_sha"] == sha1
    assert d2[0]["correlation"]["commit_sha"] == sha2
    assert sha1 != sha2
    report.check(
        "project isolation of correlated commits", (sha1, sha2), "distinct", ok=sha1 != sha2
    )

    inv1 = staging.invocations(alice, app1.project_id)
    inv2 = staging.invocations(alice, app2.project_id)
    assert all(i["project_id"] == app1.project_id for i in inv1)
    assert all(i["project_id"] == app2.project_id for i in inv2)
    assert {i["id"] for i in inv1}.isdisjoint({i["id"] for i in inv2})

    a1 = staging.artifact_rows(app1.project_id)
    a2 = staging.artifact_rows(app2.project_id)
    assert a1 and a2
    assert all(a["project_id"] == app1.project_id for a in a1)
    assert all(a["project_id"] == app2.project_id for a in a2)
    assert {a["id"] for a in a1}.isdisjoint({a["id"] for a in a2})

    # Cross-tenant read: carol is not in alice's org -> 404, no existence leak.
    assert (
        carol.get(f"/api/v1/projects/{app1.project_id}/first-responder/invocations").status_code
        == 404
    )
    assert (
        carol.get(f"/api/v1/projects/{app1.project_id}/first-responder/decisions").status_code
        == 404
    )
    report.check("cross-tenant read", 404, 404)


# ---------------------------------------------------------------------------
# Scenario H — adversarial model outputs cannot change authorization or
# evidence-confirmation semantics.
# ---------------------------------------------------------------------------


def test_h_agent_output_manipulation(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-h")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")

    scripts = all_role_scripts(repo / "target")
    scripts[RuntimeAgentName.SCOUT] = adversarial_scout_script()
    fake = fake_provider(repo / "target", scripts=scripts)
    staging.use_gateway(gateway_factory(fake))

    assert staging.ingest(alice, app).status_code == 202
    staging.drain()

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    scout = inv["scout"]
    assert scout["status"] == INV.executed.value
    ex = scout["execution"]
    assert ex["typed_output"] is True
    # None of the injected claims changed semantics.
    assert ex["evidence_verified"] is False
    assert ex["confirmation"] == ConfirmationState.unverified.value
    assert ex["independent_verification_id"] is None
    report.check("adversarial output did not confirm evidence", "unverified", ex["confirmation"])

    decisions = staging.decisions(alice, app.project_id)
    assert decisions[0]["executed"] is False
    assert decisions[0]["confirmation"] == ConfirmationState.unverified.value
    assert decisions[0]["llm_prose_is_not_confirmation"] is True

    # The transcript records the denied tool calls and the schema rejection —
    # the runtime treated model output as untrusted.
    _, transcript = staging.artifact_json(ex["transcript_artifact_id"])
    raw = json.dumps(transcript)
    assert "http__request" in raw  # denied tool call recorded
    assert "SCHEMA_INVALID" in raw  # forged submit rejected by the strict schema
    report.check(
        "denied tool recorded", "http__request in transcript", True, ok="http__request" in raw
    )
    report.check(
        "schema rejection recorded",
        "SCHEMA_INVALID in transcript",
        True,
        ok="SCHEMA_INVALID" in raw,
    )

    # The persisted hypotheses carry no injected authority fields.
    _, output = staging.artifact_json(ex["output_artifact_id"])
    raw_out = json.dumps(output)
    for forged in ("verification_status", "grant_permissions", "decision_override"):
        assert forged not in raw_out
    # Authorization unchanged: still monitor, no execution/control jobs.
    mode = alice.get(f"/api/v1/projects/{app.project_id}/first-responder/mode").json()
    assert mode["intent"] == "monitor"
    assert not any(j["kind"].startswith("live_defense_execute") for j in staging.job_rows())


# ---------------------------------------------------------------------------
# Scenario I — concurrent workers: two workers draining the same queue must
# claim each step exactly once (SKIP LOCKED), never double-execute an agent.
# ---------------------------------------------------------------------------


def test_i_concurrent_workers(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-i")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "assisted_response")

    scripts = all_role_scripts(repo / "target")
    fake = fake_provider(repo / "target", scripts=scripts)
    staging.use_gateway(gateway_factory(fake))

    assert staging.ingest(alice, app).status_code == 202

    errors: list[BaseException] = []

    def run() -> None:
        try:
            staging.drain(max_jobs=60)
        except BaseException as exc:  # noqa: BLE001 - surfaced by assertion below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert all(not t.is_alive() for t in threads), "a worker thread hung"
    assert not errors, f"concurrent drains raised: {errors}"

    decisions = staging.decisions(alice, app.project_id)
    assert len(decisions) == 1
    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    assert inv["scout"]["status"] == INV.executed.value
    assert inv["diagnose"]["status"] == INV.executed.value
    # Exactly one invocation row per agent and exactly the scripted number of
    # provider calls per role — no worker re-ran a claimed step.
    assert sum(1 for i in inv.values() if i["agent"] == "scout") == 1
    assert sum(1 for i in inv.values() if i["agent"] == "diagnose") == 1
    scout_calls = sum(1 for c in fake.calls if c.agent.value == "scout")
    diag_calls = sum(1 for c in fake.calls if c.agent.value == "diagnose")
    assert scout_calls == len(scripts[RuntimeAgentName.SCOUT])
    assert diag_calls == len(scripts[RuntimeAgentName.DIAGNOSE])
    report.check(
        "concurrent drains: single execution",
        (len(scripts[RuntimeAgentName.SCOUT]), len(scripts[RuntimeAgentName.DIAGNOSE])),
        (scout_calls, diag_calls),
    )


# ---------------------------------------------------------------------------
# Scenario J — queue redelivery after a persisted result: re-claiming a
# finished agent-step job must be an idempotent no-op, not a second execution.
# ---------------------------------------------------------------------------


@pytest.mark.live_defense_fault
def test_j_queue_redelivery(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-j")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")

    scripts = all_role_scripts(repo / "target")
    fake = fake_provider(repo / "target", scripts=scripts)
    staging.use_gateway(gateway_factory(fake))

    assert staging.ingest(alice, app).status_code == 202
    staging.drain()

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    assert inv["scout"]["status"] == INV.executed.value
    transcripts_before = len(staging.artifact_rows(app.project_id, kind="llm_transcript"))
    calls_before = len(fake.calls)

    # Force the succeeded scout step job back onto the queue (at-least-once
    # redelivery after a worker/network failure that lost the ack).
    scout_job = next(
        j
        for j in staging.job_rows("live_defense_agent_step")
        if j["status"] == "succeeded" and json.dumps(j["payload"]).find(inv["scout"]["id"]) >= 0
    )
    staging.revive_job(scout_job["id"])
    staging.drain()

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    assert inv["scout"]["status"] == INV.executed.value
    assert len(staging.decisions(alice, app.project_id)) == 1
    # No second model call, no second transcript: the replay was detected.
    assert len(fake.calls) == calls_before
    assert len(staging.artifact_rows(app.project_id, kind="llm_transcript")) == transcripts_before
    report.check(
        "redelivered step idempotent",
        "no new calls/artifacts",
        (len(fake.calls) - calls_before, 0),
    )


# ---------------------------------------------------------------------------
# Scenario K — provider failure after partial model output: a truncated turn
# followed by provider death fails honestly with the partial turn retained.
# ---------------------------------------------------------------------------


@pytest.mark.live_defense_fault
def test_k_provider_failure_after_partial_output(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-k")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")

    scripts = all_role_scripts(repo / "target")
    # Scout's first turn is a truncated, non-submitting partial response; the
    # provider then dies on the follow-up call.
    scripts[RuntimeAgentName.SCOUT] = [
        lambda _req: FakeTurn(
            text='{"hypotheses": [{"title": "ownership check missing on paym',
            stop_reason="max_tokens",
        ),
        *scripts[RuntimeAgentName.SCOUT],
    ]
    flaky = FailAfterCalls(
        fake_provider(repo / "target", scripts=scripts), agents={"scout"}, succeed=1
    )
    staging.use_gateway(gateway_factory(flaky))

    assert staging.ingest(alice, app).status_code == 202
    staging.drain()

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    scout = inv["scout"]
    assert scout["status"] == INV.failed.value
    assert scout["execution"]["error_code"] == "LLM_UNAVAILABLE"
    assert scout["execution"]["evidence_verified"] is False
    assert scout["execution"]["confirmation"] == "unverified"
    # The partial turn was persisted in the transcript, not silently dropped.
    _, transcript = staging.artifact_json(scout["execution"]["transcript_artifact_id"])
    assert "paym" in json.dumps(transcript)
    # Downstream agents are honestly skipped, never fabricated as executed.
    assert inv["diagnose"]["status"] == INV.skipped_no_prerequisite.value
    report.check(
        "partial output + provider failure", "LLM_UNAVAILABLE", scout["execution"]["error_code"]
    )


# ---------------------------------------------------------------------------
# Scenario L — OFF during an active provider call: the operator flips OFF
# inside Diagnose's in-flight call; completed evidence is retained and nothing
# further executes.
# ---------------------------------------------------------------------------


@pytest.mark.live_defense_fault
def test_l_off_during_active_provider_call(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-l")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")

    scripts = all_role_scripts(repo / "target")
    fake = fake_provider(repo / "target", scripts=scripts)
    scout_turns = len(scripts[RuntimeAgentName.SCOUT])
    hooked = HookProvider(
        fake,
        when=scout_turns + 1,  # first Diagnose call — mid-investigation
        on_call=lambda _req: staging.set_mode(alice, app.project_id, "off"),
    )
    staging.use_gateway(gateway_factory(hooked))

    assert staging.ingest(alice, app).status_code == 202
    staging.drain()

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    # Scout completed before OFF; its evidence is retained.
    assert inv["scout"]["status"] == INV.executed.value
    assert inv["scout"]["execution"]["transcript_artifact_id"]
    # Diagnose was interrupted mid-call or never permitted to continue.
    assert inv["diagnose"]["status"] in {INV.cancelled.value, INV.failed.value, INV.queued.value}
    assert inv["diagnose"]["status"] != INV.executed.value
    # No control/execution jobs were created after OFF.
    assert not any(j["kind"].startswith("live_defense_execute") for j in staging.job_rows())
    report.check(
        "OFF during active call",
        "scout retained / diagnose not executed",
        (inv["scout"]["status"], inv["diagnose"]["status"]),
        ok=inv["diagnose"]["status"] != INV.executed.value,
    )


# ---------------------------------------------------------------------------
# Scenario M — stale deployment mapping: telemetry from a window that predates
# the only trusted mapping must not be attributed to that mapping's commit.
# ---------------------------------------------------------------------------


def test_m_stale_deployment_mapping(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-m")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    # The mapping was deployed only minutes ago; the telemetry window is hours old.
    staging.attest_mapping(app, commit_sha=sha, deployed_at=utcnow() - timedelta(minutes=5))
    staging.set_mode(alice, app.project_id, "monitor")

    old_batch = staging.telemetry_batch(utcnow() - timedelta(hours=2))
    resp = staging.ingest(alice, app, old_batch)
    assert resp.status_code == 202, resp.text
    staging.drain()

    decisions = staging.decisions(alice, app.project_id)
    assert decisions and decisions[0]["correlation"]["confidence"] == "unknown"
    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    # Not attributed to sha: no agent may read code for a window the mapping
    # does not cover.
    for agent in ("scout", "diagnose"):
        assert inv[agent]["status"] == INV.skipped_no_mapping.value
    assert not staging.artifact_rows(app.project_id, kind="llm_transcript")
    report.check(
        "pre-mapping window not investigated",
        INV.skipped_no_mapping.value,
        inv["scout"]["status"],
    )


# ---------------------------------------------------------------------------
# Scenario N — artifact corruption / missing artifact: the content-addressed
# store must refuse corrupted or missing bytes instead of serving them.
# ---------------------------------------------------------------------------


@pytest.mark.live_defense_fault
def test_n_artifact_corruption_and_missing(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-n")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")
    assert staging.ingest(alice, app).status_code == 202
    staging.drain()

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    art_id = inv["scout"]["execution"]["transcript_artifact_id"]
    path = staging.artifact_path(art_id)
    original = path.read_bytes()

    # Corrupted bytes fail the sha256 integrity check.
    path.write_bytes(b"tampered-bytes")
    with pytest.raises(OSError, match="integrity"):
        staging.artifact_json(art_id)
    report.check("corrupt artifact refused", "integrity error", "OSError raised")

    # Missing bytes fail loudly as well.
    path.unlink()
    with pytest.raises(FileNotFoundError):
        staging.artifact_json(art_id)
    report.check("missing artifact refused", "FileNotFoundError", "FileNotFoundError raised")

    path.write_bytes(original)
    meta, _data = staging.artifact_json(art_id)  # store recovers cleanly
    assert meta["sha256"]


# ---------------------------------------------------------------------------
# Scenario O — cross-project artifact access: another tenant cannot read this
# project's execution artifacts over the API.
# ---------------------------------------------------------------------------


def test_o_cross_project_artifact_access(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    carol = staging.login("carol")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-o")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")
    assert staging.ingest(alice, app).status_code == 202
    staging.drain()

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    art_id = inv["scout"]["execution"]["transcript_artifact_id"]
    assert alice.get(f"/api/v1/artifacts/{art_id}").status_code == 200
    denied = carol.get(f"/api/v1/artifacts/{art_id}")
    assert denied.status_code == 404
    report.check("cross-tenant artifact read", 404, denied.status_code)


# ---------------------------------------------------------------------------
# Scenario P — duplicate telemetry ingestion: replaying the same window is
# idempotent — one window row, one event, one shadow job, one decision.
# ---------------------------------------------------------------------------


def test_p_duplicate_telemetry(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-p")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")

    batch = staging.telemetry_batch(utcnow())
    assert staging.ingest(alice, app, batch).status_code == 202
    staging.drain()
    windows_after_first = staging.table_count(staging.T.runtime_windows, app.project_id)
    events_after_first = staging.table_count(staging.T.monitoring_events, app.project_id)
    replay = staging.ingest(alice, app, batch)
    assert replay.status_code == 202
    staging.drain()

    # The replayed window is deduped: no new window, no new monitoring events.
    assert staging.table_count(staging.T.runtime_windows, app.project_id) == windows_after_first
    assert staging.table_count(staging.T.monitoring_events, app.project_id) == events_after_first
    assert len(staging.decisions(alice, app.project_id)) == 1
    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    assert inv["scout"]["status"] == INV.executed.value
    # One transcript per executed agent — the replay spawned no second run.
    transcripts = staging.artifact_rows(app.project_id, kind="llm_transcript")
    executed = [i for i in inv.values() if i["status"] == INV.executed.value]
    assert len(transcripts) == len(executed)
    report.check("duplicate window deduped", "1 window/1 decision/1 run", "deduped")


# ---------------------------------------------------------------------------
# Scenario Q — invalid authorization scope: an expired scope is no scope.
# Probe must be refused while read-only agents still run.
# ---------------------------------------------------------------------------


def test_q_invalid_authorization_scope(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob, approve_scope=True)
    repo, sha = staging.checkout_owned_repo("repo-q")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "assisted_response")

    assert staging.expire_scopes(app.project_id) == 1
    assert staging.ingest(alice, app).status_code == 202
    staging.drain()

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    assert inv["scout"]["status"] == INV.executed.value
    assert inv["probe"]["status"] == INV.skipped_no_scope.value
    report.check(
        "expired scope -> probe refused", INV.skipped_no_scope.value, inv["probe"]["status"]
    )


# ---------------------------------------------------------------------------
# Opt-in live-provider check. NOT part of the deterministic suite; requires
# RUN_LIVE_LLM_EVAL=1 and a real key (HACKATL_LIVE_ANTHROPIC_API_KEY), and it
# costs money. Never run in default CI.
# ---------------------------------------------------------------------------


@pytest.mark.live_llm
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_LLM_EVAL") != "1",
    reason="live provider eval is opt-in: set RUN_LIVE_LLM_EVAL=1 and provide a key",
)
def test_live_provider_scout_executes(staging, settings_override, report):
    key = os.environ.get("HACKATL_LIVE_ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("no HACKATL_LIVE_ANTHROPIC_API_KEY provided")
    settings_override(anthropic_api_key=key)
    staging.monkeypatch.setattr(llm, "TRANSPORT_OVERRIDE", None)

    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-live")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")

    assert staging.ingest(alice, app).status_code == 202
    staging.drain()
    scout = staging.by_agent(staging.invocations(alice, app.project_id))["scout"]
    assert scout["status"] == INV.executed.value
    assert scout["provider_mode"] == "live"
    report.check("live scout provider_mode", "live", scout["provider_mode"])


# ---------------------------------------------------------------------------
# Scenario R — OFF suppresses dispatch: an anomalous window ingested while the
# mode is OFF must not start an investigation (no executed agent work).
# ---------------------------------------------------------------------------


@pytest.mark.live_defense_fault
def test_r_off_suppresses_dispatch(staging, report):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-r")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")
    fake = fake_provider(repo / "target")
    staging.use_gateway(gateway_factory(fake))
    assert staging.ingest(alice, app).status_code == 202
    staging.drain()
    first_ids = {d["id"] for d in staging.decisions(alice, app.project_id)}
    calls_before = len(fake.calls)

    staging.set_mode(alice, app.project_id, "off")
    end = utcnow()
    batch = {
        "source": "owned_staging",
        "window_start": (end - timedelta(minutes=1)).isoformat(),
        "window_end": end.isoformat(),
        "routes": [
            {
                "route": "GET /admin/users",
                "requests": 20,
                "status_4xx": 19,
                "auth_failures": 19,
            }
        ],
    }
    r = staging.ingest(alice, app, batch)
    assert r.status_code == 202 and r.json()["anomalies"] >= 1
    staging.drain()

    new_decisions = [
        d for d in staging.decisions(alice, app.project_id) if d["id"] not in first_ids
    ]
    # Dispatch is suppressed: either no decision is recorded at all, or any new
    # decision is cancelled. No agent step may execute for it either way.
    assert all(d["cancelled"] for d in new_decisions)
    new_decision_ids = {d["id"] for d in new_decisions}
    post_off = [
        i
        for i in staging.invocations(alice, app.project_id)
        if i["decision_id"] in new_decision_ids
    ]
    assert all(i["status"] != INV.executed.value for i in post_off)
    # No model call ran after OFF.
    assert len(fake.calls) == calls_before
    report.check(
        "post-OFF window suppressed",
        "no executed work",
        (len(new_decisions), len(fake.calls) - calls_before),
    )
