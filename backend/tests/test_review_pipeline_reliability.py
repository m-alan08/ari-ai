"""Failure injection across the GitHub push -> assessment -> PR review lifecycle."""

# ruff: noqa: F811  (pytest fixtures imported from test_auto_commits)

import threading

import httpx
import pytest
import sqlalchemy as sa
from helpers import BASE, HEAD, post_webhook, push_payload
from test_auto_commits import automatic, push, records, seed_pr_assessment  # noqa: F401

from backend import worker
from backend.db import (
    assessments,
    audit_events,
    jobs,
    monitoring_events,
    reassessment_requests,
    transaction,
    webhook_deliveries,
)

OTHER = "c" * 40


def http_error(code, *, text="", headers=None, method="GET"):
    request = httpx.Request(method, "https://api.github.com/repos/acme/payments-lite")
    response = httpx.Response(code, request=request, text=text, headers=headers or {})
    return httpx.HTTPStatusError(str(code), request=request, response=response)


def job(kind):
    with transaction() as conn:
        return conn.execute(sa.select(jobs).where(jobs.c.kind == kind)).mappings().one()


def make_due(kind):
    with transaction() as conn:
        conn.execute(sa.update(jobs).where(jobs.c.kind == kind).values(run_after=sa.func.now()))


def pr_payload(sha=HEAD, number=7, action="synchronize"):
    return {
        "repository": {"full_name": "acme/payments-lite"},
        "installation": {"id": 99},
        "sender": {"login": "adamissac"},
        "action": action,
        "number": number,
        "pull_request": {
            "head": {"sha": sha, "ref": "feature"},
            "base": {"sha": BASE, "ref": "main"},
            "user": {"login": "adamissac"},
            "updated_at": "2026-10-17T01:02:03Z",
        },
    }


def complete(assessment_id):
    from backend.host import AssessmentHost
    from shared.assessment import AssessmentState

    with transaction() as conn:
        doc = conn.execute(
            sa.select(assessments.c.doc).where(assessments.c.id == assessment_id)
        ).scalar_one()
        doc["state"] = "REPORTING"
        conn.execute(
            sa.update(assessments)
            .where(assessments.c.id == assessment_id)
            .values(state="REPORTING", doc=doc)
        )
    AssessmentHost(assessment_id).transition(AssessmentState.COMPLETED, "test completion")


def pr_review_jobs():
    with transaction() as conn:
        return list(conn.execute(sa.select(jobs).where(jobs.c.kind == "post_pr_review")).mappings())


def audits(action):
    with transaction() as conn:
        return [
            d
            for d in conn.execute(sa.select(audit_events.c.doc)).scalars()
            if d["action"] == action
        ]


# --- webhook reception ---------------------------------------------------------------


def test_tampered_body_is_rejected_before_anything_is_persisted(automatic):
    alice, bob, ids, _ = automatic
    from helpers import WEBHOOK_SECRET

    from backend.github import sign

    good = sign(WEBHOOK_SECRET.encode(), b'{"ref": "refs/heads/main"}')
    response = post_webhook(alice, push_payload(["a.py"]), signature=good)
    assert response.status_code == 401
    assert post_webhook(alice, push_payload(["a.py"]), secret="wrong").status_code == 401
    with transaction() as conn:
        assert (
            conn.execute(sa.select(sa.func.count()).select_from(webhook_deliveries)).scalar() == 0
        )
        assert conn.execute(sa.select(sa.func.count()).select_from(jobs)).scalar() == 0


def test_non_default_branch_push_is_reviewed_and_tag_push_is_not(automatic):
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    p = push_payload(["README.md"], head=HEAD)
    p["ref"] = "refs/heads/feature/x"
    p["sender"] = {"login": "adamissac"}
    p["commits"] = [{"id": HEAD, "modified": ["a"], "author": {"username": "adamissac"}}]
    assert post_webhook(alice, p).status_code == 202
    tag = dict(p, ref="refs/tags/v1", after=OTHER)
    assert post_webhook(alice, tag).status_code == 202
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == [HEAD]


