"""Live Defense Phase 6.5: RL-gated pilot infrastructure. RL stays blocked."""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa
from helpers import setup_project

from backend import jobs as jobq
from backend import worker
from backend.db import jobs, live_controls, transaction
from backend.live_defense.policy import V6_SHA256, ShadowInference
from backend.live_defense.readiness import assess, assess_categories, compute_ready
from backend.live_defense.serving import (
    hosted_report,
    serving_from_inference,
    serving_from_labeled_fixture,
)
from shared.base import utcnow
from shared.ids import IdPrefix, new_id
from shared.live_defense import (
    EvidenceCheckStatus,
    InferenceStatus,
    PilotVerdict,
    RlEvidenceCategory,
)

ROUTE = "GET /payments/{payment_id}"
ALL_KINDS = [
    "rate_limit",
    "block_source",
    "increase_logging",
    "escalate",
]


def _batch(end, requests: int = 12, e4: int = 0, auth: int = 0, principals=None) -> dict:
    route = {
        "route": ROUTE,
        "requests": requests,
        "status_4xx": e4,
        "auth_failures": auth,
    }
    if principals is not None:
        route["principals"] = principals
    return {
        "source": "owned_staging",
        "window_start": (end - timedelta(minutes=1)).isoformat(),
        "window_end": end.isoformat(),
        "routes": [route],
    }


@pytest.fixture
def env(client_factory):
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    outsider = client_factory("mallory")
    other = outsider.post("/api/v1/projects", json={"name": "other-org"})
    assert other.status_code == 201, other.text
    ids["outsider_project"] = other.json()["id"]
    return alice, bob, outsider, ids, client_factory()


def _register(alice, pid, *, gated: bool = False, name: str = "payments staging"):
    res = alice.post(
        f"/api/v1/projects/{pid}/applications",
        json={"name": name, "environment": "owned_test", "rl_gated_pilot": gated},
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_readiness_categories_a_to_j_are_not_pass_and_ready_is_false() -> None:
    assessment = assess()
    letters = [item.letter for item in assessment.categories]
    assert letters == list("ABCDEFGHIJ")
    assert {item.category for item in assessment.categories} == set(RlEvidenceCategory)
    assert all(item.mandatory for item in assessment.categories)
    assert all(item.status is not EvidenceCheckStatus.passed for item in assessment.categories)
    assert assessment.categories[-1].status is EvidenceCheckStatus.not_verified
    assert assessment.ready is False
    assert assessment.verdict is PilotVerdict.infrastructure_ready_rl_blocked
    assert assessment.customer_infra_provisioned is False
    assert assessment.owner_pilot_authorization is False
    assert assessment.phase7_started is False
    assert "CONDITIONAL GO" in assessment.phase6_verdict
    assert assessment.evaluation_plan.pass_by_total_reward is False
    assert assessment.evaluation_plan.thresholds_invented is False
    assert all(metric.threshold is None for metric in assessment.evaluation_plan.metrics)
    names = [metric.name for metric in assessment.evaluation_plan.metrics]
    assert names == ["compromise", "availability", "delegated", "false_disruption", "cost"]
    assert assessment.hosted_inference.v6_passed_new_criteria is False
    assert assessment.hosted_inference.config.auto_serve_newer_research_checkpoints is False
    assert assessment.serving_contract.q_values_are_not_probabilities is True
    assert assessment.serving_contract.uncertainty is None
    assert assessment.serving_contract.checkpoint_sha256 is None


def test_open_prs_unit_tests_and_docs_do_not_make_ready() -> None:
    categories = assess_categories(
        pretend_prs_merged=(388, 394, 396, 407, 410, 411),
        pretend_unit_tests_exist=True,
        pretend_research_docs_exist=True,
    )
    assert compute_ready(categories) is False
    assert all(item.status is not EvidenceCheckStatus.passed for item in categories)


def test_serving_contract_keeps_missing_fields_and_q_is_not_probability() -> None:
    inference = ShadowInference(
        status=InferenceStatus.skipped_unavailable,
        reason="labeled fixture: runner not loaded",
    )
    out = serving_from_inference(inference)
    assert out.checkpoint_sha256 is None
    assert out.top_actions is None
    assert out.q_margin is None
    assert out.uncertainty is None
    assert out.trained_commit_sha is None
    assert out.eval_bundle_sha256 is None
    assert out.q_values_are_not_probabilities is True
    labeled = serving_from_labeled_fixture(
        {
            "inference_status": "skipped_unavailable",
            "abstention_reason": "fixture",
            "invented_probability": 0.99,
        }
    )
    assert labeled.uncertainty is None
    assert labeled.q_margin is None
    dump = labeled.model_dump(mode="json")
    assert "invented_probability" not in dump
    report = hosted_report()
    assert report.config.compatibility_fixture_sha256 == V6_SHA256
    assert report.v6_passed_new_criteria is False
    assert report.config.approved_checkpoint_sha256 is None


def test_rl_readiness_api_is_backend_source_and_tenant_scoped(env) -> None:
    alice, bob, outsider, ids, anon = env
    pid = ids["project"]
    res = alice.get(f"/api/v1/projects/{pid}/rl-readiness")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ready"] is False
    assert body["verdict"] == "INFRASTRUCTURE READY — RL BLOCKED"
    assert [item["letter"] for item in body["categories"]] == list("ABCDEFGHIJ")
    assert all(item["status"] != "PASS" for item in body["categories"])
    assert body["evaluation_plan"]["pass_by_total_reward"] is False
    hidden = outsider.get(f"/api/v1/projects/{pid}/rl-readiness")
    assert hidden.status_code == 404
    anon_res = anon.get(f"/api/v1/projects/{pid}/rl-readiness")
    assert anon_res.status_code in {401, 403, 404}


def test_gated_app_refuses_grant_channel_and_authorize(env) -> None:
    alice, bob, outsider, ids, anon = env
    pid = ids["project"]
    app = _register(alice, pid, gated=True)
    assert app["rl_gated_pilot"] is True
    assert app["shadow_mode"] is True
    grant = alice.post(
        f"/api/v1/projects/{pid}/control-grants",
        json={
            "application_id": app["id"],
            "allowed_kinds": ALL_KINDS,
            "max_duration_s": 300,
            "hourly_budget": 5,
        },
    )
    assert grant.status_code == 403, grant.text
    assert grant.json()["error"]["code"] == "LIVE_CONTROL_DENIED"
    channel = alice.post(
        f"/api/v1/applications/{app['id']}/control-channel",
        json={"port": 18080},
    )
    assert channel.status_code == 403, channel.text
    auth = alice.post(
        f"/api/v1/applications/{app['id']}/control-authorizations",
        json={
            "kind": "increase_logging",
            "target": {"type": "application", "value": app["id"]},
            "duration_s": 30,
        },
    )
    assert auth.status_code == 403, auth.text
    with transaction() as conn:
        assert (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(live_controls)
                .where(live_controls.c.application_id == app["id"])
            ).scalar()
            == 0
        )


