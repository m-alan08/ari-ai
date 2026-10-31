"""Signed pushes exercise real queue/DB scheduling with GitHub identity reads stubbed."""

import pytest
import sqlalchemy as sa
from helpers import BASE, HEAD, post_webhook, push_payload, setup_project

from backend import worker
from backend.auto_commits import authors, per_commit_enabled
from backend.db import (
    assessments,
    jobs,
    monitoring_events,
    reassessment_requests,
    repositories,
    transaction,
)
from shared.assessment import AssessmentTrigger


@pytest.fixture
def automatic(client_factory, settings_override, monkeypatch):
    settings_override(
        github_auto_commit_authors="adamissac",
    )
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob, approve=False)
    with transaction() as conn:
        conn.execute(
            sa.update(repositories)
            .where(repositories.c.id == ids["repo"])
            .values(installation_id=99)
        )
    identity = {"login": "adamissac"}

    class GitHub:
        def commit(self, installation, repo, sha):
            return {"sha": sha, "author": identity}

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    return alice, bob, ids, identity


def push(client, *shas):
    p = push_payload(["README.md"], head=shas[-1])
    p["sender"] = {"login": "adamissac"}
    p["commits"] = [
        {"id": sha, "modified": ["README.md"], "author": {"username": "adamissac"}} for sha in shas
    ]
    assert post_webhook(client, p).status_code == 202
    worker.drain(kinds=["process_github_delivery"])
    worker.drain(kinds=["assess_github_commit"])


def records():
    with transaction() as conn:
        return list(conn.execute(sa.select(assessments.c.commit_sha)).scalars())


def seed_assessments(alice, ids, triggers, *, created_at=None, spend=None):
    from backend.services import create_assessment

    seeded = []
    with transaction() as conn:
        for index, trigger in enumerate(triggers):
            assessment, _ = create_assessment(
                conn,
                project_id=ids["project"],
                repo_id=ids["repo"],
                scope_id=ids["scope"],
                commit_sha=f"{index + 1:040x}",
                trigger=trigger,
                budget=None,
                created_by=alice.me["user"]["id"],
            )
            doc = conn.execute(
                sa.select(assessments.c.doc).where(assessments.c.id == assessment.id)
            ).scalar_one()
            doc["state"] = "FAILED"
            if spend is not None:
                if isinstance(spend, list):
                    row_spend = spend[index]
                elif index == 0:
                    row_spend = spend
                else:
                    row_spend = None
                if row_spend is not None:
                    doc.setdefault("usage", {})["usd"] = row_spend
            values = {"state": "FAILED", "doc": doc}
            if created_at is not None:
                values["created_at"] = (
                    created_at[index] if isinstance(created_at, list) else created_at
                )
            conn.execute(
                sa.update(assessments).where(assessments.c.id == assessment.id).values(**values)
            )
            seeded.append(assessment.id)
    return seeded


def manual_start(alice, ids, sha):
    return alice.post(
        f"/api/v1/projects/{ids['project']}/assessments",
        json={"repo_id": ids["repo"], "scope_id": ids["scope"], "commit_sha": sha},
    )


def reviewer_webhook(alice, ids, *, settings=None, payload=None, event="push"):
    if settings:
        project_settings = alice.get(f"/api/v1/projects/{ids['project']}").json()["settings"]
        project_settings["review"].update(settings)
        response = alice.patch(
            f"/api/v1/projects/{ids['project']}", json={"settings": project_settings}
        )
        assert response.status_code == 200, response.text
    if payload is None:
        payload = push_payload(["app.py"])
        payload["sender"] = {"login": "adamissac"}
        payload["commits"][0]["id"] = HEAD
    response = post_webhook(alice, payload, event=event)
    assert response.status_code == 202, response.text
    worker.drain(kinds=["process_github_delivery"])


def test_each_commit_runs_even_docs_only_active_and_duplicate_delivery(automatic):
    alice, bob, ids, _ = automatic
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    push(alice, HEAD, "c" * 40)
    push(alice, HEAD, "c" * 40)
    push(alice, "d" * 40)
    assert sorted(records()) == [HEAD, "c" * 40, "d" * 40]


def test_pushes_to_any_branch_are_reviewed_by_default(automatic):
    alice, bob, ids, _ = automatic
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    p = push_payload(["app.py"], head="e" * 40)
    p["ref"] = "refs/heads/feature/login"
    p["sender"] = {"login": "adamissac"}
    p["commits"] = [{"id": "e" * 40, "modified": ["app.py"], "author": {"username": "adamissac"}}]
    assert post_webhook(alice, p).status_code == 202
    worker.drain(kinds=["process_github_delivery"])
    worker.drain(kinds=["assess_github_commit"])
    assert records() == ["e" * 40]


def test_scope_approval_resumes_without_run_button(automatic):
    alice, bob, ids, _ = automatic
    push(alice, HEAD)
    assert records() == []
    rrqs = alice.get(f"/api/v1/projects/{ids['project']}/reassessments").json()["items"]
    assert rrqs[0]["status"] == "blocked_no_scope"
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    worker.drain(kinds=["assess_github_commit"])
    assert records() == [HEAD]
    rrqs = alice.get(f"/api/v1/projects/{ids['project']}/reassessments").json()["items"]
    assert len(rrqs) == 1 and rrqs[0]["status"] == "started"
    events = alice.get(f"/api/v1/projects/{ids['project']}/events").json()["items"]
    assert events[0]["status_reason"] == "automatic commit assessment"


@pytest.mark.parametrize("login", ["someone-else", ""])
def test_payload_author_cannot_bypass_verified_author(automatic, login):
    alice, bob, ids, identity = automatic
    identity["login"] = login
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)
    assert records() == []


def test_rollout_configurable_for_other_accounts(automatic, settings_override):
    alice, bob, ids, identity = automatic
    identity["login"] = "AnotherUser"
    settings_override(github_auto_commit_authors="anotheruser")
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    p = push_payload(["app.py"])
    p["commits"][0]["id"] = HEAD
    p["sender"] = {"login": "anotheruser"}
    post_webhook(alice, p)
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == [HEAD]


def test_disabled_monitoring_stays_disabled(automatic):
    alice, bob, ids, _ = automatic
    settings = alice.get(f"/api/v1/projects/{ids['project']}").json()["settings"]
    settings["monitoring_policy"]["enabled"] = False
    assert (
        alice.patch(f"/api/v1/projects/{ids['project']}", json={"settings": settings}).status_code
        == 200
    )
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)
    assert records() == []


