"""GET /projects/{id} must authorize every request, not serve a cached row.

Regression: a pre-authorization cache let a project fetched by one org be
returned to non-members and unauthenticated callers.
"""

from __future__ import annotations


def test_owner_gets_project_cold_and_repeated(client_factory) -> None:
    alice = client_factory("alice")
    pid = alice.post("/api/v1/projects", json={"name": "owner-only"}).json()["id"]
    first = alice.get(f"/api/v1/projects/{pid}")
    second = alice.get(f"/api/v1/projects/{pid}")
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["name"] == "owner-only"
    assert second.json() == first.json()


def test_non_member_is_refused_after_owner_fetched_project(client_factory) -> None:
    alice, eve = client_factory("alice"), client_factory("eve")
    pid = alice.post("/api/v1/projects", json={"name": "secret-cache"}).json()["id"]
    assert alice.get(f"/api/v1/projects/{pid}").status_code == 200  # seeds any cache
    r = eve.get(f"/api/v1/projects/{pid}")
    assert r.status_code == 404
    body = r.json()
    assert "secret-cache" not in r.text
    assert "name" not in body


def test_unauthenticated_is_refused_after_owner_fetched_project(client_factory) -> None:
    alice = client_factory("alice")
    pid = alice.post("/api/v1/projects", json={"name": "anon-cache"}).json()["id"]
    assert alice.get(f"/api/v1/projects/{pid}").status_code == 200
    anon = client_factory()
    r = anon.get(f"/api/v1/projects/{pid}")
    assert r.status_code == 401
    assert "anon-cache" not in r.text
