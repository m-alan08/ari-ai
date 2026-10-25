"""Live Defense observability: investigation list/timeline, invocation detail,
diagnostics. Read-only, project-scoped, bounded."""

from __future__ import annotations

import sqlalchemy as sa
from helpers import setup_project

from backend import jobs as jobq
from backend.db import (
    first_responder_decisions,
    first_responder_invocations,
    jobs,
    transaction,
)
from shared.assessment import AgentName
from shared.base import utcnow
from shared.ids import IdPrefix, new_id
from shared.live_defense import (
    AgentCapabilityClass,
    AgentInvocationStatus,
    ConfirmationState,
    CorrelationConfidence,
    EvidenceLifecycleState,
    FirstResponderDecision,
    FirstResponderIntent,
    FirstResponderInvocation,
    OrchestratorAction,
    RuntimeCodeCorrelation,
)


def _decision(project_id: str, *, cancelled: bool = False) -> FirstResponderDecision:
    return FirstResponderDecision(
        id=new_id(IdPrefix.frd),
        project_id=project_id,
        created_at=utcnow(),
        intent_mode=FirstResponderIntent.monitor,
        effective_mode=FirstResponderIntent.monitor,
        action=OrchestratorAction.investigate,
        evidence_state=EvidenceLifecycleState.suspected,
        trigger_event_ids=[],
        selected_agents=[AgentName.scout, AgentName.diagnose],
        blocked_agents=[],
        correlation=RuntimeCodeCorrelation(
            confidence=CorrelationConfidence.unknown,
            note="test",
        ),
        confirmation=ConfirmationState.unverified,
        transition="test",
        cancelled=cancelled,
        cancel_reason="mode_off" if cancelled else None,
    )


def _invocation(
    decision: FirstResponderDecision,
    *,
    status: AgentInvocationStatus,
    job_id: str | None = None,
    execution: dict | None = None,
    inv_id: str | None = None,
) -> FirstResponderInvocation:
    inv = FirstResponderInvocation(
        id=inv_id or new_id(IdPrefix.fri),
        decision_id=decision.id,
        project_id=decision.project_id,
        agent=AgentName.scout,
        capability_class=AgentCapabilityClass.investigative_readonly,
        status=status,
        job_id=job_id,
        created_at=utcnow(),
    )
    if execution is not None:
        doc = inv.model_dump(mode="json")
        doc["execution"] = execution
        return doc  # caller inserts raw doc
    return inv


def _insert_decision(conn, decision: FirstResponderDecision) -> None:
    conn.execute(
        sa.insert(first_responder_decisions).values(
            id=decision.id,
            project_id=decision.project_id,
            application_id=decision.application_id,
            dedupe_key=f"inv-test-{decision.id}",
            action=decision.action.value,
            doc=decision.model_dump(mode="json"),
        )
    )


def _insert_invocation(conn, inv) -> None:
    doc = inv if isinstance(inv, dict) else inv.model_dump(mode="json")
    conn.execute(
        sa.insert(first_responder_invocations).values(
            id=doc["id"],
            decision_id=doc["decision_id"],
            project_id=doc["project_id"],
            agent=doc["agent"],
            status=doc["status"],
            doc=doc,
        )
    )


def _seed(client_factory, *, status=AgentInvocationStatus.queued, with_job=True):
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    decision = _decision(pid)
    with transaction() as conn:
        _insert_decision(conn, decision)
        job_id = None
        inv_id = new_id(IdPrefix.fri)
        if with_job:
            job_id, _ = jobq.enqueue(
                conn,
                "live_defense_agent_step",
                {
                    "project_id": pid,
                    "decision_id": decision.id,
                    "invocation_id": inv_id,
                    "agent": "scout",
                    "capability_class": "investigative_readonly",
                },
                dedupe_key=f"fr-agent-{new_id(IdPrefix.fri)}",
            )
        inv = _invocation(decision, status=status, job_id=job_id, inv_id=inv_id)
        _insert_invocation(conn, inv)
    return alice, pid, decision, inv


