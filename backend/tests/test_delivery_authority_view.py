"""GET /projects/{id}/delivery-authority reports what delivery does now (read-only)."""

from __future__ import annotations

import sqlalchemy as sa
from helpers import setup_project

from backend.db import memberships, projects, transaction


def _set_mode(client, pid: str, mode: str):
    settings = client.get(f"/api/v1/projects/{pid}").json()["settings"]
    r = client.patch(
        f"/api/v1/projects/{pid}", json={"settings": settings | {"delivery_mode": mode}}
    )
    assert r.status_code == 200, r.text


def _view(client, pid: str) -> dict:
    r = client.get(f"/api/v1/projects/{pid}/delivery-authority")
    assert r.status_code == 200, r.text
    return r.json()


def test_active_pull_request_authority(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    pid = setup_project(alice, bob)["project"]
    _set_mode(alice, pid, "pull_request")
    view = _view(bob, pid)
    assert view["state"] == "active"
    assert view["configured_mode"] == view["effective_mode"] == "pull_request"
    assert view["authorized_by"] == alice.me["user"]["id"]


def test_manual_mode(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    pid = setup_project(alice, bob)["project"]
    _set_mode(alice, pid, "manual")
    assert _view(alice, pid) | {"authorized_by": None} == {
        "configured_mode": "manual",
        "effective_mode": "manual",
        "state": "manual",
        "authorized_by": None,
        "direct_commits_allowed": True,
    }


def test_configured_mode_without_recorded_authorizer_is_inactive(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    pid = setup_project(alice, bob)["project"]
    _set_mode(alice, pid, "pull_request")
    with transaction() as conn:
        doc = conn.execute(sa.select(projects.c.settings).where(projects.c.id == pid)).scalar_one()
        conn.execute(
            sa.update(projects)
            .where(projects.c.id == pid)
            .values(settings=doc | {"delivery_authorized_by": None})
        )
    view = _view(alice, pid)
    assert (view["state"], view["effective_mode"]) == ("inactive", "manual")


def test_authority_of_a_demoted_owner_is_revoked(client_factory) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    pid = setup_project(alice, bob)["project"]
    _set_mode(alice, pid, "pull_request")
    with transaction() as conn:
        conn.execute(
            sa.update(memberships)
            .where(memberships.c.user_id == alice.me["user"]["id"])
            .values(role="viewer")
        )
    view = _view(bob, pid)
    assert (view["state"], view["effective_mode"]) == ("revoked", "manual")


def test_deployment_gate_turns_direct_commits_into_pull_requests(
    client_factory, settings_override
) -> None:
    alice, bob = client_factory("alice"), client_factory("bob")
    pid = setup_project(alice, bob)["project"]
    _set_mode(alice, pid, "auto")
    settings_override(allow_auto_delivery=False)
    view = _view(alice, pid)
    assert (view["state"], view["configured_mode"], view["effective_mode"]) == (
        "active",
        "auto",
        "pull_request",
    )
    assert view["direct_commits_allowed"] is False


def test_non_members_cannot_read_it(client_factory) -> None:
    alice, bob, carol = client_factory("alice"), client_factory("bob"), client_factory("carol")
    pid = setup_project(alice, bob)["project"]
    assert carol.get(f"/api/v1/projects/{pid}/delivery-authority").status_code == 404