def test_identity_api_failure_retries_without_starting(automatic, monkeypatch):
    alice, bob, ids, _ = automatic

    def fail(*args):
        raise RuntimeError("GitHub unavailable")

    monkeypatch.setattr("backend.auto_commits.GitHubApp.commit", fail)
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)
    assert records() == []
    with transaction() as conn:
        row = (
            conn.execute(sa.select(jobs).where(jobs.c.kind == "assess_github_commit"))
            .mappings()
            .one()
        )
        assert row["status"] == "queued" and row["attempts"] == 1


def test_wrong_installation_never_enqueues(automatic):
    alice, _, _, _ = automatic
    p = push_payload(["app.py"])
    p["sender"] = {"login": "adamissac"}
    p["commits"][0]["id"] = HEAD
    p["installation"]["id"] = 101
    assert post_webhook(alice, p).status_code == 202
    worker.drain(kinds=["process_github_delivery"])
    with transaction() as conn:
        assert not conn.execute(
            sa.select(jobs.c.id).where(jobs.c.kind == "assess_github_commit")
        ).first()


def test_truncated_push_enumerates_all_commits(automatic, monkeypatch, settings_override):
    alice, bob, ids, _ = automatic
    settings_override(project_assessments_per_hour="50", project_assessments_per_day="100")
    shas = [f"{i:040x}" for i in range(1, 26)]
    monkeypatch.setattr(
        "backend.auto_commits.GitHubApp.compare_commits", lambda *args: shas, raising=False
    )
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    p = push_payload(["app.py"], head=shas[-1])
    p["sender"] = {"login": "adamissac"}
    p["commits"] = [{"id": sha} for sha in shas[:20]]
    assert post_webhook(alice, p).status_code == 202
    worker.drain(kinds=["process_github_delivery"])
    worker.drain(kinds=["assess_github_commit"])
    assert sorted(records()) == shas


def test_compare_reads_subsequent_pages(monkeypatch):
    from backend.github import GitHubApp

    app = object.__new__(GitHubApp)
    pages = []

    def get(installation, path, **params):
        pages.append(params["page"])
        shas = ["a" * 40, "b" * 40] if params["page"] == 1 else ["c" * 40]
        return {"total_commits": 3, "commits": [{"sha": s} for s in shas]}

    monkeypatch.setattr(app, "_get", get)
    assert app.compare_commits(99, "acme/repo", "d" * 40, "c" * 40) == [
        "a" * 40,
        "b" * 40,
        "c" * 40,
    ]
    assert pages == [1, 2]


def test_full_run_uses_project_budget_clamped_to_operator_cap(automatic, settings_override):
    alice, bob, ids, _ = automatic
    settings_override(llm_max_tokens_per_assessment="200000", llm_max_usd_per_assessment="4")
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)
    with transaction() as conn:
        doc = conn.execute(sa.select(assessments.c.doc)).scalar_one()
        assert doc["budget"]["max_tokens"] == 200000
        assert doc["budget"]["max_usd"] == 4


def test_mixed_author_push_runs_only_allowed_commits_even_when_head_is_other_author(
    automatic, monkeypatch
):
    alice, bob, ids, _ = automatic

    def commit(self, installation, repo, sha):
        return {"sha": sha, "author": {"login": "adamissac" if sha == HEAD else "teammate"}}

    monkeypatch.setattr("backend.auto_commits.GitHubApp.commit", commit)
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD, "c" * 40)
    assert records() == [HEAD]


def test_repository_switch_persists_is_scoped_and_manual_runs_still_work(automatic):
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    other = alice.post(
        f"/api/v1/projects/{ids['project']}/repositories", json={"full_name": "acme/other"}
    ).json()
    endpoint = f"/api/v1/repositories/{ids['repo']}"
    assert alice.get(endpoint).json()["automatic_assessments"] is True
    assert (
        alice.patch(endpoint, json={"automatic_assessments": False}).json()["automatic_assessments"]
        is False
    )
    assert alice.get(f"/api/v1/repositories/{other['id']}").json()["automatic_assessments"] is False
    push(alice, HEAD)
    assert records() == []
    response = alice.post(
        f"/api/v1/projects/{ids['project']}/assessments",
        json={
            "repo_id": ids["repo"],
            "scope_id": ids["scope"],
            "commit_sha": HEAD,
        },
    )
    assert response.status_code == 202, response.text
    assert records() == [HEAD]
    assert alice.patch(endpoint, json={"automatic_assessments": True}).status_code == 200
    push(alice, "c" * 40)
    assert sorted(records()) == [HEAD, "c" * 40]


def test_off_before_worker_start_cancels_queued_automatic_assessment(automatic):
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)
    alice.patch(f"/api/v1/repositories/{ids['repo']}", json={"automatic_assessments": False})
    worker.drain(kinds=["run_assessment"])
    with transaction() as conn:
        assert conn.execute(sa.select(assessments.c.state)).scalar_one() == "CANCELLED"


def test_off_before_commit_job_blocks_automatic_start(automatic):
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    p = push_payload(["app.py"])
    p["sender"] = {"login": "adamissac"}
    p["commits"][0]["id"] = HEAD
    post_webhook(alice, p)
    worker.drain(kinds=["process_github_delivery"])
    alice.patch(f"/api/v1/repositories/{ids['repo']}", json={"automatic_assessments": False})
    worker.drain(kinds=["assess_github_commit"])
    assert records() == []


def test_repository_switch_requires_owner_and_rejects_other_organizations(
    automatic, client_factory
):
    from helpers import add_member

    alice, _, ids, _ = automatic
    outsider = client_factory("outsider")
    endpoint = f"/api/v1/repositories/{ids['repo']}"
    assert outsider.patch(endpoint, json={"automatic_assessments": False}).status_code in (403, 404)
    add_member(ids["org"], outsider.me["user"]["id"], role="viewer")
    assert outsider.get(endpoint).status_code == 200
    assert outsider.patch(endpoint, json={"automatic_assessments": False}).status_code == 403
    assert alice.get(endpoint).json()["automatic_assessments"] is True


def test_spoofed_author_does_not_authorize_an_unlisted_push_sender(automatic):
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    p = push_payload(["app.py"])
    p["commits"][0]["id"] = HEAD
    p["sender"] = {"login": "outsider"}
    post_webhook(alice, p)
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == []


