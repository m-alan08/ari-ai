"""Repository picker and the access check on connecting an App-installed repository."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from backend.api.github_repos import store_repo_access
from backend.db import transaction, users
from backend.github import GitHubNotConfigured, user_repositories
from backend.settings import Settings


def _grant(user_id: str, repos: list[dict[str, Any]]) -> None:
    with transaction() as conn:
        store_repo_access(conn, user_id, repos)


def _set_github_id(user_id: str, github_id: int | None) -> None:
    with transaction() as conn:
        conn.execute(sa.update(users).where(users.c.id == user_id).values(github_id=github_id))


def _without_personal_installation(monkeypatch) -> None:
    class NoInstallation:
        def user_installation(self, login):
            return None

        def installation_repositories(self, installation_id):
            return [ACME]

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", NoInstallation)


ACME = {"full_name": "acme/shop", "installation_id": 42, "default_branch": "main", "private": True}


def test_picker_lists_only_the_users_repositories(client_factory, monkeypatch) -> None:
    _without_personal_installation(monkeypatch)
    alice, bob = client_factory("alice"), client_factory("bob")
    _grant(alice.me["user"]["id"], [ACME])
    mine = alice.get("/api/v1/github/repositories").json()["items"]
    assert [r["full_name"] for r in mine] == ["acme/shop"]
    assert mine[0]["connected_repo_id"] is None
    assert bob.get("/api/v1/github/repositories").json()["items"] == []
    assert client_factory().get("/api/v1/github/repositories").status_code == 401


def test_connect_requires_access_through_that_installation(client_factory, monkeypatch) -> None:
    _without_personal_installation(monkeypatch)
    alice, mallory = client_factory("alice"), client_factory("mallory")
    _grant(alice.me["user"]["id"], [ACME])
    for user in (alice, mallory):
        pid = user.post("/api/v1/projects", json={"name": "p"}).json()["id"]
        body = {"full_name": "acme/shop", "installation_id": 42, "default_branch": "main"}
        if user is mallory:
            # Knowing the installation id is not enough: no access row for mallory.
            r = user.post(f"/api/v1/projects/{pid}/repositories", json=body)
            assert r.status_code == 403, r.text
        else:
            wrong = {**body, "installation_id": 43}
            assert user.post(f"/api/v1/projects/{pid}/repositories", json=wrong).status_code == 403
            r = user.post(f"/api/v1/projects/{pid}/repositories", json=body)
            assert r.status_code == 201, r.text
    listed = alice.get("/api/v1/github/repositories").json()["items"][0]
    assert listed["connected_repo_id"] is not None


def test_connect_rejects_a_repo_the_installation_no_longer_grants(
    client_factory, monkeypatch
) -> None:
    alice = client_factory("alice")
    _grant(alice.me["user"]["id"], [ACME])
    pid = alice.post("/api/v1/projects", json={"name": "p"}).json()["id"]

    class FakeApp:
        def installation_repositories(self, installation_id):
            return [{"full_name": "acme/other"}]  # acme/shop was removed from the installation

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", FakeApp)
    body = {"full_name": "acme/shop", "installation_id": 42, "default_branch": "main"}
    r = alice.post(f"/api/v1/projects/{pid}/repositories", json=body)
    assert r.status_code == 403, r.text
    assert "installation" in r.json()["error"]["message"]


def test_connect_maps_a_deleted_installation_to_an_actionable_error(
    client_factory, monkeypatch
) -> None:
    alice = client_factory("alice")
    _grant(alice.me["user"]["id"], [ACME])
    pid = alice.post("/api/v1/projects", json={"name": "p"}).json()["id"]

    class DeadApp:
        def installation_repositories(self, installation_id):
            request = httpx.Request("GET", "https://api.github.com/installation/repositories")
            raise httpx.HTTPStatusError(
                "404", request=request, response=httpx.Response(404, request=request)
            )

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", DeadApp)
    body = {"full_name": "acme/shop", "installation_id": 42, "default_branch": "main"}
    r = alice.post(f"/api/v1/projects/{pid}/repositories", json=body)
    assert r.status_code == 403, r.text
    assert "reinstall" in r.json()["error"]["message"]


def test_connect_reports_when_github_cannot_confirm_the_installation(
    client_factory, monkeypatch
) -> None:
    alice = client_factory("alice")
    _grant(alice.me["user"]["id"], [ACME])
    pid = alice.post("/api/v1/projects", json={"name": "p"}).json()["id"]

    class DownApp:
        def installation_repositories(self, installation_id):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", DownApp)
    body = {"full_name": "acme/shop", "installation_id": 42, "default_branch": "main"}
    r = alice.post(f"/api/v1/projects/{pid}/repositories", json=body)
    assert r.status_code == 503, r.text


def test_access_list_is_replaced_on_each_sign_in(client_factory, monkeypatch) -> None:
    _without_personal_installation(monkeypatch)
    alice = client_factory("alice")
    uid = alice.me["user"]["id"]
    _grant(uid, [ACME])
    _grant(uid, [{**ACME, "full_name": "acme/api"}])
    names = [r["full_name"] for r in alice.get("/api/v1/github/repositories").json()["items"]]
    assert names == ["acme/api"]


def test_picker_refreshes_personal_installation_additions(client_factory, monkeypatch) -> None:
    alice = client_factory("alice")
    user_id = alice.me["user"]["id"]
    _grant(user_id, [ACME])
    _set_github_id(user_id, 123)

    class FakeApp:
        def user_installation(self, login):
            assert login == "alice"
            return {"id": 42, "account": {"type": "User", "id": 123, "login": "renamed"}}

        def installation_repositories(self, installation_id):
            assert installation_id == 42
            return [
                ACME,
                {
                    "full_name": "acme/api",
                    "installation_id": 42,
                    "default_branch": "trunk",
                    "private": False,
                },
            ]

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", FakeApp)
    response = alice.get("/api/v1/github/repositories")

    assert response.status_code == 200, response.text
    assert {item["full_name"] for item in response.json()["items"]} == {
        "acme/shop",
        "acme/api",
    }


def test_picker_refresh_removes_repositories_removed_from_personal_installation(
    client_factory, monkeypatch
) -> None:
    alice = client_factory("alice")
    user_id = alice.me["user"]["id"]
    _grant(user_id, [ACME])
    monkeypatch.setattr(
        "backend.api.github_repos.get_settings",
        lambda: Settings(auth_dev_login_enabled=False),
    )

    class FakeApp:
        def user_installation(self, login):
            assert login == "alice"
            return {"id": 42, "account": {"type": "User", "id": 123, "login": "ALICE"}}

        def installation_repositories(self, installation_id):
            assert installation_id == 42
            return [
                {
                    "full_name": "acme/api",
                    "installation_id": 42,
                    "default_branch": None,
                    "private": False,
                }
            ]

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", FakeApp)
    response = alice.get("/api/v1/github/repositories")

    assert response.status_code == 200, response.text
    assert [item["full_name"] for item in response.json()["items"]] == ["acme/api"]
    assert response.json()["items"][0]["default_branch"] == "main"


@pytest.mark.parametrize(
    ("github_id", "account"),
    [
        (123, {"type": "Organization", "id": 123, "login": "alice"}),
        (123, {"type": "User", "id": 456, "login": "alice"}),
    ],
)
def test_picker_does_not_refresh_organization_or_mismatched_installations(
    client_factory, monkeypatch, github_id, account
) -> None:
    alice = client_factory("alice")
    user_id = alice.me["user"]["id"]
    _grant(user_id, [ACME])
    _set_github_id(user_id, github_id)

    class FakeApp:
        def user_installation(self, login):
            return {"id": 42, "account": account}

        def installation_repositories(self, installation_id):
            raise AssertionError("untrusted installations must not be listed")

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", FakeApp)
    response = alice.get("/api/v1/github/repositories")

    assert response.status_code == 200, response.text
    assert [item["full_name"] for item in response.json()["items"]] == ["acme/shop"]


@pytest.mark.parametrize("failure", ["http", "not-configured"])
def test_picker_serves_snapshot_when_github_refresh_fails(
    client_factory, monkeypatch, failure
) -> None:
    alice = client_factory("alice")
    user_id = alice.me["user"]["id"]
    _grant(user_id, [ACME])

    class FakeApp:
        def __init__(self):
            if failure == "not-configured":
                raise GitHubNotConfigured("not configured")

        def user_installation(self, login):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", FakeApp)
    response = alice.get("/api/v1/github/repositories")

    assert response.status_code == 200, response.text
    assert [item["full_name"] for item in response.json()["items"]] == ["acme/shop"]


def test_picker_keeps_rows_from_other_installations_when_refreshing(
    client_factory, monkeypatch
) -> None:
    alice = client_factory("alice")
    user_id = alice.me["user"]["id"]
    other_installation = {
        "full_name": "org/keep",
        "installation_id": 99,
        "default_branch": "main",
        "private": True,
    }
    _grant(user_id, [ACME, other_installation])
    _set_github_id(user_id, 123)

    class FakeApp:
        def user_installation(self, login):
            return {"id": 42, "account": {"type": "User", "id": 123, "login": "alice"}}

        def installation_repositories(self, installation_id):
            return [
                {
                    "full_name": "acme/new",
                    "installation_id": 42,
                    "default_branch": "main",
                    "private": False,
                }
            ]

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", FakeApp)
    response = alice.get("/api/v1/github/repositories")

    assert response.status_code == 200, response.text
    items = {item["full_name"]: item for item in response.json()["items"]}
    assert set(items) == {"acme/new", "org/keep"}
    assert items["org/keep"]["installation_id"] == 99


def test_picker_skips_refresh_without_a_github_login(client_factory, monkeypatch) -> None:
    alice = client_factory("alice")
    user_id = alice.me["user"]["id"]
    _grant(user_id, [ACME])
    with transaction() as conn:
        conn.execute(sa.update(users).where(users.c.id == user_id).values(github_login=""))

    class UnexpectedApp:
        def __init__(self):
            raise AssertionError("a missing GitHub login should skip refresh")

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", UnexpectedApp)
    response = alice.get("/api/v1/github/repositories")

    assert response.status_code == 200, response.text
    assert [item["full_name"] for item in response.json()["items"]] == ["acme/shop"]


def test_picker_skips_refresh_for_dev_login(client_factory, monkeypatch) -> None:
    alice = client_factory("alice")
    user_id = alice.me["user"]["id"]
    _grant(user_id, [ACME])

    class UnexpectedApp:
        def __init__(self):
            raise AssertionError("dev login must not trigger a GitHub refresh")

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", UnexpectedApp)
    response = alice.get("/api/v1/github/repositories")

    assert response.status_code == 200, response.text
    assert [item["full_name"] for item in response.json()["items"]] == ["acme/shop"]


def test_user_repositories_walks_installations() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer user-token"
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"installations": [{"id": 7}]})
        if request.url.path == "/user/installations/7/repositories":
            return httpx.Response(
                200,
                json={
                    "repositories": [
                        {"full_name": "acme/shop", "default_branch": "trunk", "private": True}
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.Client(
        base_url="https://api.github.test", transport=httpx.MockTransport(handler)
    )
    repos = user_repositories("user-token", settings=Settings(), client=client)
    assert repos == [
        {"full_name": "acme/shop", "installation_id": 7, "default_branch": "trunk", "private": True}
    ]


def test_returning_from_app_install_restarts_sign_in(client_factory) -> None:
    c = client_factory()
    r = c.get(
        "/api/v1/auth/github/callback?code=x&installation_id=9&setup_action=install",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"].endswith("/api/v1/auth/github/login")
    # Without the install marker, a missing state is still rejected on the public host.
    public = {"x-forwarded-host": "localhost:8000"}
    r = c.get("/api/v1/auth/github/callback?code=x", headers=public, follow_redirects=False)
    assert r.status_code == 403


def test_callback_on_the_backend_domain_bounces_to_the_public_origin(client_factory) -> None:
    c = client_factory()
    q = "code=abc&state=s1"
    # GitHub sent the browser to the backend's own domain: no state cookie there.
    r = c.get(f"/api/v1/auth/github/callback?{q}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"http://localhost:8000/api/v1/auth/github/callback?{q}"
    # Through the public origin with a wrong state it is still refused, never bounced again.
    c.cookies.set("hackatl_oauth_state", "other")
    public = {"x-forwarded-host": "localhost:8000"}
    r = c.get(f"/api/v1/auth/github/callback?{q}", headers=public, follow_redirects=False)
    assert r.status_code == 403


def test_recent_commits_reads_github_through_the_installation(client_factory, monkeypatch) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    _grant(alice.me["user"]["id"], [ACME])
    pid = alice.post("/api/v1/projects", json={"name": "p"}).json()["id"]
    body = {"full_name": "acme/shop", "installation_id": 42, "default_branch": "main"}
    rid = alice.post(f"/api/v1/projects/{pid}/repositories", json=body).json()["id"]
    calls = []

    class FakeApp:
        def recent_commits(self, installation_id, full_name, branch):
            calls.append((installation_id, full_name, branch))
            return [
                {
                    "sha": "a" * 40,
                    "message": "feat: x",
                    "author": "alice",
                    "committed_at": "2026-10-17T00:00:00Z",
                    "html_url": None,
                }
            ]

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", FakeApp)
    r = alice.get(f"/api/v1/repositories/{rid}/commits")
    assert r.status_code == 200, r.text
    assert r.json()["available"] is True
    assert [c["sha"] for c in r.json()["items"]] == ["a" * 40]
    assert calls == [(42, "acme/shop", "main")]
    # Another user cannot read the project's commits (no existence leak).
    assert bob.get(f"/api/v1/repositories/{rid}/commits").status_code == 404


def test_recent_commits_without_installation_is_explicitly_unavailable(client_factory) -> None:
    alice = client_factory("alice")
    pid = alice.post("/api/v1/projects", json={"name": "p"}).json()["id"]
    body = {"full_name": "acme/byname", "installation_id": None, "default_branch": "main"}
    rid = alice.post(f"/api/v1/projects/{pid}/repositories", json=body).json()["id"]
    r = alice.get(f"/api/v1/repositories/{rid}/commits").json()
    assert r["available"] is False and r["items"] == []


def test_github_recent_commits_parses_the_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "inst"})
        assert request.url.path == "/repos/acme/shop/commits"
        assert request.url.params["sha"] == "main"
        return httpx.Response(
            200,
            json=[
                {
                    "sha": "b" * 40,
                    "html_url": "https://github.com/acme/shop/commit/bbb",
                    "author": {"login": "alice"},
                    "commit": {
                        "message": "fix: y\n\nbody",
                        "author": {"name": "A", "date": "2026-10-17T01:00:00Z"},
                    },
                },
                {"sha": "short", "commit": {}},
            ],
        )

    from backend import github as gh

    client = httpx.Client(
        base_url="https://api.github.test", transport=httpx.MockTransport(handler)
    )
    app = gh.GitHubApp.__new__(gh.GitHubApp)
    app.settings, app._client, app._tokens, app._key = Settings(), client, {}, ""
    app._app_jwt = lambda: "jwt"  # type: ignore[method-assign]
    assert app.recent_commits(1, "acme/shop", "main") == [
        {
            "sha": "b" * 40,
            "message": "fix: y",
            "author": "alice",
            "committed_at": "2026-10-17T01:00:00Z",
            "html_url": "https://github.com/acme/shop/commit/bbb",
        }
    ]
