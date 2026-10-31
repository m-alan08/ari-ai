"""PR review effect ownership: one job owns a review before any POST, and an ambiguous
provider answer is reconciled against GitHub instead of being retried blindly."""

# ruff: noqa: F811  (pytest fixtures imported from test_auto_commits)

import json
import threading

import httpx
import sqlalchemy as sa
from test_auto_commits import automatic, seed_pr_assessment  # noqa: F401
from test_review_pipeline_reliability import audits, http_error, job, make_due, pr_review_jobs

from backend import worker
from backend.db import jobs, transaction
from backend.github import GitHubApp


def legacy_and_per_pr_jobs(ids, bob):
    """A review job queued before per-PR keys existed (older id, no ``pr_number``) plus the
    per-PR job the upgraded code enqueues for the same review."""
    from backend import jobs as job_queue

    assessment_id = seed_pr_assessment(ids, bob)
    with transaction() as conn:
        legacy_id = conn.execute(
            sa.update(jobs)
            .where(jobs.c.kind == "post_pr_review")
            .values(
                dedupe_key=f"prreview:{assessment_id}", payload={"assessment_id": assessment_id}
            )
            .returning(jobs.c.id)
        ).scalar_one()
        # A per-PR job for the same review under its own key, as the PR-link path enqueues.
        job_queue.enqueue(
            conn,
            "post_pr_review",
            {"assessment_id": assessment_id, "repo_id": ids["repo"], "pr_number": 7},
            dedupe_key=f"prreview:{assessment_id}:pr:7",
            priority=80,
        )
    [new_id] = [r["id"] for r in pr_review_jobs() if r["id"] != legacy_id]
    assert legacy_id < new_id  # the legacy job is the older one
    return legacy_id, new_id


def delay(job_id, seconds):
    with transaction() as conn:
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == job_id)
            .values(run_after=sa.func.now() + sa.text(f"interval '{seconds} seconds'"))
        )


def row(job_id):
    with transaction() as conn:
        return conn.execute(sa.select(jobs).where(jobs.c.id == job_id)).mappings().one()


def test_due_legacy_retry_cannot_preempt_a_newer_in_flight_review(automatic, monkeypatch):
    _, bob, ids, _ = automatic
    legacy_id, new_id = legacy_and_per_pr_jobs(ids, bob)
    delay(legacy_id, 3600)
    calls = []
    started, release = threading.Event(), threading.Event()

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)
            if len(calls) == 1:  # the newer job's POST hangs at GitHub
                started.set()
                assert release.wait(30)

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    first = threading.Thread(target=worker.run_one, args=("worker-a", ["post_pr_review"]))
    first.start()
    second_ran: list[str | None] = []
    second = threading.Thread(
        target=lambda: second_ran.append(worker.run_one("worker-b", ["post_pr_review"]))
    )
    try:
        assert started.wait(30)
        assert row(new_id)["status"] == "running"
        assert row(new_id)["effect_state"] == "in_flight"
        make_due("post_pr_review")  # the older legacy retry becomes due meanwhile
        second.start()
        second.join(2)  # it defers at once, or waits on the repository write lease (#694)
        assert len(calls) == 1, "the older job posted while the newer job owned the review"
        if not second.is_alive():
            assert second_ran == [legacy_id]
            assert row(legacy_id)["status"] == "queued"
            assert row(legacy_id)["effect_state"] is None
    finally:
        release.set()
        first.join(30)
        second.join(30)
    assert row(new_id)["status"] == "succeeded"
    make_due("post_pr_review")
    worker.drain(kinds=["post_pr_review"])
    assert len(calls) == 1
    assert len(audits("monitoring.pr_review_posted")) == 1
    assert row(legacy_id)["status"] == "succeeded"
    assert any(
        a["details"]["reason"] == "review already posted"
        for a in audits("monitoring.pr_review_skipped")
    )


def test_uncertain_review_on_a_newer_job_blocks_the_older_job(automatic, monkeypatch):
    _, bob, ids, _ = automatic
    legacy_id, new_id = legacy_and_per_pr_jobs(ids, bob)
    delay(legacy_id, 3600)
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)
            raise httpx.ReadTimeout("response lost", request=httpx.Request("POST", "https://x"))

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert row(new_id)["status"] == "recovery_required"
    make_due("post_pr_review")
    worker.drain(kinds=["post_pr_review"])
    assert len(calls) == 1
    assert row(legacy_id)["status"] == "succeeded"
    assert any(
        a["details"]["reason"] == "another review attempt is uncertain"
        for a in audits("monitoring.pr_review_skipped")
    )
    assert not audits("monitoring.pr_review_posted")


def test_review_accepted_then_http_500_is_reconciled_not_reposted(automatic, monkeypatch):
    _, bob, ids, _ = automatic
    seed_pr_assessment(ids, bob)
    bodies, lookups = [], []

    class GitHub:
        def post_pull_request_review(self, installation, repo, number, body):
            bodies.append(body)  # GitHub stores the review, then the response fails
            raise http_error(500, method="POST")

        def pull_request_review_has_marker(self, installation, repo, number, marker):
            lookups.append(marker)
            return any(marker in body for body in bodies)

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    make_due("post_pr_review")
    worker.drain(kinds=["post_pr_review"])
    assert len(bodies) == 1
    assert lookups and lookups[0] in bodies[0]
    assert job("post_pr_review")["status"] == "succeeded"
    assert len(audits("monitoring.pr_review_posted")) == 1
    assert not audits("monitoring.pr_review_failed")


def test_http_500_with_unknown_outcome_requires_recovery(automatic, monkeypatch):
    _, bob, ids, _ = automatic
    seed_pr_assessment(ids, bob)
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)
            raise http_error(502, method="POST")

        def pull_request_review_has_marker(self, *args):
            raise httpx.ConnectError("github unavailable")

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert job("post_pr_review")["status"] == "recovery_required"
    make_due("post_pr_review")
    worker.drain(kinds=["post_pr_review"])
    assert len(calls) == 1
    assert not audits("monitoring.pr_review_posted")


def test_http_500_confirmed_absent_on_github_is_retried(automatic, monkeypatch):
    _, bob, ids, _ = automatic
    seed_pr_assessment(ids, bob)
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)
            if len(calls) == 1:
                raise http_error(503, method="POST")

        def pull_request_review_has_marker(self, *args):
            return False

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert job("post_pr_review")["status"] == "queued"
    make_due("post_pr_review")
    worker.drain(kinds=["post_pr_review"])
    assert len(calls) == 2
    assert job("post_pr_review")["status"] == "succeeded"
    assert len(audits("monitoring.pr_review_posted")) == 1


def _app(pages):
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        page = int(request.url.params["page"])
        return httpx.Response(
            200, content=json.dumps(pages[page - 1] if page <= len(pages) else [])
        )

    app = object.__new__(GitHubApp)
    app._client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(respond)
    )
    app.installation_token = lambda *_args, **_kwargs: "read-token"
    return app, seen


def test_review_marker_lookup_pages_through_reviews():
    marker = "<!-- ari-review:prreview:asm_x:pr:7 -->"
    filler = [{"body": "other"} for _ in range(100)]
    app, seen = _app([filler, [{"body": f"review{marker}"}]])
    assert app.pull_request_review_has_marker(99, "acme/payments-lite", 7, marker) is True
    assert [s["page"] for s in seen] == ["1", "2"]
    app, _ = _app([filler, [{"body": None}]])
    assert app.pull_request_review_has_marker(99, "acme/payments-lite", 7, marker) is False
