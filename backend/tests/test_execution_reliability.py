"""Tier 2: execution reliability on the real PostgreSQL queue — lifecycle transitions and
history, crash/restart recovery, fair per-project concurrency, deadlines, retry budgets,
backpressure, database-failure recovery and uncertain external effects."""

from __future__ import annotations

import threading
import time
from collections import Counter

import pytest
import sqlalchemy as sa
from sqlalchemy import exc as sa_exc

from backend import execution, jobs, worker
from backend.db import job_events, transaction, worker_registry
from backend.db import jobs as jobs_t
from backend.jobs import InvalidTransition, JobStatus, RetryPolicy
from backend.live_defense import modes
from backend.worker import EffectNotApplied, JobContext, handler

CALLS: list[str] = []
EFFECTS: list[str] = []


@handler("rel_ok")
def _ok(ctx: JobContext) -> dict:
    CALLS.append(ctx.job.id)
    return {"attempt": ctx.job.attempts}


@handler("rel_fail")
def _fail(ctx: JobContext) -> None:
    CALLS.append(ctx.job.id)
    raise RuntimeError("always broken")


@handler("rel_db_down")
def _db_down(ctx: JobContext) -> None:
    raise sa_exc.OperationalError("SELECT 1", {}, Exception("connection refused"))


@handler("rel_effect_two")
def _effect_two(ctx: JobContext) -> dict:
    with ctx.effect("first"):
        EFFECTS.append("first")
    with ctx.effect("second"):
        EFFECTS.append("second")
        raise EffectNotApplied("rejected before acting")
    return {}


@handler("rel_slow")
def _slow(ctx: JobContext) -> dict:
    time.sleep(float(ctx.job.payload.get("sleep", 1.2)))
    ctx.check_cancelled()
    return {}


@handler("rel_effect")
def _effect(ctx: JobContext) -> dict:
    mode = ctx.job.payload["mode"]
    with ctx.effect(f"remote:{ctx.job.id}"):
        EFFECTS.append(ctx.job.id)
        if mode == "crash_mid_call":
            raise ConnectionResetError("connection reset after request was sent")
        if mode == "rejected":
            raise EffectNotApplied("remote rejected before acting")
    return {"applied": True}


@pytest.fixture(autouse=True)
def _reset(settings_override) -> None:
    CALLS.clear()
    EFFECTS.clear()
    jobs.RETRY_POLICIES.clear()
    settings_override(job_max_running_per_project="4", job_max_pending_per_project="1000")


def _job(job_id: str) -> dict:
    with transaction() as conn:
        return dict(conn.execute(sa.select(jobs_t).where(jobs_t.c.id == job_id)).mappings().one())


def _events(job_id: str) -> list[dict]:
    with transaction() as conn:
        return execution.history(conn, job_id)


def _expire(job_id: str, **extra: object) -> None:
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == job_id)
            .values(lease_expires_at=sa.func.now() - sa.text("interval '1 second'"), **extra)
        )


def _enqueue(kind: str, project: str | None = "prj_a", **kw: object) -> str:
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, kind, {"project_id": project} if project else {}, **kw)
    return jid


# --- lifecycle and history -----------------------------------------------------------


def test_transition_table_rejects_illegal_moves() -> None:
    jobs.validate_transition("j", "queued", "running")
    jobs.validate_transition("j", "recovery_required", "queued")
    for src, dst in [("succeeded", "running"), ("cancelled", "queued"), ("queued", "succeeded")]:
        with pytest.raises(InvalidTransition):
            jobs.validate_transition("j", src, dst)
    jid = _enqueue("rel_ok")
    with transaction() as conn, pytest.raises(InvalidTransition):
        row = jobs._locked(conn, jid)
        jobs.transition(conn, row, JobStatus.succeeded)


def test_successful_job_records_full_history_and_timings() -> None:
    jid = _enqueue("rel_ok")
    assert worker.drain(kinds=["rel_ok"]) == [jid]
    row = _job(jid)
    assert row["status"] == "succeeded" and row["project_id"] == "prj_a"
    assert row["first_started_at"] >= row["created_at"] and row["finished_at"] is not None
    assert [(e["from_status"], e["to_status"]) for e in _events(jid)] == [
        ("", "queued"),
        ("queued", "running"),
        ("running", "succeeded"),
    ]


def test_assessment_jobs_inherit_their_project(client_factory) -> None:
    from helpers import HEAD, setup_project

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    r = alice.post(
        f"/api/v1/projects/{ids['project']}/assessments",
        json={"scope_id": ids["scope"], "repo_id": ids["repo"], "commit_sha": HEAD},
    )
    assert r.status_code == 202, r.text
    assert _job(r.json()["job_id"])["project_id"] == ids["project"]


# --- crash, restart, leases ----------------------------------------------------------


def test_worker_crash_after_claim_is_requeued_by_reconcile_and_rerun() -> None:
    jid = _enqueue("rel_ok")
    with transaction() as conn:
        assert jobs.claim(conn, "dead-worker", lease_s=30, kinds=["rel_ok"]).id == jid
    # Live lease: neither another worker nor reconcile may take it.
    with transaction() as conn:
        assert jobs.claim(conn, "other", lease_s=30, kinds=["rel_ok"]) is None
        assert execution.reconcile(conn)["requeued"] == 0
    _expire(jid)
    with transaction() as conn:
        report = execution.reconcile(conn)
        stuck = execution.stuck_jobs(conn, "prj_a")
    assert report["requeued"] == 1 and stuck == []
    row = _job(jid)
    assert row["status"] == "queued" and row["lease_owner"] is None
    assert worker.drain(kinds=["rel_ok"]) == [jid]
    row = _job(jid)
    assert row["status"] == "succeeded" and row["attempts"] == 2 and CALLS == [jid]
    lost = [e for e in _events(jid) if e["category"] == "lease_expired"]
    assert lost and lost[0]["details"]["previous_owner"] == "dead-worker"


def test_stale_lease_is_reported_as_stuck_until_reconciled() -> None:
    jid = _enqueue("rel_ok")
    with transaction() as conn:
        jobs.claim(conn, "w1", lease_s=30, kinds=["rel_ok"])
    _expire(jid)
    with transaction() as conn:
        stuck = execution.stuck_jobs(conn)
        snap = execution.stats(conn, "prj_a")
    assert [(s["id"], s["reason"]) for s in stuck] == [(jid, "lease_expired")]
    assert snap["leases"]["expired"] == 1 and snap["stuck"]["by_reason"] == {"lease_expired": 1}


def test_expired_lease_on_final_attempt_is_dead_lettered() -> None:
    jid = _enqueue("rel_ok", max_attempts=1)
    with transaction() as conn:
        jobs.claim(conn, "w1", lease_s=30)
    _expire(jid)
    with transaction() as conn:
        assert execution.reconcile(conn)["dead"] == 1
    row = _job(jid)
    assert row["status"] == "dead" and row["error_category"] == "retry_exhausted"
    assert worker.drain(kinds=["rel_ok"]) == [] and CALLS == []