def test_redelivery_after_dead_processing_job_is_processed_again(automatic):
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    p = push_payload(["README.md"], head=HEAD)
    p["sender"] = {"login": "adamissac"}
    p["commits"] = [{"id": HEAD, "modified": ["a"], "author": {"username": "adamissac"}}]
    delivery = "6f0b5c1e-0000-4000-8000-000000000001"
    first = post_webhook(alice, p, delivery=delivery)
    assert first.status_code == 202
    with transaction() as conn:  # e.g. DB outage outlasted the retry budget
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.kind == "process_github_delivery")
            .values(status="dead", lease_owner=None, lease_expires_at=None)
        )
    again = post_webhook(alice, p, delivery=delivery)
    assert again.status_code == 202, again.json()
    assert again.json()["accepted"] is True and again.json()["job_id"] != first.json()["job_id"]
    third = post_webhook(alice, p, delivery=delivery)  # live retry job: plain duplicate
    assert third.status_code == 200 and third.json()["job_id"] == again.json()["job_id"]
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == [HEAD]


def test_redelivery_of_successful_delivery_stays_duplicate(automatic):
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    p = push_payload(["README.md"], head=HEAD)
    p["sender"] = {"login": "adamissac"}
    p["commits"] = [{"id": HEAD, "modified": ["a"], "author": {"username": "adamissac"}}]
    delivery = "6f0b5c1e-0000-4000-8000-000000000002"
    assert post_webhook(alice, p, delivery=delivery).status_code == 202
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert post_webhook(alice, p, delivery=delivery).status_code == 200
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == [HEAD]


# --- provider failures ----------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        http_error(403, text="API rate limit exceeded", headers={"x-ratelimit-remaining": "0"}),
        http_error(429, headers={"retry-after": "120"}),
    ],
)
def test_github_rate_limit_defers_commit_without_burning_attempts(automatic, monkeypatch, error):
    alice, bob, ids, _ = automatic

    def limited(self, installation, repo, sha):
        raise error

    monkeypatch.setattr("backend.auto_commits.GitHubApp.commit", limited)
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)
    row = job("assess_github_commit")
    assert row["status"] == "queued"
    assert row["attempts"] - row["defer_count"] == 0  # retry budget untouched
    assert records() == []


def test_exhausted_transient_github_failure_is_recorded_for_the_user(automatic, monkeypatch):
    alice, bob, ids, _ = automatic

    def flaky(self, installation, repo, sha):
        raise http_error(502)

    monkeypatch.setattr("backend.auto_commits.GitHubApp.commit", flaky)
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)
    for _ in range(10):
        if job("assess_github_commit")["status"] != "queued":
            break
        make_due("assess_github_commit")
        worker.drain(kinds=["assess_github_commit"])
    row = job("assess_github_commit")
    assert row["status"] == "succeeded"
    with transaction() as conn:
        event = conn.execute(sa.select(monitoring_events.c.doc)).scalar_one()
        rrq = conn.execute(sa.select(reassessment_requests.c.doc)).scalar_one()
    assert event["status"] == "ignored"
    assert rrq["status"] == "skipped" and "502" in rrq["reason"]
    events = alice.get(f"/api/v1/projects/{ids['project']}/events").json()["items"]
    assert "502" in (events[0]["status_reason"] or "")


@pytest.mark.parametrize(
    "error",
    [
        http_error(429, headers={"retry-after": "60"}, method="POST"),
        http_error(403, text="You have exceeded a secondary rate limit", method="POST"),
    ],
)
def test_pr_review_rate_limit_is_retried_not_dropped(automatic, monkeypatch, error):
    seed_pr_assessment(automatic[2], automatic[1])
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)
            raise error

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    [row] = pr_review_jobs()
    assert len(calls) == 1
    assert row["status"] == "queued"
    assert not audits("monitoring.pr_review_failed")


# --- association and delivery ---------------------------------------------------------


