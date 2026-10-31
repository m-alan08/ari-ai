from datetime import timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from helpers import setup_project

from backend import jobs
from backend.db import assessment_events, audit_events, job_events, transaction
from backend.host import AssessmentHost
from backend.settings import get_settings
from security_agents.host_adapter import HostOrchestrator
from security_agents.workflow import AssessmentState as AgentState
from shared.assessment import AssessmentState, can_transition
from shared.base import utcnow
from shared.scope import ApprovalSubject
from shared.tenancy import Usage


@pytest.fixture
def interrupted(client_factory):
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    response = alice.post(
        f"/api/v1/projects/{ids['project']}/assessments",
        json={"scope_id": ids["scope"], "repo_id": ids["repo"], "commit_sha": "b" * 40},
    )
    assert response.status_code == 202, response.text
    host = AssessmentHost(response.json()["assessment"]["id"])
    for state in (
        AssessmentState.SCOPE_CHECK,
        AssessmentState.INDEXING,
        AssessmentState.FUNCTIONAL_REVIEW,
    ):
        host.transition(state)
    host.charge(Usage(input_tokens=10, output_tokens=5, usd=0.01, sandbox_seconds=4))
    with transaction() as conn:
        conn.execute(
            sa.update(assessment_events)
            .where(
                assessment_events.c.assessment_id == host.assessment_id,
                assessment_events.c.type == "assessment.state_changed",
            )
            .values(created_at=utcnow() - timedelta(seconds=10))
        )
    return host


