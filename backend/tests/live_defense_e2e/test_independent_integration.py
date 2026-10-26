"""Independent cross-workstream regressions; scripted providers, no live calls.

Assertions describe required behavior. Owner fixes for the Codex review findings
landed (F1 ledger/effect bridge, F2 emergency slots, F3 lease expiry, F4 defer history,
F5 waiting-cancel, F6 observability, F7 durable deadline, F8 final-submit race, F9 schema
pin, F10 DB clock); these now run as strict assertions.
"""

import pytest
import sqlalchemy as sa
from helpers import setup_project

from backend import execution, jobs
from backend.api.live_defense_investigations import _final_state
from backend.db import jobs as jobs_t
from backend.db import transaction
from backend.live_defense import modes
from shared.base import utcnow

from .transports import fake_provider, gateway_factory


def enqueue(kind="review_noop", project="prj_review", **kwargs):
    with transaction() as conn:
        return jobs.enqueue(conn, kind, {"project_id": project}, **kwargs)[0]


def jobrow(jid):
    with transaction() as conn:
        return conn.execute(sa.select(jobs_t).where(jobs_t.c.id == jid)).mappings().one()


def prepare(staging):
    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("independent-crash")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")
    return alice, app, repo


def test_emergency_stop_is_claimable_when_project_slots_are_full(settings_override):
    settings_override(job_max_running_per_project="1")
    enqueue()
    with transaction() as conn:
        assert jobs.claim(conn, "busy-worker", 60, ["review_noop"]) is not None
    stop = enqueue("live_defense_emergency_stop", priority=0)
    with transaction() as conn:
        claim = jobs.claim(conn, "stop-worker", 60, ["live_defense_emergency_stop"])
    assert claim is not None and claim.id == stop, "emergency rollback must bypass ordinary slots"


def test_complete_rejects_expired_lease_without_waiting_for_reconciler():
    jid = enqueue()
    with transaction() as conn:
        assert jobs.claim(conn, "expired-worker", 60, ["review_noop"]) is not None
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == jid)
            .values(lease_expires_at=sa.func.now() - sa.text("interval '1 second'"))
        )
    with transaction() as conn:
        assert jobs.complete(conn, jid, "expired-worker", {}) is False


def test_dependency_deferral_is_recorded_in_lifecycle_history():
    jid = enqueue()
    with transaction() as conn:
        assert jobs.claim(conn, "defer-worker", 60, ["review_noop"]) is not None
        assert jobs.defer(conn, jid, "defer-worker", delay_s=1)
        assert execution.history(conn, jid)[-1]["to_status"] == "queued"


def test_off_marks_waiting_first_responder_jobs_cancelled(client_factory):
    alice, bob = client_factory("alice"), client_factory("bob")
    pid = setup_project(alice, bob)["project"]
    parent = enqueue(project=pid)
    child = enqueue("live_defense_agent_step", project=pid, depends_on=parent)
    with transaction() as conn:
        modes.cancel_first_responder_jobs(conn, pid)
    assert jobrow(child)["status"] == "cancelled"
    assert jobrow(child)["cancel_requested"] is True


def test_deadline_compatibility_with_database_clock():
    jid = enqueue(deadline_s=30)
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == jid)
            .values(deadline_at=sa.func.now() - sa.text("interval '1 second'"))
        )
        execution.reconcile(conn)
        assert (
            conn.execute(sa.select(jobs_t.c.status).where(jobs_t.c.id == jid)).scalar_one()
            == "timed_out"
        )


@pytest.mark.parametrize("state", ["timed_out", "recovery_required"])
def test_observability_marks_nonrunning_jobs_as_not_in_progress(client_factory, state):
    from test_live_defense_investigations import _seed

    from backend.api.live_defense_investigations import _invocation_view

    _alice, _pid, decision, invocation = _seed(client_factory)
    jid = invocation.job_id
    with transaction() as conn:
        conn.execute(sa.update(jobs_t).where(jobs_t.c.id == jid).values(status=state))
        from backend.db import first_responder_invocations

        row = (
            conn.execute(
                sa.select(first_responder_invocations).where(
                    first_responder_invocations.c.id == invocation.id
                )
            )
            .mappings()
            .one()
        )
        view = _invocation_view(conn, row, now=utcnow())
        assert _final_state(decision, [view], None) != "in_progress"
        assert view.job.status == state


def test_crash_during_provider_request_requires_recovery_before_replay(staging):
    alice, app, repo = prepare(staging)
    fake = fake_provider(repo / "target")

    class WorkerCrash(BaseException):
        pass

    class CrashAfterSending:
        mode = "fake"
        calls = 0

        def complete(self, request):
            self.calls += 1
            if self.calls == 1:
                raise WorkerCrash("request sent; provider outcome is unknown")
            return fake.complete(request)

    provider = CrashAfterSending()
    staging.use_gateway(gateway_factory(provider))
    assert staging.ingest(alice, app).status_code == 202
    with pytest.raises(WorkerCrash):
        staging.drain()
    step = next(j for j in staging.job_rows("live_defense_agent_step") if j["status"] == "running")
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == step["id"])
            .values(lease_expires_at=sa.func.now() - sa.text("interval '1 second'"))
        )
        execution.reconcile(conn)
    assert jobrow(step["id"])["status"] == "recovery_required", (
        "paid request uncertainty must survive worker death"
    )
    staging.drain()
    assert provider.calls == 1


def test_first_responder_step_has_a_durable_queue_deadline(staging):
    alice, app, _repo = prepare(staging)
    assert staging.ingest(alice, app).status_code == 202
    staging.drain(kinds=["live_defense_shadow", "live_defense_first_responder"])
    steps = staging.job_rows("live_defense_agent_step")
    assert steps
    assert all(step["deadline_at"] is not None for step in steps), (
        "queue time and redeliveries must share the original first-responder deadline"
    )


def test_off_during_final_submit_cannot_upgrade_cancelled_invocation(staging):
    from security_agents.agents.scout import SPEC

    alice, app, repo = prepare(staging)
    fake = fake_provider(repo / "target")

    class OffOnSubmit:
        mode = "fake"
        flipped = False

        def complete(self, request):
            response = fake.complete(request)
            if request.agent.value == "scout" and any(
                block.get("name") == SPEC.submit_tool_name() for block in response.tool_uses()
            ):
                assert staging.set_mode(alice, app.project_id, "off").status_code == 200
                self.flipped = True
            return response

    provider = OffOnSubmit()
    staging.use_gateway(gateway_factory(provider))
    assert staging.ingest(alice, app).status_code == 202
    staging.drain()
    assert provider.flipped
    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    assert inv["scout"]["status"] == "cancelled", (
        "late successful output must not erase OFF cancellation"
    )