def test_investigations_requires_auth(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    anon = client_factory("carol")
    r = anon.get(f"/api/v1/projects/{pid}/first-responder/investigations")
    assert r.status_code in (403, 404), r.text


def test_investigations_cross_project_denied(client_factory) -> None:
    alice, pid, decision, _ = _seed(client_factory)
    other = client_factory("other")
    r = other.get(f"/api/v1/projects/{pid}/first-responder/investigations")
    assert r.status_code in (403, 404)
    r2 = other.get(f"/api/v1/first-responder/investigations/{decision.id}")
    assert r2.status_code in (403, 404)


def test_investigations_list_and_timeline(client_factory) -> None:
    alice, pid, decision, inv = _seed(client_factory)
    r = alice.get(f"/api/v1/projects/{pid}/first-responder/investigations")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["decision_id"] == decision.id
    assert items[0]["invocations_total"] == 1
    assert items[0]["invocations_queued_or_running"] == 1

    t = alice.get(f"/api/v1/first-responder/investigations/{decision.id}")
    assert t.status_code == 200, t.text
    body = t.json()
    assert body["decision"]["id"] == decision.id
    assert body["invocations"][0]["invocation"]["id"] == inv.id
    assert body["invocations"][0]["job"]["job_id"] is not None
    assert body["invocations"][0]["execution"] is None
    assert "not a confirmed compromise" in body["caveat"]


def test_investigation_missing_and_invalid_refs(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    r = alice.get("/api/v1/first-responder/investigations/frd_doesnotexist")
    assert r.status_code == 404
    r2 = alice.get("/api/v1/first-responder/invocations/fri_doesnotexist")
    assert r2.status_code == 404
    r3 = alice.get(f"/api/v1/projects/{pid}/first-responder/investigations?cursor=not-a-cursor")
    assert r3.status_code in (400, 422)


def test_investigations_pagination_bounded(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    pid = ids["project"]
    with transaction() as conn:
        for _ in range(5):
            _insert_decision(conn, _decision(pid))
    page1 = alice.get(f"/api/v1/projects/{pid}/first-responder/investigations?limit=2").json()
    assert len(page1["items"]) == 2
    assert page1["next_cursor"]
    page2 = alice.get(
        f"/api/v1/projects/{pid}/first-responder/investigations",
        params={"limit": 2, "cursor": page1["next_cursor"]},
    ).json()
    assert len(page2["items"]) == 2
    ids1 = {i["decision_id"] for i in page1["items"]}
    ids2 = {i["decision_id"] for i in page2["items"]}
    assert not ids1 & ids2


def test_invocation_detail_joins_job_and_execution(client_factory) -> None:
    alice, pid, decision, _ = _seed(client_factory)
    with transaction() as conn:
        inv2 = _invocation(
            decision,
            status=AgentInvocationStatus.completed_unverified,
            execution={
                "provider_mode": "fake",
                "model_id": "claude-haiku-5-5",
                "cost_usd": 0.0042,
                "latency_ms": 1200,
                "llm_turns": 2,
                "tool_call_count": 3,
                "error_code": None,
            },
        )
        _insert_invocation(conn, inv2)
    r = alice.get(f"/api/v1/first-responder/invocations/{inv2['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["execution_recorded"] is True
    assert body["execution"]["cost_usd"] == 0.0042
    assert body["execution"]["execution_is_not_confirmation"] is True
    assert body["token_usage_available"] is False


def test_diagnostics_counts_and_no_fabrication(client_factory) -> None:
    alice, pid, decision, inv = _seed(
        client_factory, status=AgentInvocationStatus.skipped_no_snapshot
    )
    with transaction() as conn:
        cancelled = _decision(pid, cancelled=True)
        _insert_decision(conn, cancelled)
    r = alice.get(f"/api/v1/projects/{pid}/first-responder/diagnostics")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["investigations_total"] >= 2
    assert d["decisions_cancelled"] == 1
    assert d["invocations_by_status"].get("skipped_no_snapshot") == 1
    assert d["missing_prerequisites"].get("skipped_no_snapshot") == 1
    assert d["cost_usd_total"] is None
    assert "Phase 7C" in d["cost_note"] or "execution records" in d["cost_note"]
    assert d["jobs_stalled"] == 0


def test_diagnostics_stalled_and_recent_error_redacted(client_factory) -> None:
    alice, pid, decision, inv = _seed(client_factory)
    with transaction() as conn:
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == inv.job_id)
            .values(
                status="running",
                lease_owner="w1",
                last_error={"code": "E", "message": "failed with sk-ant-abcdef1234567890"},
            )
        )
    with transaction() as conn:
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == inv.job_id)
            .values(lease_expires_at=sa.text("now() - interval '1 hour'"))
        )
    d = alice.get(f"/api/v1/projects/{pid}/first-responder/diagnostics").json()
    assert d["jobs_stalled"] == 1
    assert len(d["recent_errors"]) == 1
    assert "sk-ant-abcdef1234567890" not in str(d["recent_errors"])
    assert "REDACTED" in str(d["recent_errors"])


def test_timeline_final_state_cancelled(client_factory) -> None:
    alice, pid, _, _ = _seed(client_factory)
    with transaction() as conn:
        cancelled = _decision(pid, cancelled=True)
        _insert_decision(conn, cancelled)
    t = alice.get(f"/api/v1/first-responder/investigations/{cancelled.id}")
    assert t.json()["final_state"] == "cancelled"
