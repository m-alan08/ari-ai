"""Execution-reliability (workstream B) compatibility tests — live, ungated.

This branch is stacked on a composition of the approved A and B heads
(A ``89a3deba`` Phase 7C + B ``17c3ded0`` execution reliability), so the
reliability queue is present and these tests execute against it directly —
no skip gate. They pin the queue contracts this suite relies on:

- new ``JobStatus`` values: ``waiting``, ``timed_out``, ``recovery_required``
- ``jobs.enqueue(..., deadline_s=, depends_on=, project_id=)``
- fair, per-project-concurrency-limited ``jobs.claim``
- ``jobs.begin_effect`` / ``end_effect`` effect tracking with
  ``recovery_required`` for uncertain side effects
- ``execution.reconcile`` for stale workers and expired deadlines

Deadline assertions use the database clock (``now()`` inside the same
transaction), never Python ``utcnow()`` — the transaction's ``now()`` is
fixed earlier than wall clock, so a Python timestamp can read as future to
the reconciler's SQL predicate (review finding F10).

Owner fixes landed; these run as strict assertions. Previously, a
independent review (F1, F7, F8): the tests assert the required contract and
are expected to fail until the owning workstream fixes it; a fix turns them
into XPASS failures, which is the signal to drop the marker.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend import jobs, worker
from backend.db import jobs as jobs_t
from backend.db import transaction
from security_agents.contracts import AgentName as RuntimeAgentName
from shared.ids import IdPrefix, new_id
from shared.live_defense import AgentInvocationStatus as INV

pytest.importorskip("security_agents")

pytestmark = pytest.mark.live_defense_e2e

_ORDER: list[str] = []
_KIND = "live_defense_e2e_compat_noop"


@worker.handler(_KIND)
def _noop(ctx: worker.JobContext) -> dict[str, object]:
    _ORDER.append(str(ctx.job.payload["tag"]))
    return {"ok": True}


def _enqueue(conn, tag: str, project_id: str, **kwargs) -> str:
    jid, _created = jobs.enqueue(
        conn, _KIND, {"tag": tag, "project_id": project_id}, project_id=project_id, **kwargs
    )
    return jid


def test_compat_depends_on_waits_for_prerequisite():
    pid = f"prj_compat_{new_id(IdPrefix.prj)[:12]}"
    _ORDER.clear()
    with transaction() as conn:
        a = _enqueue(conn, "a", pid)
        b = _enqueue(conn, "b", pid, depends_on=a)
        status = conn.execute(sa.select(jobs_t.c.status).where(jobs_t.c.id == b)).scalar_one()
        assert status == "waiting"
    worker.drain(max_jobs=10, kinds=[_KIND])
    assert _ORDER == ["a", "b"]


def test_compat_per_project_concurrency_is_fair(settings_override):
    settings_override(job_max_running_per_project=1)
    px = f"prj_compat_x_{new_id(IdPrefix.prj)[:10]}"
    py = f"prj_compat_y_{new_id(IdPrefix.prj)[:10]}"
    with transaction() as conn:
        _enqueue(conn, "x1", px)
        _enqueue(conn, "x2", px)
        _enqueue(conn, "y1", py)
        first = jobs.claim(conn, "compat-w", 60, [_KIND])
        assert first is not None and first.project_id == px
        # X is at its concurrency limit: the next claim must be Y's job, not x2.
        second = jobs.claim(conn, "compat-w", 60, [_KIND])
        assert second is not None
        assert second.project_id == py


def test_compat_execution_deadline_times_out(settings_override):
    from backend import execution

    pid = f"prj_compat_{new_id(IdPrefix.prj)[:12]}"
    with transaction() as conn:
        jid = _enqueue(conn, "deadline", pid, deadline_s=30)
        # Backdate the deadline using the database clock — now() inside this
        # same transaction — so reconcile's SQL predicate unequivocally sees
        # it as past (F10: a Python utcnow() can read as future here).
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == jid)
            .values(deadline_at=sa.func.now() - sa.text("interval '1 hour'"))
        )
        execution.reconcile(conn)
        status = conn.execute(sa.select(jobs_t.c.status).where(jobs_t.c.id == jid)).scalar_one()
        assert status == "timed_out"


def test_compat_uncertain_effect_needs_operator():
    pid = f"prj_compat_{new_id(IdPrefix.prj)[:12]}"
    with transaction() as conn:
        jid = _enqueue(conn, "effect", pid)
        job = jobs.claim(conn, "compat-w", 60, [_KIND])
        assert job is not None and job.id == jid
        assert jobs.begin_effect(conn, jid, "compat-w", "side-effect:1") is True
        outcome = jobs.fail(
            conn,
            jid,
            "compat-w",
            {"code": "WORKER_LOST", "message": "simulated worker loss"},
            retryable=True,
        )
        assert outcome == "recovery_required"
        status = conn.execute(sa.select(jobs_t.c.status).where(jobs_t.c.id == jid)).scalar_one()
        assert status == "recovery_required"
        # A job with an uncertain effect is never auto-re-claimed.
        assert jobs.claim(conn, "compat-w2", 60, [_KIND]) is None


# ---------------------------------------------------------------------------
# Durable deadlines through queue waiting and worker recovery (F7 regression)
# ---------------------------------------------------------------------------


def test_deadline_survives_requeue_and_recovery():
    """A durable deadline is set once at enqueue and never reset by recovery."""
    from backend import execution

    pid = f"prj_compat_{new_id(IdPrefix.prj)[:12]}"
    with transaction() as conn:
        jid = _enqueue(conn, "durable", pid, deadline_s=3600)
        job = jobs.claim(conn, "compat-w", 60, [_KIND])
        assert job is not None and job.id == jid
        deadline_at = conn.execute(
            sa.select(jobs_t.c.deadline_at).where(jobs_t.c.id == jid)
        ).scalar_one()
        assert deadline_at is not None
        # Worker lost mid-run: expire the lease with the DB clock, reconcile.
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == jid)
            .values(lease_expires_at=sa.func.now() - sa.text("interval '1 hour'"))
        )
        execution.reconcile(conn)
        row = (
            conn.execute(sa.select(jobs_t.c.status, jobs_t.c.deadline_at).where(jobs_t.c.id == jid))
            .mappings()
            .one()
        )
        # Recovered as re-claimable work (or already timed out by policy), and
        # the deadline is the same absolute instant — never reset on redelivery.
        assert row["status"] in ("queued", "timed_out")
        assert row["deadline_at"] == deadline_at


# ---------------------------------------------------------------------------
# Review-confirmed defects: owner fixes landed (F1 ledger, F7 deadline,
# F8 cancel-race). These now run as strict assertions.
# ---------------------------------------------------------------------------


def test_crash_after_send_requires_recovery_before_replay(staging):
    """No automatic second billable call while an attempt is unresolved (F1).

    The provider sends the request, then the worker dies before the response
    is recorded. The queue is allowed to re-claim the job — what must never
    happen is a second physical provider call while the first attempt's
    outcome is unresolved. Verified against the Phase 7C provider ledger:
    redelivery defers (``JobDeferred`` on an in-flight attempt) or commits the
    invocation as UNCERTAIN_PROVIDER_OUTCOME — it never re-sends.
    """
    from backend import execution

    from .transports import CrashAfterSend, fake_provider, gateway_factory

    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-compat-f1")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")

    fake = fake_provider(repo / "target")
    crashing = CrashAfterSend(fake, agents={"scout"}, crashes=1)
    staging.use_gateway(gateway_factory(crashing))

    assert staging.ingest(alice, app).status_code == 202
    # The injected death is a BaseException so it escapes the worker's
    # `except Exception` path — the job is left running mid-attempt.
    with pytest.raises(BaseException, match="worker death after provider send"):
        staging.drain()

    # Exactly one physical billable call left the process — and no response
    # was recorded, so its outcome is uncertain.
    calls_at_crash = len(fake.calls)
    assert calls_at_crash == 1
    step = next(
        j
        for j in staging.job_rows("live_defense_agent_step")
        if '"scout"' in json.dumps(j["payload"])
    )
    assert step["status"] == "running"

    # Worker is gone: expire the lease on the DB clock and reconcile.
    staging.expire_lease(step["id"])
    with transaction() as conn:
        execution.reconcile(conn)

    # The job may be re-claimed — the ledger forbids a replayed billable call
    # while the first attempt's outcome is unresolved. Force the redelivery to
    # prove it.
    staging.requeue_now(step["id"])
    staging.drain()
    assert len(fake.calls) == calls_at_crash

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    # The unresolved attempt is surfaced, not silently replayed: the scout
    # invocation must not report an execution.
    assert inv["scout"]["status"] != INV.executed.value


def test_off_during_final_submit_stays_cancelled(staging):
    """OFF during final-submit persistence must not upgrade to executed (F8).

    Flipping OFF through the real mode API while Scout's submit response is in
    flight must leave the invocation cancelled (or skipped) — a stale
    completion must not erase the revocation.
    """
    from .transports import HookProvider, all_role_scripts, fake_provider, gateway_factory

    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-compat-f8")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")

    scripts = all_role_scripts(repo / "target")
    fake = fake_provider(repo / "target", scripts=scripts)
    # Fire during Scout's last (submit) turn — the response comes back to a
    # loop that should no longer be authorized to persist an execution.
    hook = HookProvider(
        fake,
        when=len(scripts[RuntimeAgentName.SCOUT]),
        on_call=lambda _req: staging.set_mode(alice, app.project_id, "off"),
    )
    staging.use_gateway(gateway_factory(hook))

    assert staging.ingest(alice, app).status_code == 202
    staging.drain()

    inv = staging.by_agent(staging.invocations(alice, app.project_id))
    # Revocation wins over the in-flight completion (F8 fix verified on A
    # 5417990f — was overwritten to executed before the ledger head).
    assert inv["scout"]["status"] != INV.executed.value


def test_agent_step_jobs_have_durable_deadline(staging):
    """First-responder step jobs must carry the incident deadline (F7)."""
    from .transports import fake_provider, gateway_factory

    alice, bob = staging.login("alice"), staging.login("bob")
    app = staging.create_project(alice, bob)
    repo, sha = staging.checkout_owned_repo("repo-compat-f7")
    staging.bind_repo(app, repo)
    staging.index_snapshot(app.repo_id, sha)
    staging.register_app(app)
    staging.issue_token(app)
    staging.attest_mapping(app, commit_sha=sha)
    staging.set_mode(alice, app.project_id, "monitor")
    staging.use_gateway(gateway_factory(fake_provider(repo / "target")))

    assert staging.ingest(alice, app).status_code == 202
    staging.drain()

    steps = staging.job_rows("live_defense_agent_step")
    assert steps, "expected agent-step jobs after a monitor-mode investigation"
    assert all(j["deadline_at"] is not None for j in steps)
