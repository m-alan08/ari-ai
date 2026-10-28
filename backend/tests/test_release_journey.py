"""Release journey: GitHub sign-in, install, connect, setup, multi-repo, disconnect, reauthorize.

Drives the real OAuth callback, picker, connect/reconnect and webhook code paths with GitHub
replaced at the HTTP/App boundary, and the deployment treated as non-development wherever the
code consults it for access checks (so the installation and collaborator checks really run).
"""

from __future__ import annotations

import inspect
import types
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from helpers import add_member, post_webhook, push_payload

from backend import auto_commits, worker
from backend.db import jobs, transaction
from backend.settings import get_settings

GH_ID = 4242
LOGIN = "octo-dev"  # see test_default_bot_exclusion_does_not_skip_human_logins
APP, API = "octo/app", "octo/api"


class FakeGitHub:
    """GitHub as the App sees it: one personal installation that can be removed and re-added."""

    def __init__(self) -> None:
        self.installation: int | None = 555
        self.repos = [APP, API]

    def rows(self) -> list[dict[str, Any]]:
        if self.installation is None:
            return []
        return [
            {
                "full_name": n,
                "installation_id": self.installation,
                "default_branch": "main",
                "private": True,
            }
            for n in self.repos
        ]


@pytest.fixture
def github(monkeypatch, settings_override):
    settings_override(
        github_oauth_client_id="cid",
        github_oauth_client_secret="csecret",
        github_auto_commit_authors=LOGIN,
    )
    gh = FakeGitHub()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_test"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": LOGIN, "id": GH_ID, "name": "Octo"})
        return httpx.Response(404)

    real_client = httpx.Client
    monkeypatch.setattr(
        "backend.api.auth.httpx",
        types.SimpleNamespace(
            Client=lambda **kw: real_client(transport=httpx.MockTransport(handler)),
            HTTPError=httpx.HTTPError,
        ),
    )
    monkeypatch.setattr(
        "backend.api.auth.user_repositories", lambda token, settings=None: gh.rows()
    )

    class App:
        def user_installation(self, login: str) -> dict[str, Any] | None:
            if gh.installation is None or login != LOGIN:
                return None
            return {"id": gh.installation, "account": {"type": "User", "id": GH_ID, "login": LOGIN}}

        def installation_repositories(self, installation_id: int) -> list[dict[str, Any]]:
            if installation_id != gh.installation:
                request = httpx.Request("GET", "https://api.github.com/installation/repositories")
                raise httpx.HTTPStatusError(
                    "404", request=request, response=httpx.Response(404, request=request)
                )
            return gh.rows()

        def collaborator_permission(self, installation_id, full_name, login, github_id) -> str:
            ok = installation_id == gh.installation and github_id == GH_ID and login == LOGIN
            return "admin" if ok else "none"

        def commit(self, installation_id, full_name, sha):
            return {"sha": sha, "author": {"login": LOGIN}, "parents": [{"sha": "a" * 40}]}

    monkeypatch.setattr("backend.api.github_repos.GitHubApp", App)
    monkeypatch.setattr("backend.auto_commits.GitHubApp", App)
    staging = get_settings().model_copy(update={"app_env": "staging"})
    monkeypatch.setattr("backend.api.github_repos.get_settings", lambda: staging)
    monkeypatch.setattr("backend.api.routes.get_settings", lambda: staging)
    return gh


