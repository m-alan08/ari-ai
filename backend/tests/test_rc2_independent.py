"""Codex RC2 independent-review strict assertions for the onboarding/connect
territory.

N2: reconnect must verify the *target* installation/repository grant — a
healthy unrelated personal installation can never stand in for the recorded
grant (covers revoked org installs, API 401/403/404, GitHub outage, membership
changes).

Assertion mirrors `backend/tests/live_defense_e2e/test_rc2_independent.py` on
`upstream/codex/rc2-independent-review` @ 46143f9f (only the github_repos test;
that dir's harness does not exist on this branch).
"""

import sqlalchemy as sa

from backend.db import transaction


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
