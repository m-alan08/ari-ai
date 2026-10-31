"""Live Defense observability: investigation list/timeline, invocation detail,
diagnostics. Read-only, project-scoped, bounded."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from helpers import add_member, setup_project

from backend import audit
from backend import jobs as jobq
from backend.db import (
    first_responder_decisions,
    first_responder_invocations,
    jobs,
    transaction,
)
from backend.redaction import redact
from shared.assessment import AgentName
from shared.audit import ActorType
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


def _set_job_status(conn, inv, status: str) -> None:
    conn.execute(sa.update(jobs).where(jobs.c.id == inv.job_id).values(status=status))


# Migration 0018 widened ck_jobs_status to the execution-reliability states
# ('waiting', 'timed_out', 'recovery_required'). The fixture makes sure the states
# are accepted and restores whatever constraint the schema had afterwards, so it
# never leaves a narrower constraint behind for the tests that run after it.
@pytest.fixture
def reliability_job_statuses():
    new = (
        "status in ('queued','waiting','running','succeeded','failed','cancelled','dead',"
        "'timed_out','recovery_required')"
    )
    with transaction() as conn:
        original = conn.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_jobs_status'"
            )
        ).scalar_one()
        conn.execute(sa.text("ALTER TABLE jobs DROP CONSTRAINT ck_jobs_status"))
        conn.execute(sa.text(f"ALTER TABLE jobs ADD CONSTRAINT ck_jobs_status CHECK ({new})"))
    try:
        yield
    finally:
        with transaction() as conn:
            conn.execute(sa.text("ALTER TABLE jobs DROP CONSTRAINT ck_jobs_status"))
            conn.execute(sa.text(f"ALTER TABLE jobs ADD CONSTRAINT ck_jobs_status {original}"))


@pytest.mark.usefixtures("reliability_job_statuses")
def test_timeline_effective_state_timed_out_and_recovery_required(client_factory) -> None:
    """A queued invocation whose job stopped is never reported as in progress."""
    alice, pid, decision, inv = _seed(client_factory)
    for job_status in ("timed_out", "recovery_required"):
        with transaction() as conn:
            _set_job_status(conn, inv, job_status)
        t = alice.get(f"/api/v1/first-responder/investigations/{decision.id}")
        assert t.status_code == 200, t.text
        row = t.json()["invocations"][0]
        assert row["invocation"]["status"] == "queued"  # persisted state is honest
        assert row["effective_state"] == job_status
        assert t.json()["final_state"] == job_status


@pytest.mark.usefixtures("reliability_job_statuses")
def test_timeline_effective_state_precedence(client_factory) -> None:
    """Job row wins for non-terminal invocations; dead maps to failed."""
    alice, pid, decision, inv = _seed(client_factory)
    cases = {
        "failed": ("failed", "failed"),
        "cancelled": ("cancelled", "cancelled"),
        "dead": ("failed", "failed"),
        "waiting": ("waiting", "waiting"),
        "running": ("running", "in_progress"),
        "queued": ("queued", "queued"),
        "succeeded": ("succeeded", "suspected"),  # falls through to evidence_state
    }
    for job_status, (eff, final) in cases.items():
        with transaction() as conn:
            _set_job_status(conn, inv, job_status)
        t = alice.get(f"/api/v1/first-responder/investigations/{decision.id}").json()
        assert t["invocations"][0]["effective_state"] == eff, job_status
        assert t["final_state"] == final, job_status


def test_timeline_terminal_invocation_beats_job(client_factory) -> None:
    """A terminal invocation status stays authoritative over the job row."""
    alice, pid, decision, _ = _seed(
        client_factory, status=AgentInvocationStatus.completed_unverified
    )
    inv = None
    with transaction() as conn:
        row = conn.execute(
            sa.select(first_responder_invocations.c.doc).where(
                first_responder_invocations.c.decision_id == decision.id
            )
        ).scalar_one()
        inv = FirstResponderInvocation.model_validate(row)
        _set_job_status(conn, inv, "failed")
    t = alice.get(f"/api/v1/first-responder/investigations/{decision.id}").json()
    assert t["invocations"][0]["effective_state"] == "completed_unverified"
    assert t["final_state"] == "completed_unverified"


@pytest.mark.usefixtures("reliability_job_statuses")
def test_timeline_partial_failure_and_recovery_overrides(client_factory) -> None:
    """Partial failure outranks running; recovery_required outranks everything."""
    alice, pid, decision, inv = _seed(client_factory)
    with transaction() as conn:
        done = _invocation(decision, status=AgentInvocationStatus.completed_unverified)
        _insert_invocation(conn, done)
        _set_job_status(conn, inv, "failed")
    t = alice.get(f"/api/v1/first-responder/investigations/{decision.id}").json()
    assert t["final_state"] == "failed"
    with transaction() as conn:
        _set_job_status(conn, inv, "recovery_required")
    t = alice.get(f"/api/v1/first-responder/investigations/{decision.id}").json()
    assert t["final_state"] == "recovery_required"
    with transaction() as conn:
        _set_job_status(conn, inv, "timed_out")
    t = alice.get(f"/api/v1/first-responder/investigations/{decision.id}").json()
    assert t["final_state"] == "timed_out"


@pytest.mark.usefixtures("reliability_job_statuses")
def test_investigations_list_job_statuses_rollup(client_factory) -> None:
    alice, pid, decision, inv = _seed(client_factory)
    with transaction() as conn:
        _set_job_status(conn, inv, "timed_out")
    items = alice.get(f"/api/v1/projects/{pid}/first-responder/investigations").json()["items"]
    item = next(i for i in items if i["decision_id"] == decision.id)
    assert item["job_statuses"] == {"timed_out": 1}


@pytest.mark.usefixtures("reliability_job_statuses")
def test_diagnostics_new_job_states(client_factory) -> None:
    alice, pid, decision, inv = _seed(client_factory)
    waiting_inv = _invocation(decision, status=AgentInvocationStatus.queued)
    with transaction() as conn:
        wait_job, _ = jobq.enqueue(
            conn,
            "live_defense_agent_step",
            {
                "project_id": pid,
                "decision_id": decision.id,
                "invocation_id": waiting_inv.id,
                "agent": "diagnose",
                "capability_class": "investigative_readonly",
            },
            dedupe_key=f"fr-agent-{new_id(IdPrefix.fri)}",
        )
        waiting_inv.job_id = wait_job
        _insert_invocation(conn, waiting_inv)
        _set_job_status(conn, inv, "recovery_required")
        conn.execute(sa.update(jobs).where(jobs.c.id == wait_job).values(status="waiting"))
    d = alice.get(f"/api/v1/projects/{pid}/first-responder/diagnostics").json()
    assert d["jobs_recovery_required"] == 1
    assert d["jobs_waiting"] == 1
    assert d["jobs_queued"] == 0
    waiting = [w for w in d["waiting_jobs"] if w["job_id"] == wait_job]
    assert waiting and waiting[0]["status"] == "waiting"


# --- N4 (Codex RC2): stored job errors must not leak infrastructure secrets ---
# Read surfaces redact at read time, so previously stored unsanitized rows are
# covered too. The persistence-side sanitize belongs to Reliability Devin.

_DSN_SECRET = "postgresql://svc:READ-SIDE-only-password@db.internal/app"


def test_job_api_does_not_expose_infrastructure_credentials(client_factory) -> None:
    """Codex RC2 N4 probe, read side: /jobs/{id} redacts stored last_error/result."""
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    marker = "review" + "-only-password"
    with transaction() as conn:
        jid, _ = jobq.enqueue(conn, "review_credentials", {"project_id": ids["project"]})
        assert jobq.claim(conn, "review-worker", 60, ["review_credentials"])
        jobq.fail(
            conn,
            jid,
            "review-worker",
            {
                "code": "TOOL_ERROR",
                "message": "connect postgresql://svc:" + marker + "@db.internal/app failed",
            },
            retryable=False,
        )
    response = alice.get(f"/api/v1/jobs/{jid}")
    assert response.status_code == 200
    assert marker not in response.text
    assert "dsn_credentials" in response.text


def test_stored_sensitive_job_events_redact_at_read() -> None:
    """Previously stored unsanitized event reasons/details are covered by the
    same read-side redaction the history surface applies (Reliability wires it
    into /jobs/{id}/history; see coordination message)."""
    stored_events = [
        {
            "seq": 1,
            "from_status": "running",
            "to_status": "failed",
            "category": "tool_error",
            "reason": "connect " + _DSN_SECRET + " failed",
            "details": {"host": "db.internal", "note": "password=READ-SIDE-only-password"},
            "created_at": "2026-10-25T00:00:00Z",
        },
        {
            "seq": 2,
            "from_status": "recovery_required",
            "to_status": "queued",
            "category": "operator_resolution",
            "reason": None,
            "details": {"resolution": "retry", "note": "operator password=READ-SIDE-only-password"},
            "created_at": "2026-10-25T00:01:00Z",
        },
    ]
    out = redact(stored_events)
    raw = str(out)
    assert "READ-SIDE-only-password" not in raw
    assert "dsn_credentials" in raw
    assert stored_events[0]["reason"].endswith("@db.internal/app failed")  # storage untouched


def test_viewer_surfaces_do_not_bypass_job_error_redaction(client_factory) -> None:
    """A viewer reading job-derived surfaces sees only redacted error text."""
    alice, bob, viewer = client_factory("alice"), client_factory("bob"), client_factory("viewer")
    ids = setup_project(alice, bob)
    org_id = alice.me["memberships"][0]["organization"]["id"]
    add_member(org_id, viewer.me["user"]["id"], role="viewer")
    marker = "history" + "-only-password"
    with transaction() as conn:
        jid, _ = jobq.enqueue(conn, "live_defense_agent_step", {"project_id": ids["project"]})
        assert jobq.claim(conn, "history-worker", 60, ["live_defense_agent_step"])
        jobq.fail(
            conn,
            jid,
            "history-worker",
            {
                "code": "TOOL_ERROR",
                "message": "connect postgresql://svc:" + marker + "@db.internal/app failed",
            },
            retryable=False,
        )
    assert marker not in alice.get(f"/api/v1/jobs/{jid}").text
    diag = viewer.get(f"/api/v1/projects/{ids['project']}/first-responder/diagnostics")
    assert diag.status_code == 200
    assert marker not in diag.text


def test_audit_read_redacts_stored_sensitive_details(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    org_id = alice.me["memberships"][0]["organization"]["id"]
    marker = "audit" + "-only-password"
    with transaction() as conn:
        audit.record(
            conn,
            org_id=org_id,
            actor_type=ActorType.system,
            actor_id="test",
            action="test.n4",
            subject_type="project",
            subject_id=ids["project"],
            project_id=ids["project"],
            details={"note": "connect postgresql://svc:" + marker + "@db.internal/app failed"},
        )
    out = alice.get("/api/v1/audit", params={"project_id": ids["project"]})
    assert out.status_code == 200
    assert marker not in out.text