def test_ten_mixed_assessments_fill_the_shared_pool_for_both_triggers(automatic):
    from shared.base import utcnow

    alice, bob, ids, _ = automatic
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    seeded_at = utcnow()
    seed_assessments(
        alice,
        ids,
        [AssessmentTrigger.manual] * 6 + [AssessmentTrigger.monitoring] * 4,
        created_at=seeded_at,
    )

    pushed_sha = "e" * 40
    push(alice, pushed_sha)
    assert len(records()) == 10
    with transaction() as conn:
        rrq = conn.execute(
            sa.select(reassessment_requests.c.doc).where(
                reassessment_requests.c.doc["head_sha"].astext == pushed_sha
            )
        ).scalar_one()
        deferred_jobs = conn.execute(
            sa.select(sa.func.count())
            .select_from(jobs)
            .where(jobs.c.dedupe_key.like("github:deferred:%"))
        ).scalar_one()
    assert rrq["status"] == "skipped"
    assert rrq["reason"] == "skipped: project review limit reached (10/hour)"
    assert deferred_jobs == 0

    # One pool: the same ten runs also hold back a manual start.
    response = manual_start(alice, ids, "f" * 40)
    assert response.status_code == 429, response.text


def test_over_limit_hourly_assessments_report_actual_reset(automatic):
    from datetime import datetime, timedelta

    from shared.base import utcnow

    alice, bob, ids, _ = automatic
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    seeded_at = utcnow() - timedelta(seconds=20)
    created_at = [seeded_at + timedelta(seconds=index) for index in range(12)]
    seed_assessments(
        alice,
        ids,
        [AssessmentTrigger.manual] * 12,
        created_at=created_at,
    )

    response = manual_start(alice, ids, "f" * 40)
    assert response.status_code == 429
    error = response.json()["error"]
    assert "project review limit reached (10 per hour); resets at " in error["message"]
    assert datetime.fromisoformat(error["details"]["resets_at"]) == created_at[2] + timedelta(
        hours=1
    )


@pytest.mark.parametrize("start_kind", ["manual", "automatic"])
def test_nine_recent_assessments_allow_next_start(automatic, start_kind):
    alice, bob, ids, _ = automatic
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    seed_assessments(
        alice,
        ids,
        [AssessmentTrigger.manual] * 5 + [AssessmentTrigger.monitoring] * 4,
    )

    if start_kind == "manual":
        response = manual_start(alice, ids, "f" * 40)
        assert response.status_code == 202
    else:
        pushed_sha = "e" * 40
        push(alice, pushed_sha)
        with transaction() as conn:
            rrq = conn.execute(
                sa.select(reassessment_requests.c.doc).where(
                    reassessment_requests.c.doc["head_sha"].astext == pushed_sha
                )
            ).scalar_one()
        assert rrq["status"] == "started"
    with transaction() as conn:
        count = conn.execute(
            sa.select(sa.func.count())
            .select_from(assessments)
            .where(assessments.c.project_id == ids["project"])
        ).scalar_one()
    assert count == 10


def test_monitoring_assessments_count_toward_the_manual_daily_quota(automatic):
    from datetime import timedelta

    from shared.base import utcnow

    alice, bob, ids, _ = automatic
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    seed_assessments(
        alice,
        ids,
        [AssessmentTrigger.monitoring] * 50,
        created_at=utcnow() - timedelta(hours=2),
    )

    response = manual_start(alice, ids, "f" * 40)

    assert response.status_code == 429, response.text
    assert "50 per day" in response.json()["error"]["message"]


def test_manual_quota_reset_counts_the_shared_pool(automatic):
    from datetime import datetime, timedelta

    from shared.base import utcnow

    alice, bob, ids, _ = automatic
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    start = utcnow() - timedelta(hours=20)
    monitoring_created_at = [start + timedelta(seconds=index) for index in range(30)]
    seed_assessments(
        alice,
        ids,
        [AssessmentTrigger.monitoring] * 30,
        created_at=monitoring_created_at,
    )
    seed_assessments(
        alice,
        ids,
        [AssessmentTrigger.manual] * 20,
        created_at=utcnow() - timedelta(hours=2),
    )

    response = manual_start(alice, ids, "f" * 40)

    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "RATE_LIMITED"
    # The oldest run in the shared pool (an automatic one) frees the next slot.
    assert datetime.fromisoformat(error["details"]["resets_at"]) == monitoring_created_at[
        0
    ] + timedelta(days=1)


def test_fifty_daily_assessments_block_by_day_quota(automatic):
    from datetime import datetime, timedelta

    from shared.base import utcnow

    alice, bob, ids, _ = automatic
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    seeded_at = utcnow() - timedelta(hours=2)
    seed_assessments(
        alice,
        ids,
        [AssessmentTrigger.manual] * 50,
        created_at=seeded_at,
    )

    pushed_sha = "e" * 40
    push(alice, pushed_sha)
    with transaction() as conn:
        rrq = conn.execute(
            sa.select(reassessment_requests.c.doc).where(
                reassessment_requests.c.doc["head_sha"].astext == pushed_sha
            )
        ).scalar_one()
    assert rrq["status"] == "skipped"
    assert rrq["reason"] == "skipped: project review limit reached (50/day)"

    response = manual_start(alice, ids, "f" * 40)
    assert response.status_code == 429
    error = response.json()["error"]
    assert "project review limit reached (50 per day); resets at " in error["message"]
    assert datetime.fromisoformat(error["details"]["resets_at"]) == seeded_at + timedelta(days=1)


def test_over_limit_daily_assessments_report_actual_reset(automatic):
    from datetime import datetime, timedelta

    from shared.base import utcnow

    alice, bob, ids, _ = automatic
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    seeded_at = utcnow() - timedelta(hours=2, seconds=51)
    created_at = [seeded_at + timedelta(seconds=index) for index in range(52)]
    seed_assessments(
        alice,
        ids,
        [AssessmentTrigger.manual] * 52,
        created_at=created_at,
    )

    response = manual_start(alice, ids, "f" * 40)
    assert response.status_code == 429
    error = response.json()["error"]
    assert "project review limit reached (50 per day); resets at " in error["message"]
    assert datetime.fromisoformat(error["details"]["resets_at"]) == created_at[2] + timedelta(
        days=1
    )


