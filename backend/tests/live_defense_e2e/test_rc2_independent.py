"""Independent release probes; real DB, owned fixture, scripted provider only."""

import os

import sqlalchemy as sa

from backend import execution
from backend.db import get_engine, transaction
from backend.db import jobs as jobs_t

from .test_independent_integration import jobrow, prepare
from .transports import gateway_factory


def test_abrupt_process_death_during_provider_send_requires_recovery(staging):
    alice, app, _repo = prepare(staging)

    class ExitAtSend:
        mode = "fake"

        def complete(self, request):
            os._exit(71)  # cannot run BaseException handlers, like a killed worker

    staging.use_gateway(gateway_factory(ExitAtSend()))
    assert staging.ingest(alice, app).status_code == 202
    staging.drain(kinds=["live_defense_shadow", "live_defense_first_responder"])
    pid = os.fork()
    if pid == 0:
        get_engine().dispose(close=False)
        try:
            staging.drain(kinds=["live_defense_agent_step"])
        finally:
            os._exit(72)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 71
    step = next(j for j in staging.job_rows("live_defense_agent_step") if j["status"] == "running")
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == step["id"])
            .values(lease_expires_at=sa.func.now() - sa.text("interval '1 second'"))
        )
        execution.reconcile(conn)
    assert jobrow(step["id"])["status"] == "recovery_required"


def test_repo_picker_does_not_disclose_other_tenant_connection(client_factory):
    from backend.db import github_repo_access

    alice, bob = client_factory("alice"), client_factory("bob")
    pid = alice.post("/api/v1/projects", json={"name": "alice-private"}).json()["id"]
    rid = alice.post(
        f"/api/v1/projects/{pid}/repositories",
        json={"full_name": "acme/payments", "installation_id": None},
    ).json()["id"]
    with transaction() as conn:
        conn.execute(
            sa.insert(github_repo_access).values(
                user_id=bob.me["user"]["id"],
                full_name="acme/payments",
                installation_id=123,
                default_branch="main",
                private=True,
            )
        )
    assert bob.get(f"/api/v1/repositories/{rid}").status_code == 404
    rows = bob.get("/api/v1/github/repositories").json()["items"]
    assert rows[0]["connected_repo_id"] is None


def test_reconnect_refuses_stale_access_when_fresh_check_fails(client_factory, monkeypatch):
    import httpx

    from backend.db import github_repo_access, repositories, users

    alice = client_factory("alice")
    pid = alice.post("/api/v1/projects", json={"name": "private"}).json()["id"]
    rid = alice.post(
        f"/api/v1/projects/{pid}/repositories",
        json={"full_name": "acme/payments", "installation_id": None},
    ).json()["id"]
    uid = alice.me["user"]["id"]
    with transaction() as conn:
        conn.execute(
            sa.update(repositories).where(repositories.c.id == rid).values(installation_id=123)
        )
        conn.execute(
            sa.update(users).where(users.c.id == uid).values(github_login="alice", github_id=12345)
        )
        conn.execute(
            sa.insert(github_repo_access).values(
                user_id=uid,
                full_name="acme/payments",
                installation_id=123,
                default_branch="main",
                private=True,
            )
        )
    assert alice.post(f"/api/v1/repositories/{rid}/disconnect").status_code == 200

    def revoked(self, login):
        raise httpx.HTTPStatusError(
            "installation revoked",
            request=httpx.Request("GET", "https://api.github.com/users/alice/installation"),
            response=httpx.Response(404),
        )

    class RevokedApp:
        user_installation = revoked

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", RevokedApp)
    result = alice.post(f"/api/v1/repositories/{rid}/reconnect")
    assert result.status_code in {403, 503}, result.text


def test_repository_disconnect_stops_next_provider_turn(staging):
    from .transports import fake_provider

    alice, app, repo = prepare(staging)
    fake = fake_provider(repo / "target")

    class DisconnectDuringTurn:
        mode = "fake"
        calls = 0

        def complete(self, request):
            self.calls += 1
            response = fake.complete(request)
            if self.calls == 1:
                result = alice.post(f"/api/v1/repositories/{app.repo_id}/disconnect")
                assert result.status_code == 200, result.text
            return response

    provider = DisconnectDuringTurn()
    staging.use_gateway(gateway_factory(provider))
    assert staging.ingest(alice, app).status_code == 202
    staging.drain()
    assert provider.calls == 1, f"provider turns after repository disconnect: {provider.calls - 1}"


