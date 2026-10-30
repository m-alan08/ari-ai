"""#694 x #693 integration: the per-repository write lease spans PR review arbitration, the
write-token mint inside the POST, the POST and the effect settlement, for review jobs queued
with ``repo_id`` and for legacy ones without it."""

# ruff: noqa: F811  (pytest fixtures imported from test_auto_commits)

import threading

import pytest
import sqlalchemy as sa
from test_auto_commits import automatic, seed_pr_assessment  # noqa: F401
from test_review_pipeline_reliability import audits

from backend import worker
from backend.db import jobs, transaction


def _job():
    with transaction() as conn:
        return conn.execute(sa.select(jobs).where(jobs.c.kind == "post_pr_review")).mappings().one()


@pytest.mark.parametrize("legacy_payload", [False, True])
def test_disconnect_waits_for_review_post_and_its_settlement(
    automatic, monkeypatch, legacy_payload
):
    alice, bob, ids, _ = automatic
    seed_pr_assessment(ids, bob)
    if legacy_payload:
        with transaction() as conn:
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.kind == "post_pr_review")
                .values(payload=jobs.c.payload.op("-")("repo_id"))
            )
    events: list[str] = []
    entered, release = threading.Event(), threading.Event()

    class GitHub:
        def post_pull_request_review(self, *args):
            events.append("write_token+review")
            entered.set()
            assert release.wait(15)

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    writer = threading.Thread(
        target=worker.run_one, args=("review-writer", ["post_pr_review"]), daemon=True
    )
    writer.start()
    assert entered.wait(10), "the review job never posted"

    def disconnect() -> None:
        code = alice.post(f"/api/v1/repositories/{ids['repo']}/disconnect").status_code
        events.append(f"disconnect:{code}")
        events.append(f"effect_at_ack:{_job()['effect_state']}")

    t = threading.Thread(target=disconnect, daemon=True)
    t.start()
    t.join(1.5)
    try:
        assert t.is_alive(), f"disconnect returned while the review POST was in flight: {events}"
    finally:
        release.set()
        writer.join(15)
        t.join(15)
    assert events == ["write_token+review", "disconnect:200", "effect_at_ack:settled"], events
    # The disconnect cancels a linked job once the lease is free. If that lands before the
    # worker records success, the settled effect is reported for operator review
    # (recovery_required, jobs.fail precedence); it is never re-posted.
    assert _job()["effect_state"] == "settled"
    assert _job()["status"] in ("succeeded", "recovery_required")
    assert len(audits("monitoring.pr_review_posted")) == 1
    worker.drain(kinds=["post_pr_review"])
    assert events.count("write_token+review") == 1


@pytest.mark.parametrize("legacy_payload", [False, True])
def test_review_after_disconnect_returned_200_is_an_audited_skip(
    automatic, monkeypatch, legacy_payload
):
    """The disconnect commits after the handler's pre-check but before the lease: the lease's
    re-check (or the reservation) stops it, and a legacy job records why."""
    alice, bob, ids, _ = automatic
    seed_pr_assessment(ids, bob)
    if legacy_payload:
        with transaction() as conn:
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.kind == "post_pr_review")
                .values(payload=jobs.c.payload.op("-")("repo_id"))
            )
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)

    def disconnect_then_client():
        assert alice.post(f"/api/v1/repositories/{ids['repo']}/disconnect").status_code == 200
        return GitHub()

    monkeypatch.setattr("backend.auto_commits.github_client", disconnect_then_client)
    worker.drain(kinds=["post_pr_review"])
    assert calls == []
    assert not audits("monitoring.pr_review_posted")
    if legacy_payload:
        assert _job()["status"] == "succeeded"
        assert audits("monitoring.pr_review_skipped")
    else:
        assert _job()["status"] == "cancelled"
