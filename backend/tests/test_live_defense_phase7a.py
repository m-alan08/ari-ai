"""Live Defense Phase 7A: first-responder orchestration, modes, seven-agent gates."""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa
from helpers import setup_project
from pydantic import ValidationError

from backend import jobs as jobq
from backend import worker
from backend.db import jobs, transaction
from backend.live_defense.capabilities import classify, load_registry
from backend.live_defense.orchestrator import OrchestratorFacts, decide
from shared.assessment import AgentName
from shared.base import utcnow
from shared.ids import IdPrefix, new_id
from shared.live_defense import (
    CONTROL_JOB_KINDS,
    FIRST_RESPONDER_JOB_KINDS,
    SEVEN_AGENT_NAMES,
    AgentCapabilityClass,
    AgentInvocationStatus,
    AnalystHandoff,
    ConfirmationState,
    CorrelationConfidence,
    EvidenceLifecycleState,
    FirstResponderIntent,
    FirstResponderModeUpdate,
    OrchestratorAction,
    parse_first_responder_intent,
)

ROUTE = "GET /payments/{payment_id}"


def _batch(end, requests: int = 20, e4: int = 18, auth: int = 18) -> dict:
    return {
        "source": "owned_staging",
        "window_start": (end - timedelta(minutes=1)).isoformat(),
        "window_end": end.isoformat(),
        "routes": [
            {
                "route": ROUTE,
                "requests": requests,
                "status_4xx": e4,
                "auth_failures": auth,
            }
        ],
    }


def _register(alice, pid: str, name: str = "payments staging") -> dict:
    app = alice.post(
        f"/api/v1/projects/{pid}/applications",
        json={"name": name, "environment": "owned_test"},
    )
    assert app.status_code == 201, app.text
    return app.json()


def test_capability_registry_matches_real_agent_specs() -> None:
    registry = load_registry()
    assert registry.complete is True
    names = {item.name for item in registry.agents}
    assert names == set(SEVEN_AGENT_NAMES)
    by_name = {item.name: item for item in registry.agents}
    assert by_name[AgentName.scout].capability_class is AgentCapabilityClass.investigative_readonly
    assert by_name[AgentName.trace].capability_class is AgentCapabilityClass.investigative_readonly
    assert (
        by_name[AgentName.diagnose].capability_class is AgentCapabilityClass.investigative_readonly
    )
    assert (
        by_name[AgentName.fortify].capability_class is AgentCapabilityClass.investigative_readonly
    )
    assert by_name[AgentName.probe].capability_class is AgentCapabilityClass.disruptive_scoped
    assert by_name[AgentName.probe].network == "target-only"
    assert by_name[AgentName.patch].capability_class is AgentCapabilityClass.code_changing
    assert by_name[AgentName.patch].write_access == "worktree"
    assert by_name[AgentName.patch].silent_customer_deploy is False
    assert by_name[AgentName.verify].capability_class is AgentCapabilityClass.disruptive_scoped
    assert by_name[AgentName.verify].write_access == "none"
    assert (
        classify(network="none", write_access="none") is AgentCapabilityClass.investigative_readonly
    )


def test_parse_intent_aliases_and_rejects_auto_execute() -> None:
    assert parse_first_responder_intent("shadow") is FirstResponderIntent.monitor
    assert parse_first_responder_intent("MONITOR") is FirstResponderIntent.monitor
    assert parse_first_responder_intent("assisted") is FirstResponderIntent.assisted_response
    with pytest.raises(ValueError, match="AUTO-EXECUTE"):
        parse_first_responder_intent("AUTO-EXECUTE")
    with pytest.raises(ValueError, match="AUTO-EXECUTE"):
        parse_first_responder_intent("auto_execute")
    with pytest.raises(ValidationError):
        FirstResponderModeUpdate.model_validate({"intent": "auto_execute"})
    with pytest.raises(ValueError):
        parse_first_responder_intent("unknown-mode")