def test_job_api_does_not_expose_infrastructure_credentials(client_factory):
    from helpers import setup_project

    from backend import jobs

    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    marker = "review" + "-only-password"
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "review_credentials", {"project_id": ids["project"]})
        assert jobs.claim(conn, "review-worker", 60, ["review_credentials"])
        jobs.fail(
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


def test_disconnect_after_final_revocation_read_prevents_success(staging, monkeypatch):
    import inspect

    from backend.live_defense import first_responder

    from .transports import fake_provider

    alice, app, repo = prepare(staging)
    provider = fake_provider(repo / "target")
    staging.use_gateway(gateway_factory(provider))
    real_check = first_responder.repository_revocation
    disconnected = []

    def disconnect_after_read(conn, **kwargs):
        result = real_check(conn, **kwargs)
        caller = inspect.currentframe().f_back.f_code.co_name
        if caller == "live_defense_agent_step" and result is None and not disconnected:
            response = alice.post(f"/api/v1/repositories/{app.repo_id}/disconnect")
            assert response.status_code == 200, response.text
            disconnected.append(True)
        return result

    monkeypatch.setattr(first_responder, "repository_revocation", disconnect_after_read)
    assert staging.ingest(alice, app).status_code == 202
    staging.drain()
    assert disconnected
    invocations = staging.invocations(alice, app.project_id)
    assert all(inv["status"] != "executed" for inv in invocations)


def test_org_reconnect_checks_target_installation_even_with_personal_refresh(
    client_factory, monkeypatch
):
    import httpx

    from backend.db import github_repo_access, repositories, users

    alice = client_factory("alice")
    pid = alice.post("/api/v1/projects", json={"name": "org-review"}).json()["id"]
    rid = alice.post(
        f"/api/v1/projects/{pid}/repositories",
        json={"full_name": "org/private", "installation_id": None},
    ).json()["id"]
    uid = alice.me["user"]["id"]
    with transaction() as conn:
        conn.execute(
            sa.update(users).where(users.c.id == uid).values(github_login="alice", github_id=12345)
        )
        conn.execute(
            sa.update(repositories).where(repositories.c.id == rid).values(installation_id=456)
        )
        conn.execute(
            sa.insert(github_repo_access).values(
                user_id=uid,
                full_name="org/private",
                installation_id=456,
                default_branch="main",
                private=True,
            )
        )
    assert alice.post(f"/api/v1/repositories/{rid}/disconnect").status_code == 200
    checks = []

    class FakeGitHub:
        def user_installation(self, login):
            return {"id": 123, "account": {"id": 12345, "type": "User"}}

        def installation_repositories(self, installation_id):
            checks.append(installation_id)
            if installation_id == 123:
                return [{"full_name": "alice/personal", "default_branch": "main", "private": True}]
            raise httpx.HTTPStatusError(
                "org installation removed",
                request=httpx.Request("GET", "https://api.github.com/installation/repositories"),
                response=httpx.Response(404),
            )

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", FakeGitHub)
    result = alice.post(f"/api/v1/repositories/{rid}/reconnect")
    assert result.status_code == 403, (
        f"status={result.status_code}; installations verified={checks}"
    )


def test_operator_confirmed_retry_resolves_provider_ledger(staging):
    from .transports import fake_provider

    alice, app, repo = prepare(staging)

    class ExitAtSend:
        mode = "fake"

        def complete(self, request):
            os._exit(71)

    staging.use_gateway(gateway_factory(ExitAtSend()))
    assert staging.ingest(alice, app).status_code == 202
    staging.drain(kinds=["live_defense_shadow", "live_defense_first_responder"])
    pid = os.fork()
    if pid == 0:
        get_engine().dispose(close=False)
        try:
            staging.drain(kinds=["live_defense_agent_step"])
        finally:
            os._exit(72)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 71
    step = next(j for j in staging.job_rows("live_defense_agent_step") if j["status"] == "running")
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == step["id"])
            .values(lease_expires_at=sa.func.now() - sa.text("interval '1 second'"))
        )
        execution.reconcile(conn)
    assert jobrow(step["id"])["status"] == "recovery_required"
    response = alice.post(
        f"/api/v1/jobs/{step['id']}/recovery",
        json={"resolution": "retry", "note": "Scripted provider confirms request was not applied"},
    )
    assert response.status_code == 200, response.text
    provider = fake_provider(repo / "target")
    staging.use_gateway(gateway_factory(provider))
    staging.drain(kinds=["live_defense_agent_step"])
    assert provider.calls, (
        "authorized retry must reconcile the old reservation rather than defer forever"
    )


