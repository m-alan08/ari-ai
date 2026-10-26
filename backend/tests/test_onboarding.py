"""Customer onboarding: computed journey progress, test events, repo disconnect.

The status endpoint derives every step from real tables; it must reflect exactly
what the customer did and nothing more. Test events go through the real
owned_staging ingest path and never execute.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.db import (
    audit_events,
    deploy_commit_mappings,
    monitoring_events,
    repositories,
    transaction,
)
from shared.base import utcnow


def _step(status: dict, step_id: str) -> dict:
    return next(s for s in status["steps"] if s["id"] == step_id)


@pytest.fixture
def project(client_factory):
    alice = client_factory("alice")
    pid = alice.post("/api/v1/projects", json={"name": "acme"}).json()["id"]
    return alice, pid


def test_empty_project_reports_frontier(project) -> None:
    alice, pid = project
    status = alice.get(f"/api/v1/projects/{pid}/onboarding")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["complete"] is False
    assert _step(body, "sign_in")["state"] == "done"
    assert _step(body, "organization")["state"] == "done"
    assert _step(body, "repository")["state"] == "todo"
    assert _step(body, "github_access")["state"] == "blocked"
    assert _step(body, "snapshot")["state"] == "blocked"
    assert _step(body, "application")["state"] == "todo"
    assert _step(body, "deploy_mapping")["state"] == "blocked"
    assert _step(body, "test_event")["state"] == "blocked"


def test_project_isolation(client_factory) -> None:
    alice = client_factory("alice")
    mallory = client_factory("mallory")
    pid = alice.post("/api/v1/projects", json={"name": "acme"}).json()["id"]
    assert mallory.get(f"/api/v1/projects/{pid}/onboarding").status_code == 404
    assert mallory.post(f"/api/v1/projects/{pid}/onboarding/test-event", json={}).status_code == 404


def test_connect_snapshot_and_access_steps(project) -> None:
    alice, pid = project
    rid = alice.post(
        f"/api/v1/projects/{pid}/repositories",
        json={"full_name": "acme/payments", "installation_id": None},
    ).json()["id"]

    status = alice.get(f"/api/v1/projects/{pid}/onboarding").json()
    assert _step(status, "repository")["state"] == "done"
    # Connected by name only: the App grant step is honestly still open.
    assert _step(status, "github_access")["state"] == "todo"
    assert _step(status, "snapshot")["state"] == "todo"

    with transaction() as conn:
        conn.execute(
            sa.update(repositories).where(repositories.c.id == rid).values(installation_id=123)
        )
    status = alice.get(f"/api/v1/projects/{pid}/onboarding").json()
    assert _step(status, "github_access")["state"] == "done"

    snap = alice.post(f"/api/v1/repositories/{rid}/snapshots", json={"commit_sha": "b" * 40})
    assert snap.status_code == 202, snap.text
    status = alice.get(f"/api/v1/projects/{pid}/onboarding").json()
    assert _step(status, "snapshot")["state"] == "in_progress"

    # Failed snapshots surface as a retryable failure, never silent.
    from backend.db import snapshots

    sid = snap.json()["id"]
    with transaction() as conn:
        doc = dict(
            conn.execute(sa.select(snapshots.c.doc).where(snapshots.c.id == sid)).scalar_one()
        )
        doc["status"] = "failed"
        conn.execute(
            sa.update(snapshots).where(snapshots.c.id == sid).values(status="failed", doc=doc)
        )
    status = alice.get(f"/api/v1/projects/{pid}/onboarding").json()
    assert _step(status, "snapshot")["state"] == "failed"


def test_scope_steps(client_factory) -> None:
    from datetime import UTC, datetime, timedelta

    alice, bob = client_factory("alice"), client_factory("bob")
    pid = alice.post("/api/v1/projects", json={"name": "acme"}).json()["id"]
    rid = alice.post(
        f"/api/v1/projects/{pid}/repositories", json={"full_name": "acme/payments"}
    ).json()["id"]
    from helpers import add_member

    add_member(alice.me["memberships"][0]["organization"]["id"], bob.me["user"]["id"])

    scope = alice.post(
        f"/api/v1/projects/{pid}/scopes",
        json={
            "name": "sandbox",
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            "targets": [
                {
                    "kind": "sandbox_build",
                    "repo_id": rid,
                    "build_recipe_ref": "examples/payments-lite/sandbox.yaml",
                }
            ],
        },
    )
    assert scope.status_code == 201, scope.text
    status = alice.get(f"/api/v1/projects/{pid}/onboarding").json()
    assert _step(status, "scope")["state"] == "blocked"

    assert bob.post(f"/api/v1/scopes/{scope.json()['id']}/approve").status_code == 200
    status = alice.get(f"/api/v1/projects/{pid}/onboarding").json()
    assert _step(status, "scope")["state"] == "done"


def _app(alice, pid: str, *, token: bool = True) -> dict:
    app = alice.post(
        f"/api/v1/projects/{pid}/applications",
        json={"name": "staging", "environment": "owned_test"},
    )
    assert app.status_code == 201, app.text
    app = app.json()
    if token:
        minted = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens")
        assert minted.status_code == 201, minted.text
    return app


def test_application_mapping_and_test_event(project) -> None:
    alice, pid = project
    rid = alice.post(
        f"/api/v1/projects/{pid}/repositories", json={"full_name": "acme/payments"}
    ).json()["id"]
    app = _app(alice, pid)

    status = alice.get(f"/api/v1/projects/{pid}/onboarding").json()
    assert _step(status, "application")["state"] == "done"
    assert _step(status, "deploy_mapping")["state"] == "todo"
    assert _step(status, "monitoring")["state"] == "done"  # active ingest token = shadow channel
    assert _step(status, "test_event")["state"] == "todo"

    mapping = alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={"repo_id": rid, "commit_sha": "c" * 40, "deployed_at": utcnow().isoformat()},
    )
    assert mapping.status_code == 201, mapping.text
    status = alice.get(f"/api/v1/projects/{pid}/onboarding").json()
    assert _step(status, "deploy_mapping")["state"] == "done"

    event = alice.post(f"/api/v1/projects/{pid}/onboarding/test-event", json={})
    assert event.status_code == 202, event.text
    result = event.json()
    assert result["anomalies"] >= 1
    assert result["source"] == "owned_staging"
    assert result["executed"] is False

    status = alice.get(f"/api/v1/projects/{pid}/onboarding").json()
    assert _step(status, "test_event")["state"] == "done"
    assert _step(status, "evidence")["state"] == "done"

    with transaction() as conn:
        ev = (
            conn.execute(
                sa.select(monitoring_events.c.doc).where(monitoring_events.c.project_id == pid)
            )
            .scalars()
            .all()
        )
    assert ev, "test event must leave a monitoring event"
    assert ev[0]["simulated"] is False  # real authenticated ingest channel
    assert ev[0]["test_event"] is True  # still unmistakably labelled as a test


def test_test_event_requires_app_and_token(project) -> None:
    alice, pid = project
    denied = alice.post(f"/api/v1/projects/{pid}/onboarding/test-event", json={})
    assert denied.status_code == 409, denied.text

    app = _app(alice, pid, token=False)
    denied = alice.post(f"/api/v1/projects/{pid}/onboarding/test-event", json={})
    assert denied.status_code == 409, denied.text
    assert "ingest token" in denied.json()["error"]["message"]

    minted = alice.post(f"/api/v1/applications/{app['id']}/ingest-tokens")
    assert minted.status_code == 201
    ok = alice.post(f"/api/v1/projects/{pid}/onboarding/test-event", json={})
    assert ok.status_code == 202, ok.text


def test_test_event_picks_tokened_app_among_several(project) -> None:
    alice, pid = project
    _app(alice, pid, token=False)
    tokened = _app(alice, pid)
    event = alice.post(f"/api/v1/projects/{pid}/onboarding/test-event", json={})
    assert event.status_code == 202, event.text
    assert event.json()["application_id"] == tokened["id"]

    _app(alice, pid)  # a second app with a live token makes the choice ambiguous
    denied = alice.post(f"/api/v1/projects/{pid}/onboarding/test-event", json={})
    assert denied.status_code == 422, denied.text
    explicit = alice.post(
        f"/api/v1/projects/{pid}/onboarding/test-event",
        json={"application_id": tokened["id"]},
    )
    assert explicit.status_code == 202, explicit.text


def test_test_event_rate_limit_is_per_project(project) -> None:
    alice, pid = project
    _app(alice, pid)
    for _ in range(20):
        ok = alice.post(f"/api/v1/projects/{pid}/onboarding/test-event", json={})
        assert ok.status_code == 202, ok.text
    limited = alice.post(f"/api/v1/projects/{pid}/onboarding/test-event", json={})
    assert limited.status_code == 429, limited.text


def test_test_event_owner_only(client_factory) -> None:
    alice, viewer = client_factory("alice"), client_factory("vera")
    pid = alice.post("/api/v1/projects", json={"name": "acme"}).json()["id"]
    _app(alice, pid)
    from helpers import add_member

    add_member(alice.me["memberships"][0]["organization"]["id"], viewer.me["user"]["id"], "viewer")
    denied = viewer.post(f"/api/v1/projects/{pid}/onboarding/test-event", json={})
    assert denied.status_code == 403, denied.text


def test_repository_disconnect(project) -> None:
    alice, pid = project
    rid = alice.post(
        f"/api/v1/projects/{pid}/repositories",
        json={"full_name": "acme/payments", "installation_id": None},
    ).json()["id"]
    with transaction() as conn:  # pretend the GitHub App install completed
        conn.execute(
            sa.update(repositories).where(repositories.c.id == rid).values(installation_id=55)
        )
    alice.patch(f"/api/v1/repositories/{rid}", json={"automatic_assessments": True})
    app = _app(alice, pid)
    alice.post(
        f"/api/v1/applications/{app['id']}/deploy-mappings",
        json={"repo_id": rid, "commit_sha": "d" * 40, "deployed_at": utcnow().isoformat()},
    )

    out = alice.post(f"/api/v1/repositories/{rid}/disconnect")
    assert out.status_code == 200, out.text
    assert out.json() == {"id": rid, "disconnected": True}

    # Canonical (security-hardening) semantics: the row is parked, not deleted;
    # the recorded installation stays because revoking the App grant is the
    # customer's GitHub-side action — the API must not claim to do it.
    repo = alice.get(f"/api/v1/repositories/{rid}").json()
    assert repo["installation_id"] == 55
    assert repo["automatic_assessments"] is False

    with transaction() as conn:
        mapping = conn.execute(
            sa.select(deploy_commit_mappings.c.revoked_at).where(
                deploy_commit_mappings.c.repo_id == rid
            )
        ).scalar_one()
        assert mapping is not None

    # Disconnect is idempotent: no second audit event when nothing changes.
    disconnect_audits = sa.func.count().label("n")
    with transaction() as conn:
        audits_before = conn.execute(
            sa.select(disconnect_audits)
            .select_from(audit_events)
            .where(
                audit_events.c.project_id == pid,
                audit_events.c.doc["action"].astext == "repository.disconnected",
            )
        ).scalar_one()
    assert audits_before == 1
    again = alice.post(f"/api/v1/repositories/{rid}/disconnect")
    assert again.status_code == 200
    assert again.json() == {"id": rid, "disconnected": True}
    with transaction() as conn:
        assert (
            conn.execute(
                sa.select(disconnect_audits)
                .select_from(audit_events)
                .where(
                    audit_events.c.project_id == pid,
                    audit_events.c.doc["action"].astext == "repository.disconnected",
                )
            ).scalar_one()
            == audits_before
        )

    # The repo record stays readable; deploy-mapping onboarding step re-opens.
    status = alice.get(f"/api/v1/projects/{pid}/onboarding").json()
    assert _step(status, "deploy_mapping")["state"] == "todo"
    assert _step(status, "repository")["state"] == "done"


def test_repository_disconnect_authz(client_factory) -> None:
    alice, mallory, viewer = (
        client_factory("alice"),
        client_factory("mallory"),
        client_factory("vera"),
    )
    pid = alice.post("/api/v1/projects", json={"name": "acme"}).json()["id"]
    rid = alice.post(
        f"/api/v1/projects/{pid}/repositories", json={"full_name": "acme/payments"}
    ).json()["id"]
    from helpers import add_member

    add_member(alice.me["memberships"][0]["organization"]["id"], viewer.me["user"]["id"], "viewer")
    assert mallory.post(f"/api/v1/repositories/{rid}/disconnect").status_code == 404
    assert viewer.post(f"/api/v1/repositories/{rid}/disconnect").status_code == 403


def test_disconnect_application_blocks_test_event(project) -> None:
    alice, pid = project
    app = _app(alice, pid)
    assert alice.post(f"/api/v1/applications/{app['id']}/disconnect").status_code == 200
    denied = alice.post(f"/api/v1/projects/{pid}/onboarding/test-event", json={})
    assert denied.status_code == 409, denied.text
    status = alice.get(f"/api/v1/projects/{pid}/onboarding").json()
    assert _step(status, "application")["state"] == "todo"
    assert _step(status, "test_event")["state"] == "blocked"