def test_handoff_delivered_requires_provider() -> None:
    with pytest.raises(ValidationError, match="provider confirmation"):
        AnalystHandoff.model_validate(
            {
                "id": "ahl_01JFIXTURE0000000000000AHL",
                "project_id": "prj_01JFIXTURE0000000000000PRJ",
                "decision_id": "frd_01JFIXTURE0000000000000FRD",
                "state": "delivered",
                "title": "x",
                "summary": "y",
                "created_by": "usr_01JFIXTURE0000000000000USR",
                "created_at": utcnow().isoformat(),
            }
        )


def test_confirmed_requires_threat_flag() -> None:
    with pytest.raises(ValidationError):
        AnalystHandoff.model_validate(
            {
                "id": "ahl_01JFIXTURE0000000000000AHL",
                "project_id": "prj_01JFIXTURE0000000000000PRJ",
                "decision_id": "frd_01JFIXTURE0000000000000FRD",
                "state": "confirmed",
                "confirmation": "confirmed",
                "threat_confirmed": False,
                "title": "x",
                "summary": "y",
                "created_by": "usr_01JFIXTURE0000000000000USR",
                "created_at": utcnow().isoformat(),
            }
        )


def _facts(**overrides: object) -> OrchestratorFacts:
    base = dict(
        project_id="prj_01JFIXTURE0000000000000PRJ",
        application_id="app_01JFIXTURE0000000000000APP",
        intent=FirstResponderIntent.monitor,
        effective=FirstResponderIntent.monitor,
        application_connected=True,
        telemetry_authenticated=True,
        secret_like=False,
        trusted_mapping=False,
        mapped_commit=None,
        mapping_id=None,
        mapping_fresh=False,
        authorized_scope_active=False,
        remediation_approved=False,
        patch_candidate_id=None,
        kill_switch_active=False,
        budget_usd_remaining=3.0,
        deadline_passed=False,
        already_decided_id=None,
        retry_count=0,
        loop_count=0,
        simulated=False,
    )
    base.update(overrides)
    return OrchestratorFacts(**base)  # type: ignore[arg-type]


def test_orchestrator_fail_closed_and_unknown_correlation() -> None:
    off = decide(_facts(effective=FirstResponderIntent.off))
    assert off.action is OrchestratorAction.cancel_run
    assert off.executed is False
    assert off.auto_execute is False
    untrusted = decide(_facts(telemetry_authenticated=False))
    assert untrusted.action is OrchestratorAction.ignore
    secret = decide(_facts(secret_like=True))
    assert secret.action is OrchestratorAction.ignore
    unknown = decide(_facts())
    assert unknown.correlation.confidence is CorrelationConfidence.unknown
    assert unknown.action is OrchestratorAction.correlate
    assert unknown.confirmation is ConfirmationState.unverified
    assert unknown.predicted_probability is None
    assert unknown.deadline_at is not None
    auto = decide(_facts(auto_execute_requested=True))
    assert auto.action is OrchestratorAction.reject_auto_execute
    looped = decide(_facts(loop_count=1))
    assert looped.action is OrchestratorAction.cancel_run
    assert looped.cancel_reason == "loop prevention"
    # Kill-switch is stop-enforcement, not stop-monitoring.
    still_watching = decide(_facts(kill_switch_active=True))
    assert still_watching.action is OrchestratorAction.correlate
    assert still_watching.cancelled is False