def test_restart_recovers_orphans_and_prunes_dead_workers(settings_override) -> None:
    settings_override(worker_stale_s="1")
    a, b = _enqueue("rel_ok"), _enqueue("rel_ok", project="prj_b")
    with transaction() as conn:
        jobs.claim(conn, "host-1:111", lease_s=30)
        jobs.claim(conn, "host-1:111", lease_s=30)
        execution.worker_seen(conn, "host-1:111", "busy", a)
        conn.execute(
            sa.update(worker_registry).values(
                last_seen_at=sa.func.now() - sa.text("interval '1 hour'")
            )
        )
    _expire(a)
    _expire(b)
    # A fresh worker process (drain runs startup reconciliation) finishes both.
    assert sorted(worker.drain(kinds=["rel_ok"])) == sorted([a, b])
    assert {_job(a)["status"], _job(b)["status"]} == {"succeeded"}
    with transaction() as conn:
        ids = conn.execute(sa.select(worker_registry.c.worker_id)).scalars().all()
        snap = execution.stats(conn)
    assert "host-1:111" not in ids
    assert snap["workers"]["active"] == len(ids)


def test_concurrent_reconcilers_do_not_double_process() -> None:
    jid = _enqueue("rel_ok")
    with transaction() as conn:
        jobs.claim(conn, "w1", lease_s=30)
    _expire(jid)
    results: list[dict] = []
    barrier = threading.Barrier(4)

    def run() -> None:
        barrier.wait()
        with transaction() as conn:
            results.append(execution.reconcile(conn))
            time.sleep(0.2)

    threads = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(r["requeued"] for r in results) == 1
    assert sum(1 for e in _events(jid) if e["to_status"] == "queued") == 2  # enqueue + requeue


# --- concurrency, fairness, backpressure --------------------------------------------


def test_per_project_limit_and_fair_ordering_prevent_starvation(settings_override) -> None:
    settings_override(job_max_running_per_project="2")
    noisy = [_enqueue("rel_ok", project="prj_noisy") for _ in range(20)]
    quiet = [_enqueue("rel_ok", project="prj_quiet") for _ in range(3)]
    claimed = []
    with transaction() as conn:
        for i in range(6):
            j = jobs.claim(conn, f"w{i}", lease_s=30, kinds=["rel_ok"])
            if j:
                claimed.append(j)
    per_project = Counter(j.project_id for j in claimed)
    assert per_project == {"prj_noisy": 2, "prj_quiet": 2}
    # The quiet project's first job runs before the noisy project's second (least-busy first).
    assert [j.project_id for j in claimed][:2] == ["prj_noisy", "prj_quiet"]
    assert {j.id for j in claimed} <= set(noisy) | set(quiet)


def test_concurrent_workers_never_exceed_project_limit(settings_override) -> None:
    settings_override(job_max_running_per_project="3")
    for p in ("prj_a", "prj_b"):
        for _ in range(15):
            _enqueue("rel_ok", project=p)
    got: list[jobs.Job] = []
    lock = threading.Lock()
    barrier = threading.Barrier(12)

    def claim(i: int) -> None:
        barrier.wait()
        with transaction() as conn:
            j = jobs.claim(conn, f"t{i}", lease_s=30, kinds=["rel_ok"])
        if j:
            with lock:
                got.append(j)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert Counter(j.project_id for j in got) == {"prj_a": 3, "prj_b": 3}
    with transaction() as conn:
        running = conn.execute(
            sa.select(jobs_t.c.project_id, sa.func.count())
            .where(jobs_t.c.status == "running")
            .group_by(jobs_t.c.project_id)
        ).all()
    assert dict(running) == {"prj_a": 3, "prj_b": 3}


def test_high_queue_pressure_applies_backpressure_per_project(settings_override) -> None:
    settings_override(job_max_pending_per_project="5")
    for _ in range(5):
        _enqueue("rel_ok", project="prj_a")
    with pytest.raises(jobs.QueueBackpressure) as exc:
        _enqueue("rel_ok", project="prj_a")
    assert exc.value.pending == 5 and exc.value.limit == 5
    _enqueue("rel_ok", project="prj_b")  # independent project unaffected
    _enqueue("live_defense_emergency_stop", project="prj_a")  # controls are never refused
    with transaction() as conn:  # dedupe hits return the existing job, not 429
        jid, _ = jobs.enqueue(conn, "rel_ok", {"project_id": "prj_b"}, dedupe_key="d1")
        again, created = jobs.enqueue(conn, "rel_ok", {"project_id": "prj_b"}, dedupe_key="d1")
    assert again == jid and created is False
    worker.drain(kinds=["rel_ok"])
    _enqueue("rel_ok", project="prj_a")  # drained queue accepts work again


def test_backpressure_maps_to_429_with_retry_after() -> None:
    from fastapi.testclient import TestClient

    from backend.main import create_app

    app = create_app()

    @app.get("/__bp")
    def boom() -> None:
        raise jobs.QueueBackpressure("prj_a", 9, 9)

    r = TestClient(app).get("/__bp")
    assert r.status_code == 429 and r.headers["Retry-After"] == "30"
    assert r.json()["error"]["code"] == "RATE_LIMITED"


# --- cancellation, deadlines, retries ------------------------------------------------


def test_cancellation_outcomes_are_recorded() -> None:
    before, during = _enqueue("rel_ok"), _enqueue("rel_slow")
    with transaction() as conn:
        jobs.request_cancel(conn, before)
    seen: dict[str, str] = {}

    def cancel_soon() -> None:
        time.sleep(0.3)
        with transaction() as conn:
            jobs.request_cancel(conn, during)

    t = threading.Thread(target=cancel_soon)
    t.start()
    worker.drain(kinds=["rel_slow", "rel_ok"])
    t.join()
    seen = {before: _job(before)["status"], during: _job(during)["status"]}
    assert seen == {before: "cancelled", during: "cancelled"} and CALLS == []
    with transaction() as conn:
        snap = execution.stats(conn, "prj_a")
    assert snap["cancellation"]["before_start"] == 1
    assert snap["cancellation"]["while_running"] == 1


def test_deadline_exhaustion_times_out_pending_running_and_retrying_jobs() -> None:
    pending = _enqueue("rel_ok", deadline_s=0.05)
    time.sleep(0.1)
    with transaction() as conn:
        assert jobs.claim(conn, "w", lease_s=30, kinds=["rel_ok"]) is None
        assert execution.reconcile(conn)["timed_out"] == 1
    assert _job(pending)["status"] == "timed_out"

    running = _enqueue("rel_slow", deadline_s=0.5, payload_sleep=None) if False else None
    with transaction() as conn:
        running, _ = jobs.enqueue(
            conn, "rel_slow", {"project_id": "prj_a", "sleep": 0.8}, deadline_s=0.5
        )
    worker.drain(kinds=["rel_slow"])
    row = _job(running)
    assert row["status"] == "timed_out" and row["error_category"] == "deadline_exceeded"

    jobs.register_retry_policy("rel_fail", RetryPolicy(base_s=10, jitter=0))
    retrying = _enqueue("rel_fail", deadline_s=5)
    worker.drain(kinds=["rel_fail"])
    row = _job(retrying)  # the next backoff (20s) would land past the deadline
    assert row["status"] == "timed_out" and CALLS == [retrying]