def _sign_in(client) -> dict[str, Any]:
    start = client.get("/api/v1/auth/github/login", follow_redirects=False)
    assert start.status_code == 302 and "client_id=cid" in start.headers["location"]
    state = start.headers["location"].split("state=")[1].split("&")[0]
    forged = client.get(
        "/api/v1/auth/github/callback",
        params={"code": "c", "state": "forged"},
        follow_redirects=False,
    )
    assert forged.status_code == 403
    done = client.get(
        "/api/v1/auth/github/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert done.status_code == 302, done.text
    assert done.headers["location"].endswith("/connect")
    me = client.get("/api/v1/me")
    assert me.status_code == 200, me.text
    body = me.json()
    client.headers["X-CSRF-Token"] = body["csrf_token"]
    return body


def _picker(client) -> dict[str, dict[str, Any]]:
    r = client.get("/api/v1/github/repositories")
    assert r.status_code == 200, r.text
    return {row["full_name"]: row for row in r.json()["items"]}


def _repo(client, rid: str) -> dict[str, Any]:
    r = client.get(f"/api/v1/repositories/{rid}")
    assert r.status_code == 200, r.text
    return r.json()


def _commit_jobs() -> list[dict[str, Any]]:
    with transaction() as conn:
        return [
            dict(r._mapping)
            for r in conn.execute(
                sa.select(jobs.c.kind, jobs.c.payload).where(jobs.c.kind == "assess_github_commit")
            )
        ]


def _project(client, *, monitoring: bool = False) -> str:
    settings = {"monitoring_policy": {"enabled": True}} if monitoring else {}
    r = client.post("/api/v1/projects", json={"name": "octo", "settings": settings})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _connect(client, pid: str, full_name: str, installation_id: int | None = 555):
    return client.post(
        f"/api/v1/projects/{pid}/repositories",
        json={"full_name": full_name, "installation_id": installation_id},
    )


def _uninstall(client, github: FakeGitHub, installation_id: int) -> None:
    deleted = {"action": "deleted", "installation": {"id": installation_id}}
    assert post_webhook(client, deleted, event="installation").status_code == 202
    worker.drain(kinds=["process_github_delivery"])
    github.installation = None


def _reinstall(client, github: FakeGitHub, installation_id: int) -> None:
    github.installation = installation_id
    created = {
        "action": "created",
        "installation": {"id": installation_id, "account": {"login": LOGIN}},
        "repositories": [{"full_name": n} for n in github.repos],
    }
    assert post_webhook(client, created, event="installation").status_code == 202
    worker.drain(kinds=["process_github_delivery"])


def _monitored_project(client_factory) -> tuple[Any, str, str, str]:
    c = client_factory()
    me = _sign_in(c)
    bob = client_factory("bob")
    add_member(me["memberships"][0]["organization"]["id"], bob.me["user"]["id"])
    pid = _project(c, monitoring=True)
    app, api = _connect(c, pid, APP).json()["id"], _connect(c, pid, API).json()["id"]
    expires = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    scope = c.post(
        f"/api/v1/projects/{pid}/scopes",
        json={
            "name": "sandbox",
            "expires_at": expires,
            "targets": [
                {
                    "kind": "sandbox_build",
                    "repo_id": rid,
                    "build_recipe_ref": "examples/payments-lite/sandbox.yaml",
                    "exposed_ports": [8080],
                }
                for rid in (app, api)
            ],
            "allowed_check_categories": ["authorization.ownership"],
        },
    )
    assert scope.status_code == 201, scope.text
    assert bob.post(f"/api/v1/scopes/{scope.json()['id']}/approve").status_code == 200
    for rid in (app, api):
        assert (
            c.patch(f"/api/v1/repositories/{rid}", json={"automatic_assessments": True}).status_code
            == 200
        )

    return c, pid, app, api


def test_sign_in_lists_installed_repositories_and_connects_them_into_one_project(
    client_factory, github
) -> None:
    c = client_factory()
    me = _sign_in(c)
    assert me["user"]["github_login"] == LOGIN
    picker = _picker(c)
    assert set(picker) == {APP, API}
    assert all(
        r["connected_repo_id"] is None and r["installation_id"] == 555 for r in picker.values()
    )

    pid = _project(c)
    assert _connect(c, pid, APP, installation_id=999).status_code in (403, 409, 422)
    assert _connect(c, pid, "octo/not-installed").status_code in (403, 404, 422)
    app, api = _connect(c, pid, APP), _connect(c, pid, API)
    assert app.status_code == 201 and api.status_code == 201, (app.text, api.text)
    assert _connect(c, pid, APP).json()["id"] == app.json()["id"]  # repeat is a no-op

    picker = _picker(c)
    assert picker[APP]["connected_repo_id"] == app.json()["id"]
    assert picker[API]["connected_repo_id"] == api.json()["id"]
    listed = c.get(f"/api/v1/projects/{pid}/repositories").json()["items"]
    assert sorted(r["full_name"] for r in listed) == [API, APP]
    assert all(r["installation_id"] == 555 and r["automatic_assessments"] is False for r in listed)


def test_each_repository_keeps_its_own_setup_and_lifecycle(client_factory, github) -> None:
    c = client_factory()
    _sign_in(c)
    pid = _project(c)
    app, api = _connect(c, pid, APP).json()["id"], _connect(c, pid, API).json()["id"]

    first = c.get(f"/api/v1/repositories/{app}/setup").json()
    assert first["complete"] is False
    done = c.patch(f"/api/v1/repositories/{app}/setup", json={"answers": {}, "complete": True})
    assert done.status_code == 200, done.text
    assert (
        c.patch(
            f"/api/v1/repositories/{app}/setup", json={"answers": {}, "complete": False}
        ).json()["complete"]
        is True
    )  # completion is sticky
    assert c.get(f"/api/v1/repositories/{api}/setup").json()["complete"] is False

    assert (
        c.patch(f"/api/v1/repositories/{api}", json={"automatic_assessments": True}).status_code
        == 200
    )
    assert c.post(f"/api/v1/repositories/{app}/disconnect").status_code == 200
    assert _repo(c, app)["disconnected"] is True
    sibling = _repo(c, api)
    assert sibling["disconnected"] is False and sibling["automatic_assessments"] is True
    assert sibling["installation_id"] == 555
    blocked = c.patch(f"/api/v1/repositories/{app}/setup", json={"answers": {}, "complete": True})
    assert blocked.status_code == 409
    assert c.get(f"/api/v1/repositories/{app}/setup").json()["complete"] is True  # history kept

    assert c.post(f"/api/v1/repositories/{app}/reconnect").status_code == 200
    back = _repo(c, app)
    assert back["disconnected"] is False and back["automatic_assessments"] is False
    assert c.get(f"/api/v1/repositories/{app}/setup").json()["complete"] is True


def test_uninstall_blocks_reconnect_until_the_app_is_reinstalled_and_user_reauthorizes(
    client_factory, github
) -> None:
    c = client_factory()
    _sign_in(c)
    pid = _project(c)
    app, api = _connect(c, pid, APP).json()["id"], _connect(c, pid, API).json()["id"]
    assert c.post(f"/api/v1/repositories/{app}/disconnect").status_code == 200

    _uninstall(c, github, 555)
    assert _repo(c, api)["installation_id"] is None and _repo(c, api)["disconnected"] is False
    assert _picker(c) == {}
    denied = c.post(f"/api/v1/repositories/{app}/reconnect")
    assert denied.status_code == 403 and "install the App" in denied.text
    assert _repo(c, app)["disconnected"] is True
    status = c.get(f"/api/v1/repositories/{api}/automation-status").json()
    assert "no_github_app" in {r["code"] for r in status["reasons"]}

    _reinstall(c, github, 777)
    _sign_in(c)  # GitHub returns the user to sign-in after installing; the snapshot refreshes
    assert {r["installation_id"] for r in _picker(c).values()} == {777}
    assert c.post(f"/api/v1/repositories/{app}/reconnect").status_code == 200
    assert _repo(c, app)["installation_id"] == 777
    # The live sibling an uninstall detached is reattached the same way.
    assert c.post(f"/api/v1/repositories/{api}/reconnect").status_code == 200
    assert _repo(c, api)["installation_id"] == 777
    status = c.get(f"/api/v1/repositories/{api}/automation-status").json()
    assert "no_github_app" not in {r["code"] for r in status["reasons"]}


def test_reauthorized_repository_reviews_new_branch_commits_and_disconnected_sibling_does_not(
    client_factory, github
) -> None:
    c, pid, app, api = _monitored_project(client_factory)
    _uninstall(c, github, 555)
    _reinstall(c, github, 777)
    _sign_in(c)
    assert c.post(f"/api/v1/repositories/{api}/reconnect").status_code == 200
    assert c.post(f"/api/v1/repositories/{app}/disconnect").status_code == 200

    _push(c, API, "c" * 40, "refs/heads/feature/new-branch", LOGIN, 777)
    _push(c, APP, "d" * 40, "refs/heads/main", LOGIN, 777)
    worker.drain(kinds=["process_github_delivery"])
    scheduled = _commit_jobs()
    assert len(scheduled) == 1, scheduled
    assert scheduled[0]["payload"]["repo_id"] == api
    assert scheduled[0]["payload"]["ref"] == "refs/heads/feature/new-branch"
    assert scheduled[0]["payload"]["installation_id"] == 777


def _push(client, full_name: str, sha: str, ref: str, sender: str, installation_id: int) -> None:
    p = push_payload(["app.py"], head=sha, repo=full_name)
    p["ref"] = ref
    p["sender"] = {"login": sender}
    p["installation"] = {"id": installation_id}
    p["commits"] = [{"id": sha, "modified": ["app.py"]}]
    assert post_webhook(client, p).status_code == 202


def _author_patterns_use_literal_brackets() -> bool:
    """True once the review-pipeline fix (adamissac/ari-ai#693) is in the tree."""
    return "literal_brackets" in inspect.signature(auto_commits._matches).parameters


# Strict xfail only while the defect is present, so the test is a hard assertion in any
# tree that contains the fix and still fails loudly if the fix regresses or the probe lies.
@pytest.mark.xfail(
    condition=not _author_patterns_use_literal_brackets(),
    strict=True,
    reason="P1: fnmatch reads the default '*[bot]' exclusion as a character class, so it "
    "skips every login ending in b/o/t; fixed by the review-pipeline PR (#693)",
)
@pytest.mark.parametrize("login", ["bob", "octo", "scott"])
def test_default_bot_exclusion_does_not_skip_human_logins(
    client_factory, github, settings_override, login
) -> None:
    # Allowlist both senders so only the project's default reviewer exclusion decides.
    settings_override(github_auto_commit_authors=f"{LOGIN},{login},dependabot[bot]")
    c, _, app, _ = _monitored_project(client_factory)
    _push(c, APP, "e" * 40, "refs/heads/main", "dependabot[bot]", 555)
    worker.drain(kinds=["process_github_delivery"])
    assert _commit_jobs() == []
    _push(c, APP, "f" * 40, "refs/heads/main", login, 555)
    worker.drain(kinds=["process_github_delivery"])
    scheduled = _commit_jobs()
    assert [j["payload"]["repo_id"] for j in scheduled] == [app], scheduled