def test_pull_request_after_push_of_same_commit_still_gets_review(automatic, monkeypatch):
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)  # GitHub sends push before pull_request.synchronize
    assert post_webhook(alice, pr_payload(), event="pull_request").status_code == 202
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == [HEAD]  # still one assessment for the commit
    with transaction() as conn:
        assessment_id = conn.execute(sa.select(assessments.c.id)).scalar_one()
    complete(assessment_id)
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert [c[1:3] for c in calls] == [("acme/payments-lite", 7)]


def test_pull_request_for_already_completed_commit_posts_review(automatic, monkeypatch):
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)
    with transaction() as conn:
        assessment_id = conn.execute(sa.select(assessments.c.id)).scalar_one()
    complete(assessment_id)
    assert pr_review_jobs() == []  # plain push: no PR to comment on
    assert post_webhook(alice, pr_payload(), event="pull_request").status_code == 202
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert [c[2] for c in calls] == [7]
    assert records() == [HEAD]


def test_pr_review_is_not_posted_after_repository_disconnect(automatic, monkeypatch):
    alice, bob, ids, _ = automatic
    seed_pr_assessment(ids, bob)
    assert alice.post(f"/api/v1/repositories/{ids['repo']}/disconnect").status_code == 200
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert calls == []
    assert not audits("monitoring.pr_review_posted")


# --- disconnect, quota ----------------------------------------------------------------


def test_disconnect_while_commit_lookup_is_in_flight_creates_no_assessment(automatic, monkeypatch):
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")

    def disconnect_mid_flight(self, installation, repo, sha):
        assert alice.post(f"/api/v1/repositories/{ids['repo']}/disconnect").status_code == 200
        return {"sha": sha, "author": {"login": "adamissac"}}

    monkeypatch.setattr("backend.auto_commits.GitHubApp.commit", disconnect_mid_flight)
    push(alice, HEAD)
    assert records() == []
    assert job("assess_github_commit")["status"] in {"cancelled", "succeeded"}


def test_concurrent_workers_respect_hourly_quota(automatic, settings_override):
    alice, bob, ids, _ = automatic
    settings_override(project_assessments_per_hour=2)
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    shas = [f"{i:040x}" for i in range(1, 7)]
    p = push_payload(["README.md"], head=shas[-1])
    p["sender"] = {"login": "adamissac"}
    p["commits"] = [{"id": s, "modified": ["a"], "author": {"username": "adamissac"}} for s in shas]
    assert post_webhook(alice, p).status_code == 202
    worker.drain(kinds=["process_github_delivery"])
    barrier = threading.Barrier(len(shas))
    errors = []

    def run(i):
        try:
            barrier.wait()
            worker.run_one(f"quota-worker-{i}", ["assess_github_commit"])
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(shas))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(records()) == 2
    with transaction() as conn:
        statuses = sorted(conn.execute(sa.select(reassessment_requests.c.status)).scalars())
    assert statuses.count("skipped") == 4


# --- reviewer filters -----------------------------------------------------------------


@pytest.mark.parametrize("login", ["bob", "octo", "matt", "scott"])
def test_default_bot_exclusion_does_not_skip_human_logins(automatic, settings_override, login):
    alice, bob, ids, identity = automatic
    settings_override(github_auto_commit_authors="*")
    identity["login"] = login
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    p = push_payload(["README.md"], head=HEAD)
    p["sender"] = {"login": login}
    p["commits"] = [{"id": HEAD, "modified": ["a"], "author": {"username": login}}]
    assert post_webhook(alice, p).status_code == 202
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == [HEAD]


@pytest.mark.parametrize("login", ["dependabot[bot]", "Renovate[BOT]"])
def test_default_bot_exclusion_still_skips_bots(automatic, settings_override, login):
    alice, bob, ids, identity = automatic
    settings_override(github_auto_commit_authors="*")
    identity["login"] = login
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    p = push_payload(["README.md"], head=HEAD)
    p["sender"] = {"login": login}
    p["commits"] = [{"id": HEAD, "modified": ["a"], "author": {"username": login}}]
    assert post_webhook(alice, p).status_code == 202
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == []


# --- Codex independent review: revocation race, lost responses, several PRs per commit --