def test_retry_budget_exhaustion_dead_letters_with_history() -> None:
    jobs.register_retry_policy("rel_fail", RetryPolicy(base_s=0, jitter=0))
    jid = _enqueue("rel_fail", max_attempts=3)
    for _ in range(3):
        worker.drain(kinds=["rel_fail"])
    row = _job(jid)
    assert row["status"] == "dead" and row["attempts"] == 3
    assert row["error_category"] == "retry_exhausted"
    assert [e["to_status"] for e in _events(jid)].count("running") == 3
    with transaction() as conn:
        snap = execution.stats(conn, "prj_a")
    assert snap["retries"]["total_retries"] == 2
    assert snap["failure_categories"]["transient"] == 2
    assert snap["failure_categories"]["retry_exhausted"] == 1


def test_retry_policy_backoff_is_bounded_with_jitter() -> None:
    p = RetryPolicy(base_s=1, factor=2, max_s=300, jitter=0.1)
    assert 8 <= p.delay_s(3) <= 8.8
    assert 300 <= p.delay_s(20) <= 330


# --- database failures ----------------------------------------------------------------


def test_database_failure_in_handler_is_classified_and_retried() -> None:
    jobs.register_retry_policy("rel_db_down", RetryPolicy(base_s=0, jitter=0))
    jid = _enqueue("rel_db_down")
    worker.drain(kinds=["rel_db_down"], max_jobs=1)
    row = _job(jid)
    assert row["status"] == "queued" and row["error_category"] == "infrastructure"


def test_transient_database_failure_while_completing_is_retried(monkeypatch) -> None:
    real = jobs.complete
    calls = {"n": 0}

    def flaky(*a, **kw):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise sa_exc.OperationalError("UPDATE jobs", {}, Exception("server closed"))
        return real(*a, **kw)

    monkeypatch.setattr(jobs, "complete", flaky)
    jid = _enqueue("rel_ok")
    worker.drain(kinds=["rel_ok"])
    assert _job(jid)["status"] == "succeeded" and calls["n"] == 2 and CALLS == [jid]


def test_persistent_database_failure_leaves_job_for_lease_recovery(
    monkeypatch, settings_override
) -> None:
    settings_override(worker_finalize_retries="2")

    def down(*a, **kw):  # type: ignore[no-untyped-def]
        raise sa_exc.OperationalError("UPDATE jobs", {}, Exception("db down"))

    monkeypatch.setattr(jobs, "complete", down)
    monkeypatch.setattr(jobs, "fail", down)
    jid = _enqueue("rel_ok")
    worker.drain(kinds=["rel_ok"], max_jobs=1)
    assert _job(jid)["status"] == "running"  # nothing recorded; lease still covers it
    monkeypatch.undo()
    _expire(jid)
    assert worker.drain(kinds=["rel_ok"]) == [jid]
    assert _job(jid)["status"] == "succeeded" and _job(jid)["attempts"] == 2


# --- uncertain external effects ------------------------------------------------------


def test_failure_after_effect_started_requires_recovery_and_is_not_replayed() -> None:
    jid = _enqueue("rel_effect")
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t).where(jobs_t.c.id == jid).values(payload={"mode": "crash_mid_call"})
        )
    worker.drain(kinds=["rel_effect"])
    row = _job(jid)
    assert row["status"] == "recovery_required" and row["effect_state"] == "in_flight"
    assert row["recovery"]["reason"] == "uncertain_side_effect"
    assert row["recovery"]["error_category"] == "network"
    assert worker.drain(kinds=["rel_effect"]) == [] and EFFECTS == [jid]
    with transaction() as conn:
        assert [s["reason"] for s in execution.stuck_jobs(conn)] == ["recovery_required"]


def test_worker_death_during_effect_is_never_reclaimed_automatically() -> None:
    jid = _enqueue("rel_effect")
    with transaction() as conn:
        assert jobs.claim(conn, "w-dies", lease_s=30).id == jid
    with transaction() as conn:
        assert jobs.begin_effect(conn, jid, "w-dies", "control:x:apply")
    _expire(jid)
    with transaction() as conn:
        assert jobs.claim(conn, "w2", lease_s=30) is None  # claim never re-takes it
        assert execution.reconcile(conn)["recovery_required"] == 1
    row = _job(jid)
    assert row["status"] == "recovery_required" and row["recovery"]["effect_key"] == (
        "control:x:apply"
    )
    assert worker.drain(kinds=["rel_effect"]) == [] and EFFECTS == []
    # The original worker reappears and reports the observed outcome.
    with transaction() as conn:
        assert jobs.complete(conn, jid, "w2", {}) is False
        assert jobs.complete(conn, jid, "w-dies", {"applied": True}) is True
    assert _job(jid)["status"] == "succeeded"


def test_effect_definitely_not_applied_stays_retryable() -> None:
    jobs.register_retry_policy("rel_effect", RetryPolicy(base_s=0, jitter=0))
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "rel_effect", {"project_id": "prj_a", "mode": "rejected"})
    worker.drain(kinds=["rel_effect"], max_jobs=1)
    row = _job(jid)
    assert row["status"] == "queued" and row["effect_state"] is None


def test_applied_earlier_effect_keeps_job_uncertain_when_later_one_is_rejected() -> None:
    jobs.register_retry_policy("rel_effect_two", RetryPolicy(base_s=0, jitter=0))
    jid = _enqueue("rel_effect_two")
    worker.drain(kinds=["rel_effect_two"], max_jobs=1)
    row = _job(jid)
    # The first effect was applied; replaying the handler could apply it again, so the
    # second effect's clean rejection must NOT make the job retryable.
    assert row["status"] == "recovery_required" and row["effect_state"] == "settled"
    assert EFFECTS == ["first", "second"]


def test_effect_is_refused_once_cancelled() -> None:
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "rel_effect", {"project_id": "prj_a", "mode": "ok"})
        jobs.claim(conn, "w", lease_s=30)
        jobs.request_cancel(conn, jid)
    with transaction() as conn:
        assert jobs.begin_effect(conn, jid, "w", "k") is False


def test_operator_resolution_paths_and_retry_respects_cancellation() -> None:
    ids = []
    for _ in range(4):
        with transaction() as conn:
            jid, _ = jobs.enqueue(conn, "rel_effect", {"project_id": "prj_a", "mode": "ok"})
            jobs.claim(conn, "w", lease_s=30)
            jobs.begin_effect(conn, jid, "w", "k")
        _expire(jid)
        ids.append(jid)
    with transaction() as conn:
        execution.reconcile(conn)
        jobs.request_cancel(conn, ids[3])
    with transaction() as conn:
        r = execution.resolve_recovery(conn, ids[0], resolution="applied", actor_id="u", note="n")
        assert r["status"] == "succeeded"
        r = execution.resolve_recovery(
            conn, ids[1], resolution="not_applied", actor_id="u", note="n"
        )
        assert r["status"] == "failed"
        r = execution.resolve_recovery(conn, ids[2], resolution="retry", actor_id="u", note="n")
        assert r["status"] == "queued"
        with pytest.raises(execution.RecoveryError):
            execution.resolve_recovery(conn, ids[3], resolution="retry", actor_id="u", note="n")
        with pytest.raises(execution.RecoveryError):
            execution.resolve_recovery(conn, ids[0], resolution="retry", actor_id="u", note="n")
    assert worker.drain(kinds=["rel_effect"]) == [ids[2]]  # re-runs through its handler
    assert _job(ids[2])["status"] == "succeeded" and _job(ids[3])["status"] == ("recovery_required")
    with transaction() as conn:
        snap = execution.stats(conn, "prj_a")
    assert snap["recovery"]["resolved"] == {"applied": 1, "not_applied": 1, "retry": 1}