def test_global_spend_from_any_trigger_blocks_automatic_and_manual(automatic):
    alice, bob, ids, _ = automatic
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    seed_assessments(alice, ids, [AssessmentTrigger.manual], spend=50.0)

    pushed_sha = "e" * 40
    push(alice, pushed_sha)
    with transaction() as conn:
        rrq = conn.execute(
            sa.select(reassessment_requests.c.doc).where(
                reassessment_requests.c.doc["head_sha"].astext == pushed_sha
            )
        ).scalar_one()
        local_count = conn.execute(
            sa.select(sa.func.count())
            .select_from(assessments)
            .where(assessments.c.project_id == ids["project"])
        ).scalar_one()
    assert rrq["status"] == "skipped"
    assert rrq["reason"] == "skipped: daily spend cap reached"
    assert local_count == 1

    response = manual_start(alice, ids, "f" * 40)
    assert response.status_code == 429
    assert response.json()["error"]["message"].startswith("daily spend cap reached; resets at ")


def test_over_limit_spend_quota_resets_when_spend_falls_below_cap(automatic, settings_override):
    from datetime import timedelta

    from backend.quota import check_project_quota
    from shared.base import utcnow

    alice, bob, ids, _ = automatic
    settings_override(assessments_usd_per_day="10")
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    now = utcnow()
    row_times = [now - timedelta(hours=hours) for hours in (3, 2, 1)]
    seed_assessments(
        alice,
        ids,
        [AssessmentTrigger.manual] * len(row_times),
        created_at=row_times,
        spend=[6.0, 6.0, 6.0],
    )

    with transaction() as conn:
        decision = check_project_quota(conn, ids["project"], now)

    assert decision is not None and decision.reason == "spend"
    assert decision.resets_at == row_times[1] + timedelta(days=1)


def test_fractional_spend_resets_only_after_total_falls_below_cap(automatic, settings_override):
    from datetime import timedelta

    from backend.quota import check_project_quota
    from shared.base import utcnow

    alice, bob, ids, _ = automatic
    settings_override(assessments_usd_per_day="50")
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    now = utcnow()
    row_times = [now - timedelta(hours=hours) for hours in (4, 3, 2, 1)]
    seed_assessments(
        alice,
        ids,
        [AssessmentTrigger.manual] * len(row_times),
        created_at=row_times,
        spend=[0.1, 0.2, 25.0, 25.0],
    )

    with transaction() as conn:
        decision = check_project_quota(conn, ids["project"], now)

    assert decision is not None and decision.reason == "spend"
    assert decision.resets_at == row_times[2] + timedelta(days=1)


def test_at_limit_spend_quota_resets_when_oldest_usage_expires(automatic, settings_override):
    from datetime import timedelta

    from backend.quota import check_project_quota
    from shared.base import utcnow

    alice, bob, ids, _ = automatic
    settings_override(assessments_usd_per_day="10")
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    now = utcnow()
    row_times = [now - timedelta(hours=hours) for hours in (3, 2)]
    seed_assessments(
        alice,
        ids,
        [AssessmentTrigger.manual] * len(row_times),
        created_at=row_times,
        spend=[6.0, 4.0],
    )

    with transaction() as conn:
        decision = check_project_quota(conn, ids["project"], now)

    assert decision is not None and decision.reason == "spend"
    assert decision.resets_at == row_times[0] + timedelta(days=1)


def test_reuses_inflight_manual_assessment(automatic):
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    started = alice.post(
        f"/api/v1/projects/{ids['project']}/assessments",
        json={
            "repo_id": ids["repo"],
            "scope_id": ids["scope"],
            "commit_sha": HEAD,
        },
    )
    assert started.status_code == 202
    push(alice, HEAD)
    assert records() == [HEAD]
    rrqs = alice.get(f"/api/v1/projects/{ids['project']}/reassessments").json()["items"]
    assert rrqs[0]["assessment_id"] == started.json()["assessment"]["id"]


def test_blocked_event_preserves_delivery_time_and_is_audited(automatic):
    from backend.db import webhook_deliveries

    alice, _, ids, _ = automatic
    push(alice, HEAD)
    event = alice.get(f"/api/v1/projects/{ids['project']}/events").json()["items"][0]
    with transaction() as conn:
        received = conn.execute(sa.select(webhook_deliveries.c.received_at)).scalar_one()
    from datetime import datetime

    assert datetime.fromisoformat(event["received_at"].replace("Z", "+00:00")) == received
    assert event["occurred_at"].startswith("2026-10-17T01:02:03")
    audit = alice.get("/api/v1/audit", params={"project_id": ids["project"]}).json()
    assert any(item["action"] == "monitoring.event_processed" for item in audit["items"])


def test_pull_request_commit_still_uses_authenticated_automatic_path(automatic):
    alice, bob, ids, _ = automatic
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    payload = {
        "repository": {"full_name": "acme/payments-lite"},
        "installation": {"id": 99},
        "sender": {"login": "adamissac"},
        "action": "synchronize",
        "number": 7,
        "pull_request": {
            "head": {"sha": HEAD, "ref": "feature"},
            "base": {"sha": "a" * 40},
            "user": {"login": "adamissac"},
            "updated_at": "2026-10-17T01:02:03Z",
        },
    }
    post_webhook(alice, payload, event="pull_request")
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == [HEAD]
    event = alice.get(f"/api/v1/projects/{ids['project']}/events").json()["items"][0]
    assert event["type"] == "pull_request" and event["pr_number"] == 7


def test_github_client_is_reused_across_commits(automatic, monkeypatch):
    from backend import auto_commits

    alice, bob, ids, _ = automatic
    calls = []
    original = auto_commits.GitHubApp

    class CountingGitHub(original):
        def __init__(self):
            calls.append(1)

    monkeypatch.setattr(auto_commits, "GitHubApp", CountingGitHub)
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD, "c" * 40)
    assert len(calls) == 1