def test_viewer_history_does_not_bypass_job_error_redaction(client_factory):
    from helpers import add_member, setup_project

    from backend import jobs

    alice, bob, viewer = client_factory("alice"), client_factory("bob"), client_factory("viewer")
    ids = setup_project(alice, bob)
    org_id = alice.me["memberships"][0]["organization"]["id"]
    add_member(org_id, viewer.me["user"]["id"], role="viewer")
    marker = "history" + "-only-password"
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "review_history_credentials", {"project_id": ids["project"]})
        assert jobs.claim(conn, "history-worker", 60, ["review_history_credentials"])
        jobs.fail(
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
    response = viewer.get(f"/api/v1/jobs/{jid}/history")
    assert response.status_code == 200
    assert marker not in response.text


def test_disconnect_after_response_keeps_settled_attempt_evidence(staging):
    from .transports import fake_provider

    alice, app, repo = prepare(staging)
    fake = fake_provider(repo / "target")

    class DisconnectOnResponse:
        mode = "fake"
        calls = 0

        def complete(self, request):
            self.calls += 1
            response = fake.complete(request)
            if self.calls == 1:
                assert (
                    alice.post(f"/api/v1/repositories/{app.repo_id}/disconnect").status_code == 200
                )
            return response

    provider = DisconnectOnResponse()
    staging.use_gateway(gateway_factory(provider))
    assert staging.ingest(alice, app).status_code == 202
    staging.drain()
    scout = next(i for i in staging.invocations(alice, app.project_id) if i["agent"] == "scout")
    assert scout["status"] == "cancelled"
    assert provider.calls == 1
    evidence = scout["execution"]
    assert len(evidence["provider_attempts"]) == 1
    assert evidence["provider_attempts"][0]["status"] == "settled"
    assert evidence["provider_attempts"][0]["cost_usd"] is not None
    assert evidence["transcript_artifact_id"] is not None


def test_fast_reclaim_cannot_hide_unresolved_provider_from_reconciler(staging):
    from backend import worker

    from .transports import fake_provider

    alice, app, repo = prepare(staging)

    class ExitAtSend:
        mode = "fake"

        def complete(self, request):
            os._exit(71)

    staging.use_gateway(gateway_factory(ExitAtSend()))
    assert staging.ingest(alice, app).status_code == 202
    staging.drain(kinds=["live_defense_shadow", "live_defense_first_responder"])
    pid = os.fork()
    if pid == 0:
        get_engine().dispose(close=False)
        try:
            staging.drain(kinds=["live_defense_agent_step"])
        finally:
            os._exit(72)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 71
    step = next(j for j in staging.job_rows("live_defense_agent_step") if j["status"] == "running")
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_t)
            .where(jobs_t.c.id == step["id"])
            .values(lease_expires_at=sa.func.now() - sa.text("interval '1 second'"))
        )
    provider = fake_provider(repo / "target")
    staging.use_gateway(gateway_factory(provider))
    worker.run_one("fast-reclaimer", ["live_defense_agent_step"])
    assert provider.calls == [], "unresolved provider must not be replayed"
    with transaction() as conn:
        execution.reconcile(conn)
    assert jobrow(step["id"])["status"] == "recovery_required"