@pytest.mark.parametrize("legacy_payload", [False, True])
def test_disconnect_committed_after_precheck_still_blocks_the_review_post(
    automatic, monkeypatch, legacy_payload
):
    """The disconnect lands between the handler's pre-check and the GitHub call. A job
    queued before ``repo_id`` was in the payload is not cancelled by the disconnect, so
    only the locked re-check right before the POST protects it."""
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
        assert job("post_pr_review")["status"] == "succeeded"
        assert audits("monitoring.pr_review_skipped")
    else:
        assert job("post_pr_review")["status"] == "cancelled"


def test_disconnect_cancels_queued_pr_review_jobs(automatic):
    alice, bob, ids, _ = automatic
    seed_pr_assessment(ids, bob)
    assert job("post_pr_review")["status"] == "queued"
    assert alice.post(f"/api/v1/repositories/{ids['repo']}/disconnect").status_code == 200
    assert job("post_pr_review")["status"] == "cancelled"


def test_lost_review_response_is_never_replayed(automatic, monkeypatch):
    """GitHub may have created the review; the job must not post again on its own."""
    _, bob, ids, _ = automatic
    seed_pr_assessment(ids, bob)
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)
            raise httpx.ReadTimeout("response lost", request=httpx.Request("POST", "https://x"))

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert len(calls) == 1
    row = job("post_pr_review")
    assert row["status"] == "recovery_required", row["status"]
    make_due("post_pr_review")
    worker.drain(kinds=["post_pr_review"])
    assert len(calls) == 1  # an operator decides; nothing replays an uncertain effect


def test_two_pull_requests_sharing_a_commit_both_get_reviews(automatic, monkeypatch):
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)
    assert post_webhook(alice, pr_payload(number=7), event="pull_request").status_code == 202
    assert post_webhook(alice, pr_payload(number=8), event="pull_request").status_code == 202
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == [HEAD]  # one assessment for the commit
    with transaction() as conn:
        assessment_id = conn.execute(sa.select(assessments.c.id)).scalar_one()
    complete(assessment_id)
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)

        def commit(self, installation, repo, sha):
            return {"sha": sha, "author": {"login": "adamissac"}}

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert sorted(c[2] for c in calls) == [7, 8]
    # A third PR opened after the assessment finished gets its own review, nothing is reposted.
    assert post_webhook(alice, pr_payload(number=9), event="pull_request").status_code == 202
    worker.drain(kinds=["process_github_delivery", "assess_github_commit", "post_pr_review"])
    assert sorted(c[2] for c in calls) == [7, 8, 9]
    assert records() == [HEAD]


# --- duplicate delivery -----------------------------------------------------------------


def test_concurrent_identical_deliveries_process_once(automatic):
    """GitHub retries fast: two identical deliveries arrive at the same moment."""
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    p = push_payload(["README.md"], head=HEAD)
    p["sender"] = {"login": "adamissac"}
    p["commits"] = [{"id": HEAD, "modified": ["README.md"], "author": {"username": "adamissac"}}]
    delivery = "22222222-aaaa-bbbb-cccc-000000000002"
    gate = threading.Barrier(4)
    results = []

    def deliver():
        gate.wait()
        results.append(post_webhook(alice, p, delivery=delivery))

    threads = [threading.Thread(target=deliver) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.status_code for r in results) == [200, 200, 200, 202]
    acks = [r.json() for r in results]
    accepted = [a for a in acks if a["accepted"]]
    assert len(accepted) == 1
    assert all(a["job_id"] == accepted[0]["job_id"] for a in acks)  # losers point at the job
    with transaction() as conn:
        assert (
            conn.execute(sa.select(sa.func.count()).select_from(webhook_deliveries)).scalar_one()
            == 1
        )
        kinds = list(
            conn.execute(
                sa.select(jobs.c.kind).where(jobs.c.kind == "process_github_delivery")
            ).scalars()
        )
    assert len(kinds) == 1
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == [HEAD]