def test_legacy_deferred_job_is_skipped_under_quota_without_new_job(automatic):
    from shared.base import utcnow
    from shared.ids import IdPrefix, new_id
    from shared.monitoring import (
        CommitRange,
        EventSource,
        EventStatus,
        EventType,
        MonitoringEvent,
        ReassessmentRequest,
        ReassessmentScope,
        ReassessmentStatus,
    )

    alice, bob, ids, _ = automatic
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    seed_assessments(
        alice,
        ids,
        [AssessmentTrigger.manual] * 6 + [AssessmentTrigger.monitoring] * 4,
    )
    pushed_sha = "e" * 40
    payload = push_payload(["README.md"], head=pushed_sha)
    payload["sender"] = {"login": "adamissac"}
    payload["commits"] = [
        {"id": pushed_sha, "modified": ["README.md"], "author": {"username": "adamissac"}}
    ]
    assert post_webhook(alice, payload).status_code == 202
    worker.drain(kinds=["process_github_delivery"])

    with transaction() as conn:
        job = (
            conn.execute(
                sa.select(jobs).where(
                    jobs.c.dedupe_key == f"github:commit:{ids['repo']}:{pushed_sha}"
                )
            )
            .mappings()
            .one()
        )
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.id == job["id"])
            .values(dedupe_key="github:deferred:legacy-job", run_after=utcnow())
        )
        event = MonitoringEvent(
            id=new_id(IdPrefix.evt),
            project_id=ids["project"],
            repo_id=ids["repo"],
            source=EventSource.github,
            type=EventType(job["payload"]["event_type"]),
            occurred_at=job["payload"]["occurred_at"],
            received_at=job["payload"]["received_at"],
            dedupe_key=f"github:commit:{ids['repo']}:{pushed_sha}",
            payload_artifact_id=job["payload"]["payload_artifact_id"],
            commit_range=CommitRange(base_sha=None, head_sha=pushed_sha),
            ref=job["payload"]["ref"],
            status=EventStatus.processed,
            status_reason="queued by an earlier quota implementation",
        )
        conn.execute(
            sa.insert(monitoring_events).values(
                id=event.id,
                project_id=event.project_id,
                repo_id=event.repo_id,
                dedupe_key=event.dedupe_key,
                status=event.status.value,
                doc=event.model_dump(mode="json"),
            )
        )
        rrq = ReassessmentRequest(
            id=new_id(IdPrefix.rrq),
            project_id=ids["project"],
            repo_id=ids["repo"],
            event_ids=[event.id],
            scope=ReassessmentScope.full,
            head_sha=pushed_sha,
            status=ReassessmentStatus.queued,
            reason="queued: waiting for project automatic-run capacity",
            created_at=utcnow(),
        )
        conn.execute(
            sa.insert(reassessment_requests).values(
                id=rrq.id,
                project_id=rrq.project_id,
                repo_id=rrq.repo_id,
                status=rrq.status.value,
                doc=rrq.model_dump(mode="json"),
            )
        )
        jobs_before = conn.execute(sa.select(sa.func.count()).select_from(jobs)).scalar_one()

    worker.drain(kinds=["assess_github_commit"])

    with transaction() as conn:
        jobs_after = conn.execute(sa.select(sa.func.count()).select_from(jobs)).scalar_one()
        job_status = conn.execute(
            sa.select(jobs.c.status).where(jobs.c.id == job["id"])
        ).scalar_one()
        saved = conn.execute(
            sa.select(reassessment_requests.c.doc).where(reassessment_requests.c.id == rrq.id)
        ).scalar_one()
    assert jobs_after == jobs_before
    assert job_status != "queued"
    assert saved["status"] == "skipped"
    assert saved["reason"] == "skipped: project review limit reached (10/hour)"
    assert len(records()) == 10


def test_legacy_monitoring_pipeline_obeys_shared_quota(automatic, settings_override, monkeypatch):
    from monitoring.api import Action, Decision
    from shared.monitoring import ReassessmentScope

    alice, bob, ids, _ = automatic
    settings_override(github_auto_commit_authors="")
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    seed_assessments(
        alice,
        ids,
        [AssessmentTrigger.manual] * 6 + [AssessmentTrigger.monitoring] * 4,
    )
    monkeypatch.setattr(
        "backend.pipeline._impact_provider",
        lambda: (None, "test impact provider"),
    )
    monkeypatch.setattr(
        "backend.pipeline.decide",
        lambda *args, **kwargs: Decision(
            Action.assess, "test assessment", scope=ReassessmentScope.full
        ),
    )
    pushed_sha = "d" * 40
    push(alice, pushed_sha)

    with transaction() as conn:
        rrq = conn.execute(
            sa.select(reassessment_requests.c.doc).where(
                reassessment_requests.c.doc["head_sha"].astext == pushed_sha
            )
        ).scalar_one()
    assert rrq["status"] == "skipped"
    assert rrq["reason"] == "skipped: project review limit reached (10/hour)"
    assert len(records()) == 10


@pytest.mark.parametrize(
    ("settings", "author", "branch", "event", "draft", "reason"),
    [
        ({"review_pushes": False}, "adamissac", "main", "push", False, "push reviews disabled"),
        (
            {"review_pull_requests": False},
            "adamissac",
            "main",
            "pull_request",
            False,
            "pull request reviews disabled",
        ),
        (
            {"review_draft_prs": False},
            "adamissac",
            "main",
            "pull_request",
            True,
            "draft pull requests disabled",
        ),
        (
            {"include_branches": ["release/*"]},
            "adamissac",
            "main",
            "pull_request",
            False,
            "branch excluded",
        ),
        (
            {"exclude_branches": ["m*"]},
            "adamissac",
            "main",
            "pull_request",
            False,
            "branch excluded",
        ),
        (
            {"include_branches": ["release/*"]},
            "adamissac",
            "main",
            "push",
            False,
            "branch excluded",
        ),
        ({"exclude_branches": ["m*"]}, "adamissac", "main", "push", False, "branch excluded"),
        (
            {"include_authors": ["teammate*"]},
            "adamissac",
            "main",
            "push",
            False,
            "author is outside",
        ),
        (
            {"include_authors": ["teammate*"]},
            "adamissac",
            "main",
            "pull_request",
            False,
            "author is outside",
        ),
        ({}, "adamissac[bot]", "main", "push", False, "author excluded"),
        ({}, "adamissac[bot]", "main", "pull_request", False, "author excluded"),
    ],
)
@pytest.mark.parametrize("global_authors", ["adamissac", '""'])
def test_reviewer_filters_record_ignored_reason(
    automatic, settings_override, settings, author, branch, event, draft, reason, global_authors
):
    alice, _, ids, _ = automatic
    settings_override(github_auto_commit_authors=global_authors)
    if event == "pull_request":
        payload = {
            "repository": {"full_name": "acme/payments-lite"},
            "installation": {"id": 99},
            "sender": {"login": author},
            "action": "synchronize",
            "number": 7,
            "pull_request": {
                "head": {"sha": HEAD, "ref": "feature"},
                "base": {"sha": BASE, "ref": branch},
                "user": {"login": author},
                "updated_at": "2026-10-17T01:02:03Z",
                "draft": draft,
            },
        }
    else:
        payload = push_payload(["app.py"])
        payload["ref"] = f"refs/heads/{branch}"
        payload["sender"] = {"login": author}
        payload["commits"][0]["id"] = HEAD
    reviewer_webhook(alice, ids, settings=settings, payload=payload, event=event)
    with transaction() as conn:
        event_doc = conn.execute(sa.select(monitoring_events.c.doc)).scalar_one()
    assert event_doc["status"] == "ignored"
    assert reason in event_doc["status_reason"]
    assert event_doc.get("impact") is None