def test_orchestrator_probe_and_patch_gates() -> None:
    probe_blocked = decide(
        _facts(
            intent=FirstResponderIntent.assisted_response,
            effective=FirstResponderIntent.assisted_response,
            trusted_mapping=True,
            mapped_commit="b" * 40,
            mapping_id="dcm_01JFIXTURE0000000000000DCM",
            mapping_fresh=True,
            authorized_scope_active=False,
        )
    )
    assert AgentName.probe not in probe_blocked.selected_agents
    assert any(b.name is AgentName.probe for b in probe_blocked.blocked_agents)
    probe_ok = decide(
        _facts(
            intent=FirstResponderIntent.assisted_response,
            effective=FirstResponderIntent.assisted_response,
            trusted_mapping=True,
            mapped_commit="b" * 40,
            mapping_id="dcm_01JFIXTURE0000000000000DCM",
            mapping_fresh=True,
            authorized_scope_active=True,
        )
    )
    assert AgentName.probe in probe_ok.selected_agents
    assert probe_ok.action is OrchestratorAction.confirm_with_probe
    patch_blocked = decide(
        _facts(
            intent=FirstResponderIntent.assisted_response,
            effective=FirstResponderIntent.assisted_response,
            trusted_mapping=True,
            mapped_commit="b" * 40,
            mapping_id="dcm_01JFIXTURE0000000000000DCM",
            mapping_fresh=True,
            authorized_scope_active=True,
            remediation_approved=False,
        )
    )
    assert AgentName.patch not in patch_blocked.selected_agents