def test_legacy_review_job_and_per_pr_job_post_once(automatic, monkeypatch):
    """A review job queued before per-PR keys existed must not post alongside the new one."""
    _, bob, ids, _ = automatic
    assessment_id = seed_pr_assessment(ids, bob)
    with transaction() as conn:
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.kind == "post_pr_review")
            .values(
                dedupe_key=f"prreview:{assessment_id}", payload={"assessment_id": assessment_id}
            )
        )
        from backend.host import _enqueue_pr_review
        from shared.assessment import Assessment

        doc = conn.execute(
            sa.select(assessments.c.doc).where(assessments.c.id == assessment_id)
        ).scalar_one()
        _enqueue_pr_review(conn, Assessment.model_validate(doc))  # the upgraded code path
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert [c[2] for c in calls] == [7]


# --- self-audit: concurrent PR linking, uncertain sibling reviews, dead legacy jobs ------


def test_concurrent_pr_links_for_one_commit_keep_both_prs(automatic, monkeypatch):
    """Two workers link PR 7 and PR 8 to the same assessed commit at the same moment."""
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)
    with transaction() as conn:
        assessment_id = conn.execute(sa.select(assessments.c.id)).scalar_one()
    complete(assessment_id)
    assert post_webhook(alice, pr_payload(number=7), event="pull_request").status_code == 202
    assert post_webhook(alice, pr_payload(number=8), event="pull_request").status_code == 202
    worker.drain(kinds=["process_github_delivery"])
    gate = threading.Barrier(2)

    def commit(self, installation, repo, sha):
        gate.wait(timeout=10)  # both linking jobs are past their pre-checks together
        return {"sha": sha, "author": {"login": "adamissac"}}

    monkeypatch.setattr("backend.auto_commits.GitHubApp.commit", commit)
    threads = [
        threading.Thread(target=worker.run_one, args=(f"w{i}", ["assess_github_commit"]))
        for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    from backend.host import linked_pull_requests
    from shared.assessment import Assessment

    with transaction() as conn:
        doc = conn.execute(
            sa.select(assessments.c.doc).where(assessments.c.id == assessment_id)
        ).scalar_one()
        assert sorted(linked_pull_requests(conn, Assessment.model_validate(doc))) == [7, 8]
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert sorted(c[2] for c in calls) == [7, 8]
    assert records() == [HEAD]


@pytest.mark.parametrize(
    "sibling_status,expect", [("recovery_required", "skipped"), ("running", "deferred")]
)
def test_review_yields_to_an_older_job_for_the_same_review(
    automatic, monkeypatch, sibling_status, expect
):
    """An older job's POST is in flight (or ended uncertain): never post a second time."""
    _, bob, ids, _ = automatic
    from backend import jobs as jobs_api

    with transaction() as conn:
        older, _ = jobs_api.enqueue(conn, "post_pr_review", {}, dedupe_key="older-sibling")
    assessment_id = seed_pr_assessment(ids, bob)
    with transaction() as conn:
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == older)
            .values(
                status=sibling_status,
                effect_state="in_flight",
                effect_key=f"prreview:{assessment_id}:pr:7",
            )
        )
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert calls == []
    with transaction() as conn:
        mine = (
            conn.execute(
                sa.select(jobs).where(jobs.c.dedupe_key == f"prreview:{assessment_id}:pr:7")
            )
            .mappings()
            .one()
        )
    if expect == "skipped":
        assert mine["status"] == "succeeded"
        assert audits("monitoring.pr_review_skipped")
    else:
        assert mine["status"] == "queued" and mine["defer_count"] >= 1


def test_dead_legacy_review_job_does_not_block_the_per_pr_job(automatic, monkeypatch):
    _, bob, ids, _ = automatic
    assessment_id = seed_pr_assessment(ids, bob)
    with transaction() as conn:
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.kind == "post_pr_review")
            .values(
                dedupe_key=f"prreview:{assessment_id}",
                payload={"assessment_id": assessment_id},
                status="dead",
            )
        )
        from backend.host import _enqueue_pr_review
        from shared.assessment import Assessment

        doc = conn.execute(
            sa.select(assessments.c.doc).where(assessments.c.id == assessment_id)
        ).scalar_one()
        _enqueue_pr_review(conn, Assessment.model_validate(doc))
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert [c[2] for c in calls] == [7]
