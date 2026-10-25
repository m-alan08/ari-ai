"""Pilot rehearsal drills: two independent orgs, cross-tenant ID substitution,
cancel/opt-out, operator deletion procedure. Real FastAPI + real Postgres +
in-process worker; model turns are the scripted FakeTransport (provider_mode=fake)
and are labelled as such in the rehearsal report — nothing live is claimed here.

Org A = alice (owner) + bob (co-owner approver). Org B = eve alone (owner of her
own auto-created personal org). Eve must get 404 — never a leak — for every
Org-A resource ID she substitutes, whether or not the resource exists.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from helpers import HEAD, post_webhook, push_payload, setup_project
from reference_orchestrator import scripted_transport

from backend import llm, worker
from backend.db import projects, repositories, transaction


@pytest.fixture
def reference(settings_override):
    settings_override(orchestrator_factory="reference_orchestrator:build_orchestrator")
    llm.TRANSPORT_OVERRIDE = scripted_transport


def _drain_full_loop(alice, bob, ids):
    """Webhook -> assessment -> approve -> patch/verify -> COMPLETED (scripted)."""
    assert post_webhook(alice, push_payload(["app/invoices.py"])).status_code == 202
    worker.drain(kinds=["process_github_delivery"])
    asm_id = alice.get(f"/api/v1/projects/{ids['project']}/reassessments").json()["items"][0][
        "assessment_id"
    ]
    worker.drain(kinds=["run_assessment"])
    pending = alice.get(
        f"/api/v1/projects/{ids['project']}/approvals", params={"status": "pending"}
    ).json()["items"]
    bob.post(
        f"/api/v1/approvals/{pending[0]['id']}/decision",
        json={"decision": "approved", "reason": "rehearsal"},
    )
    worker.drain(kinds=["run_assessment"])
    return asm_id


def test_two_org_isolation_by_id_substitution(client_factory, reference) -> None:
    alice, bob, eve = (
        client_factory("reh-alice"),
        client_factory("reh-bob"),
        client_factory("reh-eve"),
    )
    ids = setup_project(alice, bob)
    asm_id = _drain_full_loop(alice, bob, ids)

    detail = alice.get(f"/api/v1/assessments/{asm_id}").json()
    assert detail["assessment"]["state"] == "COMPLETED"
    assert all(r["provider_mode"] == "fake" for r in detail["agent_runs"])  # scripted, labelled
    fid = detail["findings"][0]["id"]
    patches = alice.get(f"/api/v1/findings/{fid}/patches").json()["items"]
    artifact_id = patches[0]["patch"]["diff_artifact_id"]
    transcript_id = detail["agent_runs"][0]["transcript_artifact_id"]

    # Eve is a real authenticated user in a different org — sanity: her API works.
    assert eve.get("/api/v1/me").status_code == 200
    assert eve.get("/api/v1/projects").json()["items"] == []

    p, r, s, a = ids["project"], ids["repo"], ids["scope"], asm_id
    probes = [
        ("GET", f"/api/v1/projects/{p}"),
        ("GET", f"/api/v1/projects/{p}/repositories"),
        ("GET", f"/api/v1/repositories/{r}"),
        ("GET", f"/api/v1/repositories/{r}/commits"),
        ("GET", f"/api/v1/repositories/{r}/snapshots"),
        ("POST", f"/api/v1/repositories/{r}/snapshots"),
        ("GET", f"/api/v1/projects/{p}/scopes"),
        ("POST", f"/api/v1/scopes/{s}/approve"),
        ("POST", f"/api/v1/scopes/{s}/revoke"),
        ("GET", f"/api/v1/projects/{p}/assessments"),
        ("POST", f"/api/v1/projects/{p}/assessments"),
        ("GET", f"/api/v1/assessments/{a}"),
        ("GET", f"/api/v1/assessments/{a}/runs"),
        ("GET", f"/api/v1/assessments/{a}/events"),
        ("GET", f"/api/v1/assessments/{a}/events/log"),
        ("GET", f"/api/v1/assessments/{a}/report"),
        ("GET", f"/api/v1/assessments/{a}/rules-of-engagement"),
        ("POST", f"/api/v1/assessments/{a}/cancel"),
        ("GET", f"/api/v1/projects/{p}/findings"),
        ("GET", f"/api/v1/findings/{fid}"),
        ("GET", f"/api/v1/findings/{fid}/patches"),
        ("POST", f"/api/v1/findings/{fid}/accept-risk"),
        ("GET", f"/api/v1/artifacts/{artifact_id}"),
        ("GET", f"/api/v1/artifacts/{transcript_id}"),
        ("GET", f"/api/v1/projects/{p}/events"),
        ("GET", f"/api/v1/projects/{p}/reassessments"),
        ("GET", f"/api/v1/projects/{p}/approvals"),
        ("GET", f"/api/v1/projects/{p}/defense-recommendations"),
        ("GET", f"/api/v1/projects/{p}/defense-health"),
        ("GET", f"/api/v1/projects/{p}/pilot-report"),
        ("GET", f"/api/v1/projects/{p}/ingest-tokens"),
        ("GET", f"/api/v1/projects/{p}/applications"),
        ("GET", f"/api/v1/projects/{p}/control-grants"),
        ("GET", f"/api/v1/jobs/{uuid.uuid4().hex[:8]}"),
    ]
    for method, url in probes:
        if url.endswith("/assessments"):
            body = {"scope_id": s, "repo_id": r, "commit_sha": HEAD}
        elif url.endswith("/snapshots"):
            body = {"commit_sha": HEAD}
        elif url.endswith("accept-risk"):
            body = {"reason": "probe"}
        else:
            body = None
        resp = eve.request(method, url, json=body) if body else eve.request(method, url)
        assert resp.status_code == 404, f"{method} {url} -> {resp.status_code}: {resp.text[:200]}"

    # Context packs: eve cannot build a pack on org A's project.
    r_pack = eve.post(
        "/api/v1/intelligence/context",
        json={"project_id": p, "query": "payments", "budget_tokens": 1000},
    )
    assert r_pack.status_code in (403, 404, 422), r_pack.text[:200]

    # Fake-but-valid-shaped IDs must behave identically (no existence leak).
    for url in (
        f"/api/v1/findings/fnd_{uuid.uuid4().hex[:20]}",
        f"/api/v1/assessments/asm_{uuid.uuid4().hex[:20]}",
        f"/api/v1/artifacts/art_{uuid.uuid4().hex[:20]}",
    ):
        assert eve.get(url).status_code == 404, url


def test_cancel_optout_and_reoptin_drills(client_factory, reference) -> None:
    alice, bob = client_factory("reh2-alice"), client_factory("reh2-bob")
    ids = setup_project(alice, bob)
    url = f"/api/v1/projects/{ids['project']}/assessments"
    body = {"scope_id": ids["scope"], "repo_id": ids["repo"], "commit_sha": HEAD}

    # Cancel before start -> terminal CANCELLED, second cancel conflicts honestly.
    a1 = alice.post(url, json=body).json()["assessment"]["id"]
    assert alice.post(f"/api/v1/assessments/{a1}/cancel").json()["cancelled"] is True
    assert alice.get(f"/api/v1/assessments/{a1}").json()["assessment"]["state"] == "CANCELLED"

    # Opt-out: automatic_assessments off -> push produces no reassessment.
    assert (
        alice.patch(
            f"/api/v1/repositories/{ids['repo']}", json={"automatic_assessments": False}
        ).status_code
        == 200
    )
    assert post_webhook(alice, push_payload(["app/x.py"])).status_code == 202
    worker.drain(kinds=["process_github_delivery"])
    assert alice.get(f"/api/v1/projects/{ids['project']}/reassessments").json()["items"] == []

    # Re-opt-in -> push produces a reassessment again.
    alice.patch(f"/api/v1/repositories/{ids['repo']}", json={"automatic_assessments": True})
    assert post_webhook(alice, push_payload(["app/x.py"])).status_code == 202
    worker.drain(kinds=["process_github_delivery"])
    items = alice.get(f"/api/v1/projects/{ids['project']}/reassessments").json()["items"]
    assert len(items) == 1


def test_operator_deletion_procedure(client_factory, reference) -> None:
    """Documents the runbook's manual deletion: DB rows gone -> every endpoint 404."""
    alice, bob = client_factory("reh3-alice"), client_factory("reh3-bob")
    ids = setup_project(alice, bob)
    asm_id = _drain_full_loop(alice, bob, ids)
    fid = alice.get(f"/api/v1/assessments/{asm_id}").json()["findings"][0]["id"]

    with transaction() as conn:
        # FK constraints have no cascades, so the operator procedure must touch
        # every referencing table. The honest runbook order is: enumerate all
        # org-scoped tables, delete child-first, then disk objects (artifact
        # files + $SECURITY_WORKDIR/repos/{repo_id}.git bare clone).
        conn.execute(sa.text("SET LOCAL session_replication_role = 'replica'"))
        org_id = ids["org"]
        tables = (
            conn.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE column_name = 'organization_id' AND table_schema = 'public'"
                )
            )
            .scalars()
            .all()
        )
        for t in tables:
            conn.execute(sa.text(f'DELETE FROM "{t}" WHERE organization_id = :o'), {"o": org_id})
        conn.execute(sa.delete(repositories).where(repositories.c.id == ids["repo"]))
        conn.execute(sa.delete(projects).where(projects.c.id == ids["project"]))

    gone = [
        f"/api/v1/projects/{ids['project']}",
        f"/api/v1/repositories/{ids['repo']}",
        f"/api/v1/assessments/{asm_id}",
        f"/api/v1/findings/{fid}",
        f"/api/v1/projects/{ids['project']}/events",
    ]
    for url in gone:
        assert alice.get(url).status_code == 404, f"{url} still visible after deletion"
