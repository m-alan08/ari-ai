"""Installation lifecycle deliveries keep `repositories` in sync with GitHub."""

from __future__ import annotations

import sqlalchemy as sa
from helpers import post_webhook, push_payload, setup_project

from backend import worker
from backend.db import github_repo_access, jobs, repositories, transaction


def _installation_deleted(installation_id: int, *names: str) -> dict:
    return {
        "action": "deleted",
        "installation": {"id": installation_id, "account": {"login": "acme"}},
        "repositories": [{"full_name": n} for n in names],
        "sender": {"login": "acme"},
    }


def _set_installation(repo_id: str, installation_id: int | None) -> None:
    with transaction() as conn:
        conn.execute(
            sa.update(repositories)
            .where(repositories.c.id == repo_id)
            .values(installation_id=installation_id)
        )


def _installation_of(repo_id: str):
    with transaction() as conn:
        return conn.execute(
            sa.select(repositories.c.installation_id).where(repositories.c.id == repo_id)
        ).scalar_one()


def test_installation_deleted_detaches_repository(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    _set_installation(ids["repo"], 99)

    r = post_webhook(alice, _installation_deleted(99, "acme/payments-lite"), event="installation")
    assert r.status_code == 202
    worker.drain(kinds=["process_github_delivery"])

    assert _installation_of(ids["repo"]) is None
    audit = alice.get("/api/v1/audit", params={"project_id": ids["project"]}).json()
    assert any(a["action"] == "repository.installation_detached" for a in audit["items"])


def test_repositories_removed_detaches_only_the_removed_repo(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    _set_installation(ids["repo"], 99)
    other = alice.post(
        f"/api/v1/projects/{ids['project']}/repositories",
        json={"full_name": "acme/other", "installation_id": None},
    ).json()
    _set_installation(other["id"], 99)

    payload = {
        "action": "removed",
        "installation": {"id": 99, "account": {"login": "acme"}},
        "repositories_removed": [{"full_name": "acme/payments-lite"}],
        "repositories_added": [],
        "sender": {"login": "acme"},
    }
    assert post_webhook(alice, payload, event="installation_repositories").status_code == 202
    worker.drain(kinds=["process_github_delivery"])

    assert _installation_of(ids["repo"]) is None
    assert _installation_of(other["id"]) == 99


def test_repository_rename_updates_connected_name_and_access_rows(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    _set_installation(ids["repo"], 99)
    with transaction() as conn:
        conn.execute(
            sa.insert(github_repo_access).values(
                user_id=alice.me["user"]["id"],
                full_name="acme/payments-lite",
                installation_id=99,
                default_branch="main",
                private=True,
            )
        )

    payload = {
        "action": "renamed",
        "repository": {
            "full_name": "acme/payments-plus",
            "name": "payments-plus",
            "owner": {"login": "acme"},
        },
        "changes": {"repository": {"name": {"from": "payments-lite"}}},
        "installation": {"id": 99},
        "sender": {"login": "acme"},
    }
    assert post_webhook(alice, payload, event="repository").status_code == 202
    worker.drain(kinds=["process_github_delivery"])

    with transaction() as conn:
        row = conn.execute(
            sa.select(repositories.c.full_name).where(repositories.c.id == ids["repo"])
        ).scalar_one()
        assert row == "acme/payments-plus"
        assert conn.execute(
            sa.select(github_repo_access.c.full_name).where(
                github_repo_access.c.full_name == "acme/payments-plus"
            )
        ).first()


def test_renamed_repo_keeps_scheduling_automatic_assessments(
    client_factory, settings_override, monkeypatch
) -> None:
    settings_override(github_auto_commit_authors="adamissac")
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    _set_installation(ids["repo"], 99)

    class GitHub:
        def commit(self, installation, repo, sha):
            return {"sha": sha, "author": {"login": "adamissac"}}

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)

    payload = {
        "action": "renamed",
        "repository": {
            "full_name": "acme/payments-plus",
            "name": "payments-plus",
            "owner": {"login": "acme"},
        },
        "changes": {"repository": {"name": {"from": "payments-lite"}}},
        "installation": {"id": 99},
        "sender": {"login": "acme"},
    }
    post_webhook(alice, payload, event="repository")
    worker.drain(kinds=["process_github_delivery"])

    p = push_payload(["app.py"], repo="acme/payments-plus")
    p["sender"] = {"login": "adamissac"}
    p["commits"] = [{"id": "b" * 40, "modified": ["app.py"]}]
    assert post_webhook(alice, p).status_code == 202
    worker.drain(kinds=["process_github_delivery"])
    with transaction() as conn:
        assert conn.execute(
            sa.select(jobs.c.id).where(jobs.c.kind == "assess_github_commit")
        ).first()


def test_unrelated_repository_actions_change_nothing(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    _set_installation(ids["repo"], 99)

    payload = {
        "action": "edited",
        "repository": {"full_name": "acme/payments-lite"},
        "installation": {"id": 99},
        "sender": {"login": "acme"},
    }
    assert post_webhook(alice, payload, event="repository").status_code == 202
    worker.drain(kinds=["process_github_delivery"])
    assert _installation_of(ids["repo"]) == 99