@pytest.mark.parametrize(
    ("settings", "author", "branch", "event", "draft"),
    [
        ({}, "adamissac", "main", "push", False),
        ({"include_branches": ["MA*"]}, "adamissac", "main", "push", False),
        ({"exclude_branches": ["release/*"]}, "adamissac", "main", "push", False),
        ({"include_authors": ["ADA*"]}, "adamissac", "main", "push", False),
        ({"include_branches": ["MA*"]}, "adamissac", "main", "pull_request", False),
        ({"exclude_branches": ["release/*"]}, "adamissac", "main", "pull_request", False),
        ({"include_authors": ["ADA*"]}, "adamissac", "main", "pull_request", False),
        ({}, "adamissac", "main", "pull_request", False),
        ({"review_draft_prs": True}, "adamissac", "main", "pull_request", True),
    ],
)
@pytest.mark.parametrize("global_authors", ["adamissac", '""'])
def test_reviewer_filters_allow_matching_events(
    automatic,
    settings_override,
    monkeypatch,
    settings,
    author,
    branch,
    event,
    draft,
    global_authors,
):
    alice, _, ids, _ = automatic
    settings_override(github_auto_commit_authors=global_authors)
    if not per_commit_enabled():
        monkeypatch.setattr(
            "backend.github.GitHubApp.compare_paths",
            lambda *args: (["app.py"], True),
        )
        monkeypatch.setattr("backend.pipeline._impact_provider", lambda: (None, "test provider"))
    if event == "pull_request":
        payload = {
            "repository": {"full_name": "acme/payments-lite"},
            "installation": {"id": 99},
            "sender": {"login": author},
            "action": "synchronize",
            "number": 7,
            "pull_request": {
                "head": {"sha": HEAD, "ref": "feature"},
                "base": {"sha": BASE, "ref": branch},
                "user": {"login": author},
                "updated_at": "2026-10-17T01:02:03Z",
                "draft": draft,
            },
        }
    else:
        payload = push_payload(["app.py"])
        payload["ref"] = f"refs/heads/{branch}"
        payload["sender"] = {"login": author}
        payload["commits"][0]["id"] = HEAD
    reviewer_webhook(alice, ids, settings=settings, payload=payload, event=event)
    with transaction() as conn:
        commit_jobs = (
            conn.execute(sa.select(jobs.c.id).where(jobs.c.kind == "assess_github_commit"))
            .scalars()
            .all()
        )
    if per_commit_enabled():
        assert len(commit_jobs) == 1
    else:
        with transaction() as conn:
            event_doc = conn.execute(sa.select(monitoring_events.c.doc)).scalar_one()
        assert not commit_jobs
        assert event_doc.get("impact") is not None
        assert "reviewer settings" not in event_doc["status_reason"]


@pytest.mark.parametrize(
    ("allowlist", "uses_scheduler"),
    [
        ("", False),
        ('""', False),
        ("''", False),
        ("  ", False),
        ("*", True),
        ("adamissac,teammate", True),
        ('"adamissac","teammate"', True),
    ],
)
def test_empty_star_and_comma_global_author_allowlist_semantics(
    automatic, settings_override, monkeypatch, allowlist, uses_scheduler
):
    alice, _, ids, _ = automatic
    settings_override(github_auto_commit_authors=allowlist)

    if not uses_scheduler:
        monkeypatch.setattr(
            "backend.github.GitHubApp.compare_paths",
            lambda *args: (["app.py"], True),
        )
        monkeypatch.setattr("backend.pipeline._impact_provider", lambda: (None, "test provider"))
    reviewer_webhook(alice, ids)
    with transaction() as conn:
        commit_jobs = (
            conn.execute(sa.select(jobs.c.id).where(jobs.c.kind == "assess_github_commit"))
            .scalars()
            .all()
        )
    assert per_commit_enabled() is uses_scheduler
    if uses_scheduler:
        assert len(commit_jobs) == 1
    else:
        with transaction() as conn:
            event_doc = conn.execute(sa.select(monitoring_events.c.doc)).scalar_one()
        assert not commit_jobs
        assert event_doc.get("impact") is not None


def test_comma_global_allowlist_rejects_unlisted_sender(automatic, settings_override):
    alice, _, ids, _ = automatic
    settings_override(github_auto_commit_authors="teammate,other")
    reviewer_webhook(alice, ids)
    with transaction() as conn:
        event_doc = conn.execute(sa.select(monitoring_events.c.doc)).scalar_one()
    assert event_doc["status"] == "ignored"
    assert "global automatic-review allowlist" in event_doc["status_reason"]


def test_project_hourly_review_limit_only_lowers_server_cap(automatic, settings_override):
    from backend.quota import check_project_quota
    from shared.base import utcnow

    alice, bob, ids, _ = automatic
    settings_override(project_assessments_per_hour="10")
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    project_settings = alice.get(f"/api/v1/projects/{ids['project']}").json()["settings"]
    project_settings["review"]["max_reviews_per_hour"] = 2
    assert (
        alice.patch(
            f"/api/v1/projects/{ids['project']}", json={"settings": project_settings}
        ).status_code
        == 200
    )
    seed_assessments(alice, ids, [AssessmentTrigger.manual] * 2)
    with transaction() as conn:
        decision = check_project_quota(conn, ids["project"], utcnow())
    assert decision is not None and decision.reason == "hour" and decision.limit == 2

    project_settings["review"]["max_reviews_per_hour"] = 20
    assert (
        alice.patch(
            f"/api/v1/projects/{ids['project']}", json={"settings": project_settings}
        ).status_code
        == 200
    )
    seed_assessments(alice, ids, [AssessmentTrigger.manual] * 8)
    with transaction() as conn:
        decision = check_project_quota(conn, ids["project"], utcnow())
    assert decision is not None and decision.reason == "hour" and decision.limit == 10


def test_pr_review_body_filters_strictness_and_redacts_secrets():
    from backend.auto_commits import _pr_review_body

    token = "ghp_" + "x" * 36
    body, count = _pr_review_body(
        "asm_test",
        "prj_test",
        [
            {
                "title": f"Sensitive {token}",
                "severity": {"level": "high"},
                "status": "verified",
                "evidence": [
                    {"path": "src/auth.py", "span": {"start_line": 17}, "content": "do not include"}
                ],
            },
            {
                "title": "Low severity",
                "severity": {"level": "low"},
                "status": "open",
                "evidence": [{"path": "src/other.py", "span": {"start_line": 2}}],
            },
        ],
        2,
    )
    assert count == 1
    assert "Ari security review" in body
    assert "Sensitive [REDACTED" in body and token not in body
    assert "src/auth.py:17" in body
    assert "Low severity" not in body and "do not include" not in body
    assert "No findings at this strictness." not in body