# --- prerequisites ------------------------------------------------------------------


def test_waiting_on_prerequisite_promotes_or_fails() -> None:
    pre = _enqueue("rel_ok")
    dep = _enqueue("rel_ok", depends_on=pre)
    assert _job(dep)["status"] == "waiting"
    assert worker.drain(kinds=["rel_ok"]) == [pre, dep]
    bad = _enqueue("rel_fail", max_attempts=1)
    child = _enqueue("rel_ok", depends_on=bad)
    grandchild = _enqueue("rel_ok", depends_on=child)
    worker.drain(kinds=["rel_fail", "rel_ok"])
    assert _job(bad)["status"] == "dead"
    assert _job(child)["status"] == "failed" and _job(grandchild)["status"] == "failed"
    assert _job(child)["error_category"] == "prerequisite_failed"
    done = _enqueue("rel_ok", depends_on=pre)  # prerequisite already succeeded
    assert _job(done)["status"] == "queued"


# --- isolation and authorization ---------------------------------------------------


def test_stats_are_project_isolated() -> None:
    _enqueue("rel_ok", project="prj_a")
    _enqueue("rel_ok", project="prj_b")
    _enqueue("rel_ok", project="prj_b")
    with transaction() as conn:
        a, b = execution.stats(conn, "prj_a"), execution.stats(conn, "prj_b")
    assert a["queue"]["pending"] == 1 and b["queue"]["pending"] == 2
    assert "workers" not in a


def test_execution_api_enforces_project_authorization(client_factory) -> None:
    from helpers import add_member, setup_project

    alice, bob, eve, vic = (client_factory(n) for n in ("alice", "bob", "eve", "vic"))
    ids = setup_project(alice, bob)
    org_id = alice.me["memberships"][0]["organization"]["id"]
    add_member(org_id, vic.me["user"]["id"], "viewer")
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "rel_effect", {"project_id": ids["project"], "mode": "ok"})
        jobs.claim(conn, "w", lease_s=30, kinds=["rel_effect"])
        jobs.begin_effect(conn, jid, "w", "k")
    _expire(jid)
    with transaction() as conn:
        execution.reconcile(conn)
    base = f"/api/v1/projects/{ids['project']}/execution"
    assert alice.get(base).json()["recovery"]["required"] == 1
    assert vic.get(base).status_code == 200
    assert eve.get(base).status_code == 404
    assert client_factory().get(base).status_code == 401
    hist = f"/api/v1/jobs/{jid}/history"
    assert [e["to_status"] for e in vic.get(hist).json()["events"]][-1] == "recovery_required"
    assert eve.get(hist).status_code == 404
    job = alice.get(f"/api/v1/jobs/{jid}").json()
    assert job["status"] == "recovery_required" and job["effect_state"] == "in_flight"
    body = {"resolution": "retry", "note": "adapter log shows no request"}
    url = f"/api/v1/jobs/{jid}/recovery"
    assert eve.post(url, json=body).status_code == 404
    assert vic.post(url, json=body).status_code == 403
    assert _job(jid)["status"] == "recovery_required"
    r = alice.post(url, json=body)
    assert r.status_code == 200 and r.json()["status"] == "queued", r.text
    assert alice.post(url, json=body).status_code == 409
    audit = alice.get("/api/v1/audit", params={"project_id": ids["project"]}).json()
    assert any(e["action"] == "job.recovery_resolved" for e in audit["items"])


def test_reconcile_never_runs_handlers_or_bypasses_cancellation() -> None:
    jid = _enqueue("rel_ok")
    with transaction() as conn:
        jobs.claim(conn, "w", lease_s=30)
        jobs.request_cancel(conn, jid)
    _expire(jid)
    worker.drain(kinds=["rel_ok"])
    assert _job(jid)["status"] == "cancelled" and CALLS == []
    with transaction() as conn:
        n = conn.execute(
            sa.select(sa.func.count()).where(
                job_events.c.job_id == jid, job_events.c.reason.in_(("claimed",))
            )
        ).scalar_one()
    assert n == 1


def test_queue_wait_and_duration_metrics() -> None:
    for _ in range(3):
        _enqueue("rel_ok")
    worker.drain(kinds=["rel_ok"])
    with transaction() as conn:
        snap = execution.stats(conn, "prj_a")
    assert snap["queue"]["wait"]["samples"] == 3
    assert snap["execution"]["duration"]["samples"] == 3
    assert snap["status_counts"]["succeeded"] == 3
    assert snap["queue"]["wait"]["p95_s"] >= 0


# --- Codex independent-review regressions (F2-F5) ---------------------------------------

EMERGENCY_KINDS = (
    "live_defense_emergency_stop",
    "live_defense_rollback",
    "live_defense_expire",
)


def _claim(name: str, kinds: list[str] | None = None):
    with transaction() as conn:
        return jobs.claim(conn, name, 60, kinds)


def test_emergency_jobs_claimable_when_project_and_global_caps_saturated(settings_override):
    """F2: emergency-stop/rollback/expiry must bypass ordinary saturated capacity."""
    settings_override(
        job_max_running_per_project="1", job_max_running_global="1", job_max_running_emergency="3"
    )
    _enqueue("rel_ok", priority=100)
    assert _claim("busy-worker", ["rel_ok"]) is not None
    for kind in EMERGENCY_KINDS:
        jid = _enqueue(kind, priority=0)
        claim = _claim("stop-worker-" + kind.rsplit("_", 1)[-1], [kind])
        assert claim is not None and claim.id == jid, f"{kind} must bypass full ordinary slots"


def test_emergency_capacity_is_bounded(settings_override):
    """F2: the exemption is reserved capacity, not unlimited emergency concurrency."""
    settings_override(job_max_running_emergency="1")
    _enqueue("live_defense_emergency_stop", priority=0)
    assert _claim("stop-1", ["live_defense_emergency_stop"]) is not None
    _enqueue("live_defense_emergency_stop", priority=0)
    assert _claim("stop-2", ["live_defense_emergency_stop"]) is None


def test_emergency_jobs_do_not_eat_ordinary_capacity(settings_override):
    """F2: emergency claims don't consume a project's ordinary running slots."""
    settings_override(job_max_running_per_project="1")
    _enqueue("live_defense_emergency_stop", priority=0)
    assert _claim("stop-w", ["live_defense_emergency_stop"]) is not None
    _enqueue("rel_ok")
    assert _claim("work-w", ["rel_ok"]) is not None


def test_new_apply_operations_stay_under_ordinary_limits(settings_override):
    """F2: new apply/verify operations must not bypass limits."""
    settings_override(job_max_running_per_project="1")
    _enqueue("rel_ok")
    assert _claim("busy-worker", ["rel_ok"]) is not None
    _enqueue("live_defense_execute", priority=0)
    assert _claim("apply-worker", ["live_defense_execute"]) is None
    _enqueue("live_defense_verify", priority=0)
    assert _claim("verify-worker", ["live_defense_verify"]) is None


