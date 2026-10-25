"""Signed pushes exercise real queue/DB scheduling with GitHub identity reads stubbed."""

import pytest
import sqlalchemy as sa
from helpers import HEAD, post_webhook, push_payload, setup_project

from backend import worker
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
            if spend is not None and index == 0:
                doc.setdefault("usage", {})["usd"] = spend
            values = {"state": "FAILED", "doc": doc}
            if created_at is not None:
                values["created_at"] = created_at
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


def test_each_commit_runs_even_docs_only_active_and_duplicate_delivery(automatic):
    alice, bob, ids, _ = automatic
    assert bob.post(f"/api/v1/scopes/{ids['scope']}/approve").status_code == 200
    push(alice, HEAD, "c" * 40)
    push(alice, HEAD, "c" * 40)
    push(alice, "d" * 40)
    assert sorted(records()) == [HEAD, "c" * 40, "d" * 40]


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


def test_ten_mixed_assessments_skip_automatic_and_block_manual_start(automatic):
    from datetime import datetime, timedelta

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

    response = manual_start(alice, ids, "f" * 40)
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "RATE_LIMITED"
    assert error["message"].startswith("project review limit reached (10 per hour); resets at ")
    resets_at = error["details"]["resets_at"]
    assert datetime.fromisoformat(resets_at) == seeded_at + timedelta(hours=1)


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


def test_wildcard_runs_any_pusher_and_unlinked_author(automatic, settings_override):
    alice, bob, ids, identity = automatic
    identity["login"] = ""  # commit email maps to no GitHub account
    settings_override(github_auto_commit_authors="*")
    bob.post(f"/api/v1/scopes/{ids['scope']}/approve")
    p = push_payload(["app.py"])
    p["commits"][0]["id"] = HEAD
    p["sender"] = {"login": "teammate"}
    post_webhook(alice, p)
    worker.drain(kinds=["process_github_delivery", "assess_github_commit"])
    assert records() == [HEAD]


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