def test_pr_review_body_reports_empty_strictness_match():
    from backend.auto_commits import _pr_review_body

    body, count = _pr_review_body(
        "asm_test",
        "prj_test",
        [{"title": "Low", "severity": {"level": "low"}, "evidence": []}],
        3,
    )
    assert count == 0
    assert "No findings at this strictness." in body


def seed_pr_assessment(ids, bob):
    from shared.base import utcnow
    from shared.ids import IdPrefix, new_id
    from shared.monitoring import (
        CommitRange,
        EventSource,
        EventStatus,
        EventType,
        MonitoringEvent,
        ReassessmentRequest,
        ReassessmentScope,
        ReassessmentStatus,
    )

    now = utcnow()
    event_id = new_id(IdPrefix.evt)
    request_id = new_id(IdPrefix.rrq)
    event = MonitoringEvent(
        id=event_id,
        project_id=ids["project"],
        repo_id=ids["repo"],
        source=EventSource.github,
        type=EventType.pull_request,
        occurred_at=now,
        received_at=now,
        dedupe_key=f"test:pr:{event_id}",
        payload_artifact_id=None,
        commit_range=CommitRange(base_sha=BASE, head_sha=HEAD),
        pr_number=7,
        status=EventStatus.processed,
    )
    request = ReassessmentRequest(
        id=request_id,
        project_id=ids["project"],
        repo_id=ids["repo"],
        event_ids=[event_id],
        scope=ReassessmentScope.full,
        head_sha=HEAD,
        status=ReassessmentStatus.started,
        reason="test PR review",
        created_at=now,
    )
    from backend.services import create_assessment

    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    with transaction() as conn:
        conn.execute(
            sa.insert(monitoring_events).values(
                id=event.id,
                project_id=event.project_id,
                repo_id=event.repo_id,
                dedupe_key=event.dedupe_key,
                status=event.status.value,
                doc=event.model_dump(mode="json"),
            )
        )
        assessment, _ = create_assessment(
            conn,
            project_id=ids["project"],
            repo_id=ids["repo"],
            scope_id=ids["scope"],
            commit_sha=HEAD,
            trigger=AssessmentTrigger.monitoring,
            budget=None,
            created_by=None,
            reassessment_request_id=request_id,
        )
        assessment_doc = conn.execute(
            sa.select(assessments.c.doc).where(assessments.c.id == assessment.id)
        ).scalar_one()
        assessment_doc["state"] = "COMPLETED"
        conn.execute(
            sa.update(assessments)
            .where(assessments.c.id == assessment.id)
            .values(state="COMPLETED", doc=assessment_doc)
        )
        request = request.model_copy(update={"assessment_id": assessment.id})
        conn.execute(
            sa.insert(reassessment_requests).values(
                id=request.id,
                project_id=request.project_id,
                repo_id=request.repo_id,
                status=request.status.value,
                doc=request.model_dump(mode="json"),
            )
        )
        from backend.host import _enqueue_pr_review
        from shared.assessment import Assessment

        completed = Assessment.model_validate(assessment_doc)
        _enqueue_pr_review(conn, completed)
        _enqueue_pr_review(conn, completed)
    return assessment.id


def test_pr_review_job_is_deduped_and_4xx_is_permanent(automatic, monkeypatch):
    import httpx

    from backend.db import audit_events

    alice, _, ids, _ = automatic
    assessment_id = seed_pr_assessment(ids, automatic[1])
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)
            request = httpx.Request("POST", "https://api.github.com/review")
            response = httpx.Response(422, request=request)
            raise httpx.HTTPStatusError("unprocessable", request=request, response=response)

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    with transaction() as conn:
        job = conn.execute(sa.select(jobs).where(jobs.c.kind == "post_pr_review")).mappings().one()
        failures = [
            doc
            for doc in conn.execute(sa.select(audit_events.c.doc)).scalars()
            if doc["action"] == "monitoring.pr_review_failed"
        ]
    assert len(calls) == 1
    assert job["payload"]["assessment_id"] == assessment_id
    assert job["attempts"] == 1
    assert job["status"] != "queued"
    assert failures


def test_pr_review_job_noops_when_comments_are_disabled(automatic, monkeypatch):
    alice, _, ids, _ = automatic
    seed_pr_assessment(ids, automatic[1])
    project = alice.get(f"/api/v1/projects/{ids['project']}").json()
    project["settings"]["review"]["comment_on_pull_requests"] = False
    assert (
        alice.patch(
            f"/api/v1/projects/{ids['project']}", json={"settings": project["settings"]}
        ).status_code
        == 200
    )
    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert calls == []


def test_impact_path_pull_request_posts_review_from_monitoring_event(
    automatic, settings_override, monkeypatch
):
    from backend.host import AssessmentHost
    from monitoring.api import Action, Decision
    from shared.assessment import AssessmentState
    from shared.monitoring import ReassessmentScope

    alice, bob, ids, _ = automatic
    settings_override(github_auto_commit_authors="")
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    monkeypatch.setattr("backend.pipeline._impact_provider", lambda: (None, "test provider"))
    monkeypatch.setattr(
        "backend.pipeline.decide",
        lambda *args, **kwargs: Decision(
            Action.assess, "test assessment", scope=ReassessmentScope.full
        ),
    )
    monkeypatch.setattr(
        "backend.github.GitHubApp.compare_paths",
        lambda *args: (["app.py"], True),
    )
    payload = {
        "repository": {"full_name": "acme/payments-lite"},
        "installation": {"id": 99},
        "sender": {"login": "adamissac"},
        "action": "synchronize",
        "number": 7,
        "pull_request": {
            "head": {"sha": HEAD, "ref": "feature"},
            "base": {"sha": BASE, "ref": "main"},
            "user": {"login": "adamissac"},
            "updated_at": "2026-10-17T01:02:03Z",
            "draft": False,
        },
    }
    reviewer_webhook(alice, ids, payload=payload, event="pull_request")
    with transaction() as conn:
        event_doc = conn.execute(sa.select(monitoring_events.c.doc)).scalar_one()
        request_doc = conn.execute(
            sa.select(reassessment_requests.c.doc).where(
                reassessment_requests.c.doc["head_sha"].astext == HEAD
            )
        ).scalar_one()
        assessment_id = request_doc["assessment_id"]
        assert request_doc["event_ids"] == [event_doc["id"]]
        assert event_doc["pr_number"] == 7
        assessment_doc = conn.execute(
            sa.select(assessments.c.doc).where(assessments.c.id == assessment_id)
        ).scalar_one()
        assessment_doc["state"] = "REPORTING"
        conn.execute(
            sa.update(assessments)
            .where(assessments.c.id == assessment_id)
            .values(state="REPORTING", doc=assessment_doc)
        )

    AssessmentHost(assessment_id).transition(AssessmentState.COMPLETED, "test completion")
    with transaction() as conn:
        job = (
            conn.execute(
                sa.select(jobs).where(jobs.c.dedupe_key == f"prreview:{assessment_id}:pr:7")
            )
            .mappings()
            .one()
        )
    assert job["payload"]["assessment_id"] == assessment_id

    calls = []

    class GitHub:
        def post_pull_request_review(self, *args):
            calls.append(args)

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    worker.drain(kinds=["post_pr_review"])
    assert len(calls) == 1
    assert calls[0][2] == 7
    assert "Ari security review" in calls[0][3]
    assert "No findings at this strictness." in calls[0][3]