def test_emergency_cleanup_bounded_latency_under_queue_saturation(settings_override):
    """F2: emergency jobs are claimable ahead of saturated ordinary backlog."""
    settings_override(job_max_running_per_project="1", job_max_running_emergency="1")
    _enqueue("rel_ok")
    assert _claim("busy", ["rel_ok"]) is not None
    for _ in range(5):
        _enqueue("rel_ok")  # ordinary backlog
    stop = _enqueue("live_defense_emergency_stop", priority=0)
    claim = _claim("stop", ["rel_ok", "live_defense_emergency_stop"])
    assert claim is not None and claim.id == stop


def test_complete_rejects_expired_lease_without_waiting_for_reconciler():
    """F3: an expired worker cannot report ordinary success."""
    jid = _enqueue("rel_ok")
    assert _claim("expired-worker", ["rel_ok"]) is not None
    _expire(jid)
    with transaction() as conn:
        assert jobs.complete(conn, jid, "expired-worker", {}) is False


def test_fail_rejects_expired_lease_leaving_recovery_to_reconcile():
    """F3: an expired worker cannot record ordinary failure outcomes either."""
    jid = _enqueue("rel_fail")
    assert _claim("expired-worker", ["rel_fail"]) is not None
    _expire(jid)
    with transaction() as conn:
        assert jobs.fail(conn, jid, "expired-worker", {"message": "x"}, retryable=True) == "lost"
        assert _job(jid)["status"] == "running"
        execution.reconcile(conn)
    assert _job(jid)["status"] == "queued"


def test_expired_owner_loses_race_to_new_lease_owner():
    """F3: after reclaim, the old owner cannot complete; the new owner can."""
    jid = _enqueue("rel_ok")
    assert _claim("old-worker", ["rel_ok"]) is not None
    _expire(jid)
    assert _claim("new-worker", ["rel_ok"]) is not None
    with transaction() as conn:
        assert jobs.complete(conn, jid, "old-worker", {}) is False
        assert jobs.complete(conn, jid, "new-worker", {"ok": True}) is True
    assert _job(jid)["status"] == "succeeded"


def test_heartbeat_race_extends_lease_then_completion_succeeds():
    """F3: a live lease (heartbeat-extended) completes normally."""
    jid = _enqueue("rel_ok")
    assert _claim("w", ["rel_ok"]) is not None
    with transaction() as conn:
        assert jobs.heartbeat_state(conn, jid, "w", 60) == "ok"
        assert jobs.complete(conn, jid, "w", {}) is True


def test_deferral_is_recorded_in_lifecycle_history():
    """F4: deferral goes through the validated transition + history path."""
    jid = _enqueue("rel_ok")
    job = _claim("defer-worker", ["rel_ok"])
    with transaction() as conn:
        assert jobs.defer(conn, jid, "defer-worker", delay_s=1) == "queued"
    assert _job(jid)["status"] == "queued"
    last = _events(jid)[-1]
    assert last["to_status"] == "queued"
    assert last["attempt"] == job.attempts


def test_deferral_does_not_consume_retry_budget():
    """F4: claim/defer pairs are neutral on the attempts budget."""
    jid = _enqueue("rel_ok", max_attempts=2)
    for i in range(3):
        assert _claim(f"w{i}", ["rel_ok"]) is not None
        with transaction() as conn:
            assert jobs.defer(conn, jid, f"w{i}") == "queued"
    row = _job(jid)
    assert row["attempts"] == 3 and row["defer_count"] == 3
    # One real failure still leaves a retry, then exhaustion dead-letters.
    assert _claim("w3", ["rel_ok"]) is not None
    with transaction() as conn:
        assert jobs.fail(conn, jid, "w3", {"message": "boom"}, retryable=True) == "queued"
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == jid)
            .values(run_after=sa.func.now() - sa.text("interval '1 second'"))
        )
        assert jobs.claim(conn, "w4", 60, ["rel_ok"]) is not None
        assert jobs.fail(conn, jid, "w4", {"message": "boom"}, retryable=True) == "dead"


def test_deferral_respects_cancellation_deadline_and_effect_guards():
    """F4: cancel/deadline take precedence; uncertain effects refuse deferral."""
    jid = _enqueue("rel_ok")
    assert _claim("w", ["rel_ok"]) is not None
    with transaction() as conn:
        jobs.request_cancel(conn, jid)
        assert jobs.defer(conn, jid, "w") == "cancelled"
    assert _job(jid)["status"] == "cancelled"

    jid = _enqueue("rel_ok", deadline_s=60)
    assert _claim("w", ["rel_ok"]) is not None
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == jid)
            .values(deadline_at=sa.func.now() - sa.text("interval '1 second'"))
        )
        assert jobs.defer(conn, jid, "w") == "timed_out"

    jid = _enqueue("rel_ok")
    assert _claim("w", ["rel_ok"]) is not None
    with transaction() as conn:
        assert jobs.begin_effect(conn, jid, "w", "provider:req-1") is True
        assert jobs.defer(conn, jid, "w") is None
        assert _job(jid)["status"] == "running"


def test_deferral_onto_prerequisite_parks_in_waiting():
    """F4: durable depends_on deferral; prerequisite completion re-queues the job."""
    dep = _enqueue("rel_ok")
    jid = _enqueue("rel_ok", priority=90)
    assert _claim("w", ["rel_ok", "live_defense_emergency_stop"]) is not None
    with transaction() as conn:
        assert jobs.defer(conn, jid, "w", depends_on=dep) == "waiting"
        assert jobs.defer(conn, dep, "nobody", depends_on=jid) is None  # wrong owner
        assert jobs.complete(conn, dep, "dep-worker", {}) is False  # never claimed
        assert jobs.claim(conn, "dep-worker", 60, ["rel_ok"]) is not None
        assert jobs.complete(conn, dep, "dep-worker", {}) is True
    assert _job(jid)["status"] == "queued"


def test_enqueue_propagates_an_absolute_deadline():
    """F7 contract: follow-on enqueues share the original absolute deadline."""
    from datetime import timedelta

    from shared.base import utcnow

    deadline = utcnow() + timedelta(seconds=120)
    jid = _enqueue("rel_ok", deadline_at=deadline)
    assert _job(jid)["deadline_at"] == deadline
    # deadline_s still works and a redelivery never extends deadline_at.
    worker.drain(kinds=["rel_ok"])
    assert _job(jid)["deadline_at"] == deadline