def test_enable_fails_closed_until_leftovers_are_removed(env) -> None:
    alice, bob, outsider, ids, anon = env
    pid = ids["project"]
    app = _register(alice, pid, gated=False, name="owned test leftover")
    grant = alice.post(
        f"/api/v1/projects/{pid}/control-grants",
        json={
            "application_id": app["id"],
            "allowed_kinds": ALL_KINDS,
            "max_duration_s": 300,
            "hourly_budget": 5,
        },
    )
    assert grant.status_code == 201, grant.text
    blocked = alice.post(f"/api/v1/applications/{app['id']}/enable-rl-gated-pilot")
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "CONFLICT_STATE"
    assert blocked.json()["error"]["details"]["active_grants"] == 1
    revoked = alice.post(f"/api/v1/control-grants/{grant.json()['id']}/revoke")
    assert revoked.status_code == 200, revoked.text
    enabled = alice.post(f"/api/v1/applications/{app['id']}/enable-rl-gated-pilot")
    assert enabled.status_code == 200, enabled.text
    body = enabled.json()
    assert body["enabled"] is True
    assert body["application"]["rl_gated_pilot"] is True
    assert body["fail_closed"] is False
    later = alice.post(
        f"/api/v1/projects/{pid}/control-grants",
        json={
            "application_id": app["id"],
            "allowed_kinds": ALL_KINDS,
            "max_duration_s": 300,
            "hourly_budget": 5,
        },
    )
    assert later.status_code == 403