def test_wildcard_assesses_unlisted_commit_author(automatic, settings_override, monkeypatch):
    alice, bob, ids, identity = automatic
    identity["login"] = "outside-list"
    settings_override(github_auto_commit_authors="*")
    assert authors() == set()
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    p = push_payload(["app.py"])
    p["commits"][0]["id"] = HEAD
    p["sender"] = {"login": "teammate"}
    p["commits"][0]["author"] = {"username": "outside-list"}
    assert post_webhook(alice, p).status_code == 202
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == [HEAD]


@pytest.mark.parametrize(
    ("configured", "enabled"),
    [
        ("", False),
        ("  ", False),
        ("*", True),
        ('"*" ', True),
        ("alice", True),
    ],
)
def test_per_commit_enabled(configured, enabled, settings_override):
    settings_override(github_auto_commit_authors=configured)
    assert per_commit_enabled() is enabled


def test_wildcard_push_schedules_each_commit_without_impact_decision(
    automatic, settings_override, monkeypatch
):
    alice, _, _, _ = automatic
    settings_override(github_auto_commit_authors="*")
    shas = ["1" * 40, "2" * 40, "3" * 40]
    payload = push_payload(["app.py"], head=shas[-1])
    payload["sender"] = {"login": "teammate"}
    payload["commits"] = [
        {"id": sha, "modified": ["app.py"], "author": {"username": "teammate"}} for sha in shas
    ]

    def reject_legacy_decision(*args, **kwargs):
        pytest.fail("wildcard push entered the legacy impact decision path")

    monkeypatch.setattr("backend.pipeline.decide", reject_legacy_decision)
    assert post_webhook(alice, payload).status_code == 202
    worker.drain(kinds=["process_github_delivery"])

    with transaction() as conn:
        jobs_for_push = (
            conn.execute(sa.select(jobs.c.payload).where(jobs.c.kind == "assess_github_commit"))
            .scalars()
            .all()
        )
    assert len(jobs_for_push) == 3
    assert {job["sha"] for job in jobs_for_push} == set(shas)


def test_missing_commit_records_skipped_instead_of_dead_lettering(automatic, monkeypatch):
    import httpx

    from backend.db import monitoring_events, reassessment_requests

    alice, bob, ids, _ = automatic

    def gone(self, installation, repo, sha):
        request = httpx.Request("GET", f"https://api.github.com/repos/{repo}/commits/{sha}")
        raise httpx.HTTPStatusError(
            "404", request=request, response=httpx.Response(404, request=request)
        )

    monkeypatch.setattr("backend.auto_commits.GitHubApp.commit", gone)
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)
    assert records() == []
    with transaction() as conn:
        job = (
            conn.execute(sa.select(jobs).where(jobs.c.kind == "assess_github_commit"))
            .mappings()
            .one()
        )
        assert job["status"] == "succeeded"  # permanent failure: recorded, not retried to dead
        event = conn.execute(sa.select(monitoring_events.c.status)).scalar_one()
        rrq = conn.execute(sa.select(reassessment_requests.c.doc)).scalar_one()
    assert event == "ignored"
    assert rrq["status"] == "skipped" and "404" in rrq["reason"]


def test_transient_commit_lookup_still_retries(automatic, monkeypatch):
    import httpx

    alice, bob, ids, _ = automatic

    def flaky(self, installation, repo, sha):
        request = httpx.Request("GET", f"https://api.github.com/repos/{repo}/commits/{sha}")
        raise httpx.HTTPStatusError(
            "502", request=request, response=httpx.Response(502, request=request)
        )

    monkeypatch.setattr("backend.auto_commits.GitHubApp.commit", flaky)
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)
    with transaction() as conn:
        row = (
            conn.execute(sa.select(jobs).where(jobs.c.kind == "assess_github_commit"))
            .mappings()
            .one()
        )
        assert row["status"] == "queued" and row["attempts"] == 1


def test_compare_failure_falls_back_to_payload_commits(automatic, monkeypatch):
    import httpx

    alice, bob, ids, _ = automatic

    def boom(*args):
        raise httpx.ConnectError("compare unavailable")

    monkeypatch.setattr("backend.auto_commits.GitHubApp.compare_commits", boom, raising=False)
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    p = push_payload(["app.py"])
    p["sender"] = {"login": "adamissac"}
    p["commits"] = []  # force-push style payload: compare is the only enumerator
    assert post_webhook(alice, p).status_code == 202
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == [HEAD]


def test_commit_event_records_its_parent_so_the_reviewer_sees_the_diff(automatic, monkeypatch):
    from backend.db import monitoring_events

    alice, bob, ids, _ = automatic
    parent = "e" * 40

    class GitHub:
        def commit(self, installation, repo, sha):
            return {"sha": sha, "author": {"login": "adamissac"}, "parents": [{"sha": parent}]}

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    monkeypatch.setattr("backend.auto_commits._CLIENT", None)
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    push(alice, HEAD)
    with transaction() as conn:
        doc = conn.execute(sa.select(monitoring_events.c.doc)).scalars().all()[-1]
    assert doc["commit_range"] == {"base_sha": parent, "head_sha": HEAD}