def test_off_sweep_cancels_waiting_and_preserves_recovery_evidence():
    """F5: OFF marks waiting FR jobs cancelled; recovery_required keeps evidence."""
    pid = "prj_off"
    parent = _enqueue("rel_ok", project=pid)  # non-FR prerequisite, not swept by OFF
    child = _enqueue("live_defense_agent_step", project=pid, depends_on=parent)
    assert _job(child)["status"] == "waiting"
    unrecoverable = _enqueue("live_defense_agent_step", project=pid, priority=90)
    stop = _enqueue("live_defense_emergency_stop", project=pid)
    other = _enqueue("live_defense_agent_step", project="prj_other")

    assert _claim("w", ["live_defense_agent_step"]) is not None
    _expire(unrecoverable, effect_state="in_flight", effect_key="provider:x")
    with transaction() as conn:
        execution.reconcile(conn)
    assert _job(unrecoverable)["status"] == "recovery_required"
    with transaction() as conn:
        n = modes.cancel_first_responder_jobs(conn, pid)
    assert n == 2
    # Waiting child cancelled with the flag; parent running with flag; recovery job
    # keeps its status and effect evidence but can never be retried.
    assert _job(child)["status"] == "cancelled" and _job(child)["cancel_requested"]
    assert _job(unrecoverable)["status"] == "recovery_required"
    assert _job(unrecoverable)["cancel_requested"] and _job(unrecoverable)["effect_state"]
    # Independently authorized control jobs and other projects are untouched.
    assert _job(stop)["status"] == "queued" and not _job(stop)["cancel_requested"]
    assert _job(other)["status"] in ("queued", "running")
    # Retry resolution after OFF cancellation is refused.
    with transaction() as conn, pytest.raises(execution.RecoveryError):
        execution.resolve_recovery(
            conn, unrecoverable, resolution="retry", actor_id="u", note="after OFF"
        )


def test_off_then_dependency_completion_does_not_revive_cancelled_work():
    """F5: a cancelled waiting job stays cancelled when its prerequisite settles."""
    pid = "prj_off_dep"
    parent = _enqueue("rel_ok", project=pid)
    child = _enqueue("live_defense_agent_step", project=pid, depends_on=parent)
    with transaction() as conn:
        modes.cancel_first_responder_jobs(conn, pid)
    worker.drain(kinds=["rel_ok"])
    assert _job(parent)["status"] == "succeeded"
    assert _job(child)["status"] == "cancelled"


def test_deadline_compatibility_with_database_clock():
    """Codex F10 direction: reconcile must see a DB-clock deadline that already passed."""
    jid = _enqueue("rel_ok", deadline_s=30)
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == jid)
            .values(deadline_at=sa.func.now() - sa.text("interval '1 second'"))
        )
        execution.reconcile(conn)
    assert _job(jid)["status"] == "timed_out"


# --- R5: reconcile derives job uncertainty from the billable-request ledger -------------


def _mk_invocation(conn, pid: str, doc: dict) -> str:
    """Insert the org/project/decision/invocation chain a provider ledger hangs off."""
    from backend.db import (
        first_responder_decisions,
        first_responder_invocations,
        organizations,
        projects,
    )

    org_id, dec_id, inv_id = f"org-{pid}", f"dec-{pid}", f"inv-{pid}"
    conn.execute(sa.insert(organizations).values(id=org_id, name=org_id))
    conn.execute(sa.insert(projects).values(id=pid, org_id=org_id, name=pid, settings={}))
    conn.execute(
        sa.insert(first_responder_decisions).values(
            id=dec_id,
            project_id=pid,
            dedupe_key=f"r5-{pid}",
            action="respond",
            doc={},
        )
    )
    conn.execute(
        sa.insert(first_responder_invocations).values(
            id=inv_id,
            decision_id=dec_id,
            project_id=pid,
            agent="scout",
            status="running",
            doc=doc,
        )
    )
    return inv_id


def _attempt(status: str, seq: int = 1) -> dict:
    return {
        "id": f"att_{seq:04d}",
        "idempotency_key": f"fr-provider:inv:{seq}",
        "status": status,
        "effect_key": f"provider:att_{seq:04d}",
    }


def _update_invocation_doc(inv_id: str, doc: dict) -> None:
    """Rewrite an invocation doc the way the handler's ledger commit would."""
    from backend.db import first_responder_invocations

    with transaction() as conn:
        conn.execute(
            sa.update(first_responder_invocations)
            .where(first_responder_invocations.c.id == inv_id)
            .values(doc=doc)
        )


def test_expired_lease_with_unresolved_provider_ledger_requires_recovery():
    """R5: abrupt death during a provider send -> recovery_required, never requeue."""
    pid = "prj_r5"
    with transaction() as conn:
        inv_id = _mk_invocation(
            conn,
            pid,
            {"execution": {"provider_attempts": [_attempt("settled")]}},
        )
        jid, _ = jobs.enqueue(
            conn,
            "live_defense_agent_step",
            {"project_id": pid, "invocation_id": inv_id},
        )
    assert _claim("dead-worker", ["live_defense_agent_step"]) is not None
    # The handler's reservation commit lands mid-run, then the process dies.
    _update_invocation_doc(
        inv_id,
        {
            "execution": {
                "provider_attempts": [
                    _attempt("settled"),
                    _attempt("in_flight", 2),
                ]
            }
        },
    )
    _expire(jid)
    with transaction() as conn:
        report = execution.reconcile(conn)
    assert report["recovery_required"] == 1 and report["requeued"] == 0
    row = _job(jid)
    assert row["status"] == "recovery_required"
    assert row["error_category"] == "uncertain_side_effect"
    # Owner/attempt fencing and the ledger row's idempotency evidence are audited.
    rec = row["recovery"]
    assert rec["previous_owner"] == "dead-worker" and rec["attempt"] == 1
    assert rec["uncertainty_source"] == "provider_attempt_ledger"
    assert rec["ledger_attempt_id"] == "att_0002"
    assert rec["idempotency_key"] == "fr-provider:inv:2"
    last = _events(jid)[-1]
    assert last["to_status"] == "recovery_required"
    # Audited operator resolution still works on the ledger-derived recovery.
    with transaction() as conn:
        out = execution.resolve_recovery(
            conn, jid, resolution="applied", actor_id="u1", note="provider confirmed 2xx"
        )
        assert out["status"] == "succeeded"


def test_expired_lease_with_uncertain_provider_outcome_flag_requires_recovery():
    """R5: the invocation-level uncertain flag is honoured the same way."""
    pid = "prj_r5_flag"
    with transaction() as conn:
        inv_id = _mk_invocation(conn, pid, {"execution": {"provider_attempts": []}})
        jid, _ = jobs.enqueue(
            conn, "live_defense_agent_step", {"project_id": pid, "invocation_id": inv_id}
        )
    assert _claim("w", ["live_defense_agent_step"]) is not None
    _update_invocation_doc(
        inv_id,
        {
            "execution": {
                "provider_attempts": [_attempt("uncertain")],
                "uncertain_provider_outcome": True,
            }
        },
    )
    _expire(jid)
    with transaction() as conn:
        execution.reconcile(conn)
    assert _job(jid)["status"] == "recovery_required"


def test_expired_lease_with_only_settled_attempts_requeues_normally():
    """R5: a fully settled ledger carries no uncertainty -> normal lease recovery."""
    pid = "prj_r5_settled"
    with transaction() as conn:
        inv_id = _mk_invocation(
            conn,
            pid,
            {"execution": {"provider_attempts": [_attempt("settled"), _attempt("failed", 2)]}},
        )
        jid, _ = jobs.enqueue(
            conn, "live_defense_agent_step", {"project_id": pid, "invocation_id": inv_id}
        )
    assert _claim("w", ["live_defense_agent_step"]) is not None
    _expire(jid)
    with transaction() as conn:
        report = execution.reconcile(conn)
    assert report["requeued"] == 1 and report["recovery_required"] == 0
    assert _job(jid)["status"] == "queued"


