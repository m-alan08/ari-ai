"""Codex re-review strict assertions for the onboarding/connect territory.

R3: reconnect must verify installation access with GitHub *fresh* — a revoked
installation or a GitHub outage must never reactivate Ari processing from the
stale sign-in snapshot cache. R4: the repository picker must not join repo rows
across tenant boundaries — shared GitHub visibility of a full_name is not Ari
tenant membership.

Assertions mirror `backend/tests/live_defense_e2e/test_release_rereview.py` on
`upstream/codex/release-0019-rereview` (only the github_repos tests; that dir's
harness does not exist on this branch).
"""

import sqlalchemy as sa

from backend.db import transaction


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