@pytest.mark.parametrize(
    "lost_state",
    [
        AssessmentState.FUNCTIONAL_REVIEW,
        AssessmentState.RECON,
        AssessmentState.VALIDATION,
        AssessmentState.ANALYSIS,
        AssessmentState.DIAGNOSIS,
        AssessmentState.AWAITING_APPROVAL,
        AssessmentState.REMEDIATION,
        AssessmentState.HARDENING,
        AssessmentState.REPORTING,
    ],
)
def test_lost_worker_restarts_with_carried_usage_and_audit(
    interrupted, monkeypatch, tmp_path, lost_state
):
    host = interrupted
    for state in (
        AssessmentState.RECON,
        AssessmentState.VALIDATION,
        AssessmentState.ANALYSIS,
        AssessmentState.DIAGNOSIS,
        AssessmentState.AWAITING_APPROVAL,
        AssessmentState.REMEDIATION,
        AssessmentState.HARDENING,
        AssessmentState.REPORTING,
    ):
        if host.assessment.state is lost_state:
            break
        host._set_state(state, "simulate interrupted run")
    with transaction() as conn:
        conn.execute(
            sa.update(assessment_events)
            .where(
                assessment_events.c.assessment_id == host.assessment_id,
                assessment_events.c.type == "assessment.state_changed",
            )
            .values(created_at=utcnow() - timedelta(seconds=10))
        )
    before = host.assessment
    seen = {}

    class FreshRun:
        state = None

        def run(self):
            seen["assessment"] = host.assessment
            for state in (
                AssessmentState.INDEXING,
                AssessmentState.FUNCTIONAL_REVIEW,
                AssessmentState.RECON,
                AssessmentState.REPORTING,
                AssessmentState.COMPLETED,
            ):
                assert can_transition(host.assessment.state, state)
                host.transition(state)
            return SimpleNamespace(state=AgentState.COMPLETED)

    adapter = HostOrchestrator(workdir=tmp_path)
    monkeypatch.setattr(adapter, "_build_intel", lambda *args: object())
    monkeypatch.setattr(adapter, "_make_orchestrator", lambda *args: FreshRun())
    monkeypatch.setattr(adapter, "_settle", lambda *args: None)
    adapter.run(host)
    restarted = seen["assessment"]
    assert restarted.state is AssessmentState.SCOPE_CHECK
    assert restarted.commit_sha == before.commit_sha and restarted.scope_id == before.scope_id
    assert restarted.budget == before.budget
    assert restarted.usage.input_tokens == 10 and restarted.usage.output_tokens == 5
    assert restarted.usage.usd == 0.01
    if lost_state is AssessmentState.AWAITING_APPROVAL:
        assert restarted.usage.sandbox_seconds == 4
    else:
        assert restarted.usage.sandbox_seconds >= 14
    assert host.assessment.state is AssessmentState.COMPLETED
    with transaction() as conn:
        events = (
            conn.execute(
                sa.select(audit_events.c.doc).where(
                    audit_events.c.doc["subject_id"].astext == host.assessment_id,
                    audit_events.c.doc["action"].astext == "assessment.lost_worker_recovery",
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert (
        events[0]["details"]["reason"]
        == "unsettled usage from lost worker estimated as upper bound"
    )
    estimate = events[0]["details"]["estimated_sandbox_seconds"]
    if lost_state is AssessmentState.AWAITING_APPROVAL:
        assert estimate == 0
    else:
        assert estimate >= 10


@pytest.mark.parametrize("subject_id", ["fnd_functional", "rmp_security"])
def test_long_approval_pause_has_no_estimated_sandbox_time(interrupted, subject_id):
    host = interrupted
    for state in (
        AssessmentState.RECON,
        AssessmentState.VALIDATION,
        AssessmentState.ANALYSIS,
        AssessmentState.DIAGNOSIS,
        AssessmentState.AWAITING_APPROVAL,
    ):
        host.transition(state)
    host.request_approval(ApprovalSubject.remediation, subject_id, "human approval")
    with transaction() as conn:
        conn.execute(
            sa.update(assessment_events)
            .where(
                assessment_events.c.assessment_id == host.assessment_id,
                assessment_events.c.type == "assessment.state_changed",
            )
            .values(created_at=utcnow() - timedelta(days=3))
        )
    assert host.restart_lost_state("AWAITING_APPROVAL") == "restart"
    assert host.assessment.state is AssessmentState.SCOPE_CHECK
    assert host.assessment.usage.sandbox_seconds == 4
    with transaction() as conn:
        event = conn.execute(
            sa.select(audit_events.c.doc).where(
                audit_events.c.doc["subject_id"].astext == host.assessment_id,
                audit_events.c.doc["action"].astext == "assessment.lost_worker_recovery",
            )
        ).scalar_one()
    assert event["details"]["estimated_sandbox_seconds"] == 0
    assert event["details"]["reason"] == "unsettled usage from lost worker estimated as upper bound"


@pytest.mark.parametrize("age, expected", [(30, 30), (300, 60)])
def test_running_estimate_uses_segment_start_and_lease_cap(interrupted, monkeypatch, age, expected):
    host = interrupted
    monkeypatch.setattr(get_settings(), "worker_lease_s", 60)
    with transaction() as conn:
        job = jobs.claim(conn, "lost-worker", lease_s=60, kinds=["run_assessment"])
        assert job is not None
        conn.execute(
            sa.update(job_events)
            .where(job_events.c.job_id == job.id, job_events.c.to_status == "running")
            .values(created_at=utcnow() - timedelta(seconds=age))
        )
    assert host.restart_lost_state("FUNCTIONAL_REVIEW") == "restart"
    estimate = host.assessment.usage.sandbox_seconds - 4
    assert expected <= estimate < expected + 2
    with transaction() as conn:
        event = conn.execute(
            sa.select(audit_events.c.doc).where(
                audit_events.c.doc["subject_id"].astext == host.assessment_id,
                audit_events.c.doc["action"].astext == "assessment.lost_worker_recovery",
            )
        ).scalar_one()
    assert event["details"]["estimated_sandbox_seconds"] == pytest.approx(estimate)


def test_second_loss_fails_with_reason(interrupted):
    host = interrupted
    assert host.restart_lost_state("FUNCTIONAL_REVIEW") == "restart"
    host.transition(AssessmentState.INDEXING)
    assert host.restart_lost_state("INDEXING") == "terminal"
    result = host.assessment
    assert result.state is AssessmentState.FAILED
    assert result.error.code.value == "CONFLICT_STATE"
    assert "start a new assessment" in result.error.message


def test_carried_sandbox_time_is_charged_on_new_host(interrupted):
    import time

    host = interrupted
    assert host.restart_lost_state("FUNCTIONAL_REVIEW") == "restart"
    resumed = AssessmentHost(host.assessment_id)
    assert time.monotonic() - resumed._started_monotonic >= resumed.assessment.usage.sandbox_seconds


@pytest.mark.parametrize("resource", ["tokens", "sandbox"])
def test_lost_worker_carry_can_exhaust_budget(interrupted, resource):
    host = interrupted
    if resource == "tokens":
        host.charge(Usage(input_tokens=host.assessment.budget.max_tokens - 15))
    else:
        host.charge(Usage(sandbox_seconds=host.assessment.budget.max_wall_clock_s - 9))
    assert host.restart_lost_state("FUNCTIONAL_REVIEW") == "terminal"
    result = host.assessment
    assert result.state is AssessmentState.BUDGET_EXHAUSTED
    assert result.error.code.value == "BUDGET_EXHAUSTED"
    assert result.usage.sandbox_seconds >= 14