def test_expired_lease_with_missing_invocation_requeues_normally():
    """R5: a dangling invocation_id adds no uncertainty."""
    with transaction() as conn:
        jid, _ = jobs.enqueue(
            conn,
            "live_defense_agent_step",
            {"project_id": "prj_r5_missing", "invocation_id": "inv-does-not-exist"},
        )
    assert _claim("w", ["live_defense_agent_step"]) is not None
    _expire(jid)
    with transaction() as conn:
        report = execution.reconcile(conn)
    assert report["requeued"] == 1
    assert _job(jid)["status"] == "queued"


def test_atomic_reservation_committed_with_ledger_write_is_fenced():
    """R5 alt: ledger writers can commit job uncertainty atomically via begin_effect
    inside their own reservation transaction — same recovery_required outcome."""
    pid = "prj_r5_atomic"
    with transaction() as conn:
        inv_id = _mk_invocation(conn, pid, {"execution": {"provider_attempts": []}})
        jid, _ = jobs.enqueue(
            conn, "live_defense_agent_step", {"project_id": pid, "invocation_id": inv_id}
        )
    job = _claim("w", ["live_defense_agent_step"])
    assert job is not None
    with transaction() as conn:
        # Ledger reservation + job uncertainty land in one commit (Grok's contract).
        from backend.db import first_responder_invocations

        conn.execute(
            sa.update(first_responder_invocations)
            .where(first_responder_invocations.c.id == inv_id)
            .values(doc={"execution": {"provider_attempts": [_attempt("in_flight")]}})
        )
        assert jobs.begin_effect(conn, jid, "w", "provider:att_0001") is True
    _expire(jid)
    with transaction() as conn:
        report = execution.reconcile(conn)
    assert report["recovery_required"] == 1
    assert _job(jid)["status"] == "recovery_required"


# --- Codex RC2 regressions (N3 fenced recovery, N4 event sanitization) ---------------


def _invocation_doc(inv_id: str) -> dict:
    from backend.db import first_responder_invocations

    with transaction() as conn:
        return dict(
            conn.execute(
                sa.select(first_responder_invocations.c.doc).where(
                    first_responder_invocations.c.id == inv_id
                )
            ).scalar_one()
        )


def _recovery_required_from_ledger(pid: str, doc: dict) -> tuple[str, str]:
    """Claim clean, land the ledger rows mid-run, expire, reconcile -> recovery."""
    with transaction() as conn:
        inv_id = _mk_invocation(conn, pid, {"execution": {"provider_attempts": []}})
        jid, _ = jobs.enqueue(
            conn, "live_defense_agent_step", {"project_id": pid, "invocation_id": inv_id}
        )
    assert _claim("dead-worker", ["live_defense_agent_step"]) is not None
    _update_invocation_doc(inv_id, doc)
    _expire(jid)
    with transaction() as conn:
        execution.reconcile(conn)
    assert _job(jid)["status"] == "recovery_required"
    return inv_id, jid


def test_fast_reclaim_parks_job_with_unresolved_provider_ledger():
    """N3: claim-time fence — an unresolved billable ledger row is never handed
    to a worker, even when a fast reclaim beats the reconciler to the lease."""
    pid = "prj_n3_claim"
    with transaction() as conn:
        inv_id = _mk_invocation(conn, pid, {"execution": {"provider_attempts": []}})
        jid, _ = jobs.enqueue(
            conn, "live_defense_agent_step", {"project_id": pid, "invocation_id": inv_id}
        )
    assert _claim("dead-worker", ["live_defense_agent_step"]) is not None
    # Reservation commit lands mid-run, then the owning process dies abruptly.
    _update_invocation_doc(inv_id, {"execution": {"provider_attempts": [_attempt("in_flight")]}})
    _expire(jid)
    # Fast reclaim: a fresh worker picks the expired lease before reconcile runs.
    assert _claim("fast-reclaimer", ["live_defense_agent_step"]) is None
    row = _job(jid)
    assert row["status"] == "recovery_required"
    assert row["error_category"] == "uncertain_side_effect"
    rec = row["recovery"]
    assert rec["previous_owner"] == "dead-worker"
    assert rec["uncertainty_source"] == "provider_attempt_ledger"
    assert rec["ledger_attempt_id"] == "att_0001"
    last = _events(jid)[-1]
    assert last["to_status"] == "recovery_required" and last["worker"] == "fast-reclaimer"
    # The parked job stays parked: reconcile never requeues it.
    with transaction() as conn:
        report = execution.reconcile(conn)
    assert report["requeued"] == 0
    assert _job(jid)["status"] == "recovery_required"


def test_queued_job_with_unresolved_ledger_is_parked_at_claim():
    """N3: the claim fence applies to ordinary queued claims too — a job whose
    ledger went unresolved while queued is parked, never executed."""
    pid = "prj_n3_queued"
    with transaction() as conn:
        inv_id = _mk_invocation(
            conn,
            pid,
            {
                "execution": {
                    "provider_attempts": [_attempt("uncertain")],
                    "uncertain_provider_outcome": True,
                }
            },
        )
        jid, _ = jobs.enqueue(
            conn, "live_defense_agent_step", {"project_id": pid, "invocation_id": inv_id}
        )
    assert _claim("w", ["live_defense_agent_step"]) is None
    row = _job(jid)
    assert row["status"] == "recovery_required"
    assert row["recovery"]["invocation_id"] == inv_id


def test_operator_retry_clears_provider_ledger_for_authorized_replay():
    """N3: resolution=retry closes the stale reservation in the same transaction
    as the requeue, so the authorized replay can reserve a fresh attempt."""
    inv_id, jid = _recovery_required_from_ledger(
        "prj_n3_retry", {"execution": {"provider_attempts": [_attempt("in_flight")]}}
    )
    with transaction() as conn:
        out = execution.resolve_recovery(
            conn, jid, resolution="retry", actor_id="op1", note="provider confirmed 4xx"
        )
    assert out["status"] == "queued"
    doc = _invocation_doc(inv_id)
    attempts = doc["execution"]["provider_attempts"]
    assert attempts[0]["status"] == "cancelled"
    assert attempts[0]["error_code"].startswith("OPERATOR_AUTHORIZED_RETRY")
    assert attempts[0]["settled_at"] is not None
    assert doc["execution"]["uncertain_provider_outcome"] is False
    # The replay is allowed through the same claim fence — nothing unresolved left.
    job = _claim("retry-worker", ["live_defense_agent_step"])
    assert job is not None and job.id == jid


def test_operator_applied_marks_provider_ledger_settled():
    """N3: resolution=applied marks the uncertain attempt settled so the
    accounted outcome is never replayed."""
    inv_id, jid = _recovery_required_from_ledger(
        "prj_n3_applied",
        {
            "execution": {
                "provider_attempts": [_attempt("settled"), _attempt("sent", 2)],
                "uncertain_provider_outcome": True,
            }
        },
    )
    with transaction() as conn:
        out = execution.resolve_recovery(
            conn, jid, resolution="applied", actor_id="op2", note="provider confirmed 2xx"
        )
    assert out["status"] == "succeeded"
    doc = _invocation_doc(inv_id)
    attempts = doc["execution"]["provider_attempts"]
    assert attempts[0]["status"] == "settled"  # untouched evidence
    assert attempts[0].get("error_code") is None
    assert attempts[1]["status"] == "settled"
    assert attempts[1]["error_code"].startswith("OPERATOR_CONFIRMED_APPLIED")
    assert doc["execution"]["uncertain_provider_outcome"] is False