def test_worker_refuses_leftover_execute_on_gated_app(env) -> None:
    alice, bob, outsider, ids, anon = env
    pid = ids["project"]
    app = _register(alice, pid, gated=True, name="gated leftover execute")
    control_id = new_id(IdPrefix.lct)
    now = utcnow()
    from shared.live_defense import (
        ControlTarget,
        LiveControl,
        LiveControlKind,
        LiveControlOrigin,
        LiveControlStatus,
        LiveControlTargetType,
    )

    control = LiveControl(
        id=control_id,
        project_id=pid,
        application_id=app["id"],
        kind=LiveControlKind.increase_logging,
        target=ControlTarget(type=LiveControlTargetType.application, value=app["id"]),
        duration_s=30,
        status=LiveControlStatus.authorized,
        origin=LiveControlOrigin.analyst,
        binding_sha256="a" * 64,
        requested_by=alice.me["user"]["id"],
        requested_at=now,
        authorized_at=now,
    )
    with transaction() as conn:
        conn.execute(
            sa.insert(live_controls).values(
                id=control.id,
                project_id=pid,
                application_id=app["id"],
                live_policy_decision_id=None,
                kind=control.kind.value,
                status=control.status.value,
                unresolved=False,
                expires_at=None,
                binding_sha256=control.binding_sha256,
                doc=control.model_dump(mode="json"),
            )
        )
        jobq.enqueue(
            conn,
            "live_defense_execute",
            {"control_id": control.id, "project_id": pid},
            dedupe_key=f"live-control-exec:{control.id}",
        )
    worker.drain(kinds=["live_defense_execute"])
    with transaction() as conn:
        row = (
            conn.execute(sa.select(live_controls).where(live_controls.c.id == control_id))
            .mappings()
            .one()
        )
        stored = LiveControl.model_validate(row["doc"])
        assert stored.status is LiveControlStatus.rejected
        assert stored.denial_reason and "shadow-only" in stored.denial_reason
        job = (
            conn.execute(sa.select(jobs).where(jobs.c.payload["control_id"].astext == control_id))
            .mappings()
            .one()
        )
        assert job["status"] == "succeeded"
        assert job["result"]["denied"] is True
        assert job["result"]["rl_gated_pilot"] is True


def test_gated_ingest_analyst_accept_and_health_stay_recorded_only(env) -> None:
    alice, bob, outsider, ids, anon = env
    pid = ids["project"]
    app = _register(alice, pid, gated=True)
    token = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens").json()["token"]
    now = utcnow()
    ack = anon.post(
        f"/api/v1/ingest/runtime/{pid}",
        json=_batch(
            now,
            requests=20,
            e4=18,
            auth=18,
            principals=[{"principal": "prn_alice01", "cross_tenant": 3, "own": 4}],
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ack.status_code == 202, ack.text
    worker.drain(kinds=["live_defense_shadow"])
    decisions = alice.get(f"/api/v1/projects/{pid}/live-policy-decisions").json()["items"]
    assert decisions
    decision = decisions[0]
    assert decision["enforcement"] == "NOT_EXECUTED"
    assert decision["executed"] is False
    review = alice.post(
        f"/api/v1/live-policy-decisions/{decision['id']}/reviews",
        json={"decision": "accept", "reason": "recorded opinion"},
    )
    assert review.status_code == 201, review.text
    assert review.json()["executes"] is False
    health = alice.get(f"/api/v1/projects/{pid}/defense-health").json()
    assert health["status"] == "unknown"
    assert health["worker_status"] == "unknown"
    assert (
        "healthy" not in health["status_reason"].lower() or "not" in health["status_reason"].lower()
    )
    outsider_events = outsider.get(f"/api/v1/projects/{pid}/events")
    assert outsider_events.status_code == 404
    report = alice.get(f"/api/v1/projects/{pid}/pilot-report").json()
    assert report["shadow_mode"] is True
    assert "infrastructure completeness authorizes a customer pilot" in report["unsupported_claims"]
    ready = alice.get(f"/api/v1/projects/{pid}/rl-readiness").json()
    assert ready["gated_application_count"] >= 1
    assert ready["ready"] is False


def test_evaluation_plan_does_not_invent_thresholds() -> None:
    from backend.live_defense.readiness import evaluation_plan

    plan = evaluation_plan()
    assert plan.frozen_scenarios_required is True
    assert plan.holdout_required is True
    assert plan.independent_seeds_required is True
    assert plan.baselines == ["rules", "fixed_block", "v6"]
    assert plan.per_scenario_required is True
    assert plan.abstention_required is True
    assert plan.uncertainty_required is True
    assert plan.provenance_required is True
    assert plan.threshold_owner == "rl_owner"
    for metric in plan.metrics:
        assert metric.threshold is None
        assert metric.threshold_status == "pending_rl_owner_approval"