def test_mode_defaults_off_and_rejects_auto_execute(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    mode = alice.get(f"/api/v1/projects/{pid}/first-responder/mode")
    assert mode.status_code == 200, mode.text
    assert mode.json()["intent"] == "off"
    assert mode.json()["auto_execute"] is False
    denied = alice.post(
        f"/api/v1/projects/{pid}/first-responder/mode",
        json={"intent": "auto_execute"},
    )
    assert denied.status_code in {400, 422}, denied.text
    rejected = alice.post(f"/api/v1/projects/{pid}/first-responder/auto-execute")
    assert rejected.status_code == 404
    outsider = client_factory("mallory")
    outsider.post("/api/v1/projects", json={"name": "other"})
    assert (
        outsider.post(
            f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"}
        ).status_code
        == 404
    )
    viewer = client_factory("viewer")
    from helpers import add_member

    add_member(ids["org"], viewer.me["user"]["id"], role="member")
    assert (
        viewer.post(
            f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"}
        ).status_code
        == 403
    )


def test_shadow_alias_monitor_and_org_intent_not_effective(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    app = _register(alice, pid)
    set_mode = alice.post(
        f"/api/v1/projects/{pid}/first-responder/mode",
        json={"intent": "shadow"},
    )
    assert set_mode.status_code == 200, set_mode.text
    assert set_mode.json()["intent"] == "monitor"
    perms = alice.get(f"/api/v1/applications/{app['id']}/first-responder/permissions")
    assert perms.status_code == 200, perms.text
    body = perms.json()
    assert body["intent"] == "monitor"
    assert body["org_intent_is_not_app_permissions"] is True
    assert body["trusted_mapping"] is False
    alice.post(f"/api/v1/applications/{app['id']}/disconnect")
    disconnected = alice.get(f"/api/v1/applications/{app['id']}/first-responder/permissions")
    assert disconnected.json()["effective"] == "off"
    assert disconnected.json()["application_connected"] is False


def test_off_cancels_first_responder_jobs_not_control_jobs(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    with transaction() as conn:
        jobq.enqueue(
            conn,
            "live_defense_first_responder",
            {"project_id": pid, "application_id": app["id"], "window_end": utcnow().isoformat()},
            dedupe_key=f"fr-test-{pid}",
        )
        jobq.enqueue(
            conn,
            "live_defense_execute",
            {"project_id": pid, "control_id": "lct_leftover"},
            dedupe_key=f"ctl-test-{pid}",
        )
    stopped = alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "off"})
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["stop_kind"] == "stop_monitoring"
    assert stopped.json()["retention_policy_unchanged"] is True
    assert stopped.json()["cancelled_first_responder_jobs"] >= 1
    with transaction() as conn:
        rows = conn.execute(sa_select_jobs()).mappings().all()
    by_kind = {row["kind"]: row["status"] for row in rows}
    assert by_kind["live_defense_first_responder"] == "cancelled"
    assert by_kind["live_defense_execute"] == "queued"
    assert "live_defense_execute" in CONTROL_JOB_KINDS
    assert "live_defense_first_responder" in FIRST_RESPONDER_JOB_KINDS


def sa_select_jobs():
    import sqlalchemy as sa

    return sa.select(jobs.c.kind, jobs.c.status, jobs.c.cancel_requested)


def test_ingest_does_not_auto_execute_in_assisted_mode(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    app = _register(alice, pid)
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    alice.post(
        f"/api/v1/projects/{pid}/first-responder/mode",
        json={"intent": "assisted_response"},
    )
    now = utcnow()
    ack = alice.post(
        f"/api/v1/ingest/runtime/{pid}",
        json=_batch(now),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ack.status_code == 202, ack.text
    worker.drain(max_jobs=20, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    decisions = alice.get(f"/api/v1/projects/{pid}/first-responder/decisions")
    assert decisions.status_code == 200, decisions.text
    items = decisions.json()["items"]
    assert items, decisions.text
    row = items[0]
    assert row["executed"] is False
    assert row["auto_execute"] is False
    assert row["llm_prose_is_not_confirmation"] is True
    assert row["confirmation"] == "unverified"
    assert row["correlation"]["confidence"] == "unknown"
    assert row["correlation"]["finding_is_not_proof_of_this_request"] is True
    assert row["predicted_probability"] is None
    with transaction() as conn:
        execute = list(
            conn.execute(
                __import__("sqlalchemy")
                .select(jobs.c.kind)
                .where(jobs.c.kind == "live_defense_execute")
            ).scalars()
        )
    assert execute == []
    caps = alice.get(f"/api/v1/projects/{pid}/first-responder/capabilities").json()
    assert caps["complete"] is True
    assert {a["name"] for a in caps["agents"]} == {n.value for n in SEVEN_AGENT_NAMES}


def test_mapping_and_handoff_delivery_ack_labeled_synthetic_provider(client_factory) -> None:
    """SYNTHETIC PROVIDER ACK (test-only). Production delivery stays pending until a genuine provider receipt."""
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    app = _register(alice, pid)
    alice.post(
        f"/api/v1/projects/{pid}/first-responder/mode",
        json={"intent": "assisted_response"},
    )
    mapping = alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": ids["repo"],
            "commit_sha": "b" * 40,
            "deployed_at": utcnow().isoformat(),
            "source": "owner_attested",
        },
    )
    assert mapping.status_code == 201, mapping.text
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    now = utcnow()
    assert (
        alice.post(
            f"/api/v1/ingest/runtime/{pid}",
            json=_batch(now + timedelta(minutes=1)),
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 202
    )
    worker.drain(max_jobs=30, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    handoffs = alice.get(f"/api/v1/projects/{pid}/first-responder/handoffs").json()["items"]
    assert handoffs, "assisted mode should open an analyst handoff"
    item = handoffs[0]
    assert item["state"] != "delivered"
    assert item["confirmation"] == "unverified"
    assert item["threat_confirmed"] is False
    confirm = alice.post(f"/api/v1/first-responder/handoffs/{item['id']}/confirm")
    assert confirm.status_code == 409
    ack = alice.post(
        f"/api/v1/first-responder/handoffs/{item['id']}/delivery-ack",
        json={
            "provider": "labeled-synthetic-test-inbox",
            "provider_message_id": "synthetic-msg-1",
            "received_at": utcnow().isoformat(),
        },
    )
    assert ack.status_code == 200, ack.text
    assert ack.json()["state"] == "delivered"
    assert ack.json()["delivery_provider"] == "labeled-synthetic-test-inbox"
    invocations = alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
    probe = [i for i in invocations if i["agent"] == "probe"]
    assert probe
    assert probe[0]["status"] in {"queued", "skipped_no_scope", "completed_unverified", "blocked"}
    patch = [i for i in invocations if i["agent"] == "patch"]
    if patch:
        assert patch[0]["worktree_only"] is True
        assert patch[0]["silent_deploy"] is False


def test_no_unrestricted_shell_or_prod_env_in_registry() -> None:
    registry = load_registry()
    for item in registry.agents:
        assert "shell" not in item.tools
        assert "unrestricted" not in " ".join(item.tools)
        if item.name is AgentName.probe:
            assert item.network == "target-only"
        if item.name is AgentName.patch:
            assert item.write_access == "worktree"


def test_verify_eligibility_is_not_verified_evidence() -> None:
    eligible = decide(
        _facts(
            intent=FirstResponderIntent.assisted_response,
            effective=FirstResponderIntent.assisted_response,
            trusted_mapping=True,
            mapped_commit="b" * 40,
            mapping_id="dcm_01JFIXTURE0000000000000DCM",
            mapping_fresh=True,
            patch_candidate_id="pat_01JFIXTURE0000000000000PAT",
        )
    )
    assert AgentName.verify in eligible.selected_agents
    assert eligible.action is OrchestratorAction.verify_independently
    assert eligible.evidence_state is EvidenceLifecycleState.recommended
    assert eligible.transition == "verify_eligible_not_executed"
    executed = decide(
        _facts(
            intent=FirstResponderIntent.assisted_response,
            effective=FirstResponderIntent.assisted_response,
            trusted_mapping=True,
            mapped_commit="b" * 40,
            mapping_id="dcm_01JFIXTURE0000000000000DCM",
            mapping_fresh=True,
            patch_candidate_id="pat_01JFIXTURE0000000000000PAT",
            independent_verification_id="ver_01JFIXTURE0000000000000VER",
        )
    )
    assert executed.evidence_state is EvidenceLifecycleState.verified
    assert executed.transition == "independent_verification_executed"


def test_independent_verification_lookup_ignores_patch_eligibility(client_factory) -> None:
    from backend.db import assessments, findings, patch_candidates, verification_results
    from backend.live_defense.first_responder import _independent_verification

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    asm = new_id(IdPrefix.asm)
    fnd = new_id(IdPrefix.fnd)
    pat = new_id(IdPrefix.pat)
    ver = new_id(IdPrefix.ver)
    with transaction() as conn:
        assert _independent_verification(conn, pid) is None
        conn.execute(
            sa.insert(assessments).values(
                id=asm,
                project_id=pid,
                repo_id=ids["repo"],
                scope_id=ids["scope"],
                commit_sha="b" * 40,
                trigger="manual",
                state="CREATED",
                doc={"id": asm},
            )
        )
        conn.execute(
            sa.insert(findings).values(
                id=fnd,
                project_id=pid,
                assessment_id=asm,
                kind="security",
                status="open",
                paths=[],
                doc={"id": fnd},
            )
        )
        conn.execute(sa.insert(patch_candidates).values(id=pat, finding_id=fnd, doc={"id": pat}))
        assert _independent_verification(conn, pid) is None
        conn.execute(
            sa.insert(verification_results).values(
                id=ver,
                patch_id=pat,
                verdict="accepted",
                doc={"id": ver, "verdict": "accepted"},
            )
        )
        assert _independent_verification(conn, pid) == ver


def test_completed_unverified_verify_does_not_mark_evidence_verified(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    app = _register(alice, pid)
    alice.post(
        f"/api/v1/projects/{pid}/first-responder/mode",
        json={"intent": "assisted_response"},
    )
    assert (
        alice.post(
            f"/api/v1/applications/{app['id']}/deploy-mappings",
            json={
                "repo_id": ids["repo"],
                "commit_sha": "b" * 40,
                "deployed_at": utcnow().isoformat(),
                "source": "owner_attested",
            },
        ).status_code
        == 201
    )
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    now = utcnow()
    assert (
        alice.post(
            f"/api/v1/ingest/runtime/{pid}",
            json=_batch(now),
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 202
    )
    worker.drain(max_jobs=40, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    decisions = alice.get(f"/api/v1/projects/{pid}/first-responder/decisions").json()["items"]
    assert decisions
    assert all(row["evidence_state"] != "verified" for row in decisions)
    invocations = alice.get(f"/api/v1/projects/{pid}/first-responder/invocations").json()["items"]
    verify_rows = [item for item in invocations if item["agent"] == "verify"]
    for item in verify_rows:
        assert item["status"] != "verified"
        assert item["status"] in {
            "queued",
            "skipped_no_patch",
            "completed_unverified",
            "blocked",
            "skipped_mode",
            "cancelled",
        }


def test_handoff_stays_pending_without_genuine_provider_ack(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    app = _register(alice, pid)
    alice.post(
        f"/api/v1/projects/{pid}/first-responder/mode",
        json={"intent": "assisted_response"},
    )
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    now = utcnow()
    assert (
        alice.post(
            f"/api/v1/ingest/runtime/{pid}",
            json=_batch(now),
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 202
    )
    worker.drain(max_jobs=30, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    handoffs = alice.get(f"/api/v1/projects/{pid}/first-responder/handoffs").json()["items"]
    assert handoffs
    item = handoffs[0]
    assert item["state"] == "delivery_pending"
    assert item["delivery_provider"] is None
    assert item["delivery_confirmed_at"] is None
    assert item["confirmation"] == "unverified"


def test_mapping_uses_telemetry_window_not_newest(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    now = utcnow()
    older = alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": ids["repo"],
            "commit_sha": "a" * 40,
            "deployed_at": (now - timedelta(hours=3)).isoformat(),
            "source": "owner_attested",
        },
    )
    newer = alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={
            "repo_id": ids["repo"],
            "commit_sha": "c" * 40,
            "deployed_at": now.isoformat(),
            "source": "owner_attested",
        },
    )
    assert older.status_code == 201 and newer.status_code == 201, (older.text, newer.text)
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    window = now - timedelta(hours=1)
    assert (
        alice.post(
            f"/api/v1/ingest/runtime/{pid}",
            json=_batch(window),
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 202
    )
    worker.drain(max_jobs=30, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    row = alice.get(f"/api/v1/projects/{pid}/first-responder/decisions").json()["items"][0]
    assert row["correlation"]["commit_sha"] == "a" * 40
    assert row["correlation"]["confidence"] != "unknown"
    assert row["correlation"]["mapping_id"] == older.json()["id"]


def test_mapping_after_window_is_unknown(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    now = utcnow()
    assert (
        alice.post(
            f"/api/v1/applications/{app['id']}/deploy-mappings",
            json={
                "repo_id": ids["repo"],
                "commit_sha": "d" * 40,
                "deployed_at": now.isoformat(),
                "source": "owner_attested",
            },
        ).status_code
        == 201
    )
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    assert (
        alice.post(
            f"/api/v1/ingest/runtime/{pid}",
            json=_batch(now - timedelta(hours=2)),
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 202
    )
    worker.drain(max_jobs=30, kinds=["live_defense_shadow", *FIRST_RESPONDER_JOB_KINDS])
    row = alice.get(f"/api/v1/projects/{pid}/first-responder/decisions").json()["items"][0]
    assert row["correlation"]["confidence"] == "unknown"
    assert row["correlation"]["commit_sha"] is None
    assert row["correlation"]["mapping_id"] is None


def test_off_cancels_queued_agent_steps_and_skips_claim(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    with transaction() as conn:
        jobq.enqueue(
            conn,
            "live_defense_agent_step",
            {
                "project_id": pid,
                "decision_id": "frd_queued",
                "invocation_id": "fri_queued",
                "agent": "scout",
                "capability_class": "investigative_readonly",
            },
            dedupe_key=f"fr-agent-queued-{pid}",
        )
    stopped = alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "off"})
    assert stopped.status_code == 200, stopped.text
    drained = worker.drain(max_jobs=10, kinds=["live_defense_agent_step"])
    assert drained == []
    with transaction() as conn:
        rows = conn.execute(sa_select_jobs()).mappings().all()
    by_kind = {row["kind"]: row for row in rows if row["kind"] == "live_defense_agent_step"}
    assert by_kind["live_defense_agent_step"]["status"] == "cancelled"
    assert by_kind["live_defense_agent_step"]["cancel_requested"] is True


def test_off_aborts_running_agent_step_at_execution_boundary(client_factory) -> None:
    from backend.db import first_responder_decisions, first_responder_invocations
    from backend.live_defense.first_responder import live_defense_agent_step
    from backend.worker import JobCancelled, JobContext
    from shared.live_defense import FirstResponderInvocation

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "monitor"})
    decision = decide(_facts(project_id=pid, application_id=None))
    inv = FirstResponderInvocation(
        id=new_id(IdPrefix.fri),
        decision_id=decision.id,
        project_id=pid,
        agent=AgentName.scout,
        capability_class=AgentCapabilityClass.investigative_readonly,
        status=AgentInvocationStatus.queued,
        created_at=utcnow(),
    )
    with transaction() as conn:
        conn.execute(
            sa.insert(first_responder_decisions).values(
                id=decision.id,
                project_id=pid,
                application_id=None,
                dedupe_key=f"off-race-{pid}",
                action=decision.action.value,
                doc=decision.model_dump(mode="json"),
            )
        )
        conn.execute(sa_insert_invocation(inv))
        job_id, _ = jobq.enqueue(
            conn,
            "live_defense_agent_step",
            {
                "project_id": pid,
                "decision_id": inv.decision_id,
                "invocation_id": inv.id,
                "agent": "scout",
                "capability_class": "investigative_readonly",
            },
            dedupe_key=f"fr-agent-running-{pid}",
        )
    with transaction() as conn:
        conn.execute(sa.update(jobs).where(jobs.c.id == job_id).values(run_after=sa.func.now()))
        claimed = jobq.claim(conn, "fr-test-worker", 30, kinds=["live_defense_agent_step"])
    assert claimed is not None and claimed.id == job_id
    stopped = alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "off"})
    assert stopped.status_code == 200, stopped.text
    ctx = JobContext(job=claimed, owner="fr-test-worker")
    with pytest.raises(JobCancelled):
        live_defense_agent_step(ctx)
    with transaction() as conn:
        row = (
            conn.execute(
                sa.select(first_responder_invocations).where(
                    first_responder_invocations.c.id == inv.id
                )
            )
            .mappings()
            .one()
        )
    body = FirstResponderInvocation.model_validate(row["doc"])
    assert body.status is AgentInvocationStatus.cancelled
    assert row["status"] == "cancelled"


def test_process_after_off_does_not_enqueue_new_agent_steps(client_factory) -> None:
    from backend.live_defense.first_responder import process_first_responder

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    app = _register(alice, pid)
    alice.post(f"/api/v1/projects/{pid}/first-responder/mode", json={"intent": "off"})
    result = process_first_responder(
        pid,
        application_id=app["id"],
        window_end=utcnow(),
        source="owned_staging",
        live_policy_decision_id=None,
        event_ids=[],
    )
    assert result["action"] == "cancel_run"
    with transaction() as conn:
        kinds = list(
            conn.execute(
                sa.select(jobs.c.kind).where(jobs.c.kind == "live_defense_agent_step")
            ).scalars()
        )
    assert kinds == []


def sa_insert_invocation(inv):
    from backend.db import first_responder_invocations

    return sa.insert(first_responder_invocations).values(
        id=inv.id,
        decision_id=inv.decision_id,
        project_id=inv.project_id,
        agent=inv.agent.value,
        status=inv.status.value,
        doc=inv.model_dump(mode="json"),
    )