def test_operator_not_applied_cancels_provider_ledger():
    """N3: resolution=not_applied closes the stale reservation as cancelled."""
    inv_id, jid = _recovery_required_from_ledger(
        "prj_n3_na", {"execution": {"provider_attempts": [_attempt("reserved")]}}
    )
    with transaction() as conn:
        out = execution.resolve_recovery(
            conn, jid, resolution="not_applied", actor_id="op3", note="never sent"
        )
    assert out["status"] == "failed"
    attempts = _invocation_doc(inv_id)["execution"]["provider_attempts"]
    assert attempts[0]["status"] == "cancelled"
    assert attempts[0]["error_code"].startswith("OPERATOR_CONFIRMED_NOT_APPLIED")


def test_failure_reason_redacted_in_events_and_last_error():
    """N4: raw exception text carrying credentials never reaches job_events
    or last_error — reason, details and stored error are all sanitized."""
    marker = "unit-only-password"
    jid = _enqueue("rel_fail")
    assert _claim("w1", ["rel_fail"]) is not None
    with transaction() as conn:
        jobs.fail(
            conn,
            jid,
            "w1",
            {
                "code": "TOOL_ERROR",
                "message": f"connect postgresql://svc:{marker}@db.internal/app failed",
            },
            retryable=False,
        )
    row = _job(jid)
    assert marker not in str(row["last_error"])
    with transaction() as conn:
        raw = conn.execute(
            sa.select(job_events.c.reason, job_events.c.details).where(job_events.c.job_id == jid)
        ).all()
    assert marker not in str([dict(r._mapping) for r in raw])
    assert marker not in str(_events(jid))


def test_history_read_scrubs_legacy_unsanitized_events():
    """N4: events persisted before write-side redaction are scrubbed at read."""
    marker = "legacy-only-password"
    jid = _enqueue("rel_ok")
    with transaction() as conn:
        conn.execute(
            sa.insert(job_events).values(
                job_id=jid,
                project_id="prj_a",
                kind="rel_ok",
                from_status="running",
                to_status="failed",
                attempt=1,
                category="permanent",
                reason=f"connect postgresql://svc:{marker}@db.internal/app failed",
                details={"message": f"driver said password={marker} retry"},
            )
        )
    events = _events(jid)
    assert marker not in str(events)
    last = events[-1]
    assert "[REDACTED" in last["reason"]
    assert "[REDACTED" in last["details"]["message"]


def test_diff_artifact_preserves_non_secret_code_expressions(tmp_path):
    """N7: free-text key=value scrubbing must not corrupt persisted diff code."""
    import ast

    from backend.artifacts import ArtifactStore
    from shared.evidence import ArtifactKind

    source = 'api_key=os.environ["API_KEY"]\npassword=os.environ.get("DB_PASSWORD")\n'
    ast.parse(source)
    store = ArtifactStore(tmp_path.as_uri())
    with transaction() as conn:
        ref = store.put_text(conn, source, kind=ArtifactKind.diff, project_id=None)
    with transaction() as conn:
        _, body = store.get(conn, ref.id)
    actual = body.decode()
    assert actual == source, "code-aware redaction rewrote a non-secret expression"
    ast.parse(actual)


def test_free_text_and_code_redaction_split(tmp_path):
    """N7: secrets still redact in both modes; expressions only survive in code."""
    import ast

    from backend.artifacts import ArtifactStore
    from backend.redaction import (
        find_high_confidence_secret,
        redact_code,
        redact_text,
    )
    from shared.evidence import ArtifactKind

    # Free text: credential-looking key=value fragments are scrubbed.
    assert "hunter2" not in redact_text("connect failed: password=hunter2 api-key=hunter2")
    # Code: unquoted expressions survive; quoted secret literals still redact.
    expr = 'password=os.environ.get("DB_PASSWORD")\n'
    assert redact_code(expr) == expr
    redacted_literal = redact_code('password="hunter2"\n')
    assert "hunter2" not in redacted_literal
    ast.parse(redacted_literal)
    # A real credential inside a code literal is still caught.
    code = 'dsn = "postgresql://svc:hunter2@db.internal/app"\n'
    out = redact_code(code)
    assert "hunter2" not in out
    ast.parse(out)
    # Diff artifacts with an actual embedded secret lose the secret, not syntax.
    store = ArtifactStore(tmp_path.as_uri())
    with transaction() as conn:
        ref = store.put_text(conn, code, kind=ArtifactKind.diff, project_id=None)
    with transaction() as conn:
        _, body = store.get(conn, ref.id)
    assert "hunter2" not in body.decode()
    # Label preference: the transport-DSN pattern names postgres DSNs.
    assert find_high_confidence_secret("postgresql://svc:hunter2@db.internal") == "dsn_credentials"


@pytest.mark.parametrize(
    "source",
    [
        'settings = {"password": "n8-dict-secret"}\n',
        "password: str = 'n8-typed-secret'\n",
        'd["api_key"] = "n8-subscript-secret"\n',
    ],
)
def test_code_redaction_covers_quoted_keys_and_annotations(source):
    """N8: quoted mapping keys, annotated and subscript assignments redact."""
    import ast

    from backend.redaction import redact_code

    out = redact_code(source)
    for marker in ("n8-dict-secret", "n8-typed-secret", "n8-subscript-secret"):
        assert marker not in out
    assert "[REDACTED:kv]" in out
    ast.parse(out)


def test_code_redaction_still_preserves_annotated_nonliteral():
    """N8 vs N7: typed non-secret expressions stay byte-identical."""
    from backend.redaction import redact_code

    for expr in (
        'password: str = os.getenv("DB_PASSWORD")\n',
        'api_key=os.environ["API_KEY"]\n',
        '{"password": os.environ["P"]}\n',
    ):
        assert redact_code(expr) == expr


@pytest.mark.parametrize(
    "source",
    [
        'password: str; notice = "public notice"\n',
        'password: str # note = "x"\n',
        'password: str == "comparison-not-assignment"\n',
    ],
)
def test_annotation_matcher_stays_inside_one_statement(source):
    """N9: the annotated-assignment span may not cross ; or # into another
    statement, nor treat a comparison as an assignment."""
    import ast

    from backend.redaction import redact_code

    ast.parse(source)
    actual = redact_code(source)
    ast.parse(actual)
    assert actual == source, f"annotation matcher rewrote an unrelated statement: {actual!r}"


def test_annotation_boundary_still_redacts_real_typed_secret(tmp_path):
    """N9 control: a real annotated literal still redacts in stored diffs."""
    from backend.artifacts import ArtifactStore
    from shared.evidence import ArtifactKind

    diff = '--- a/config.py\n+++ b/config.py\n@@ -0,0 +1 @@\n+password: str = "n9-typed-secret"\n'
    store = ArtifactStore(tmp_path.as_uri())
    with transaction() as conn:
        ref = store.put_text(conn, diff, kind=ArtifactKind.diff, project_id=None)
    with transaction() as conn:
        row, body = store.get(conn, ref.id)
    assert row["redacted"] is True
    assert "n9-typed-secret" not in body.decode()
