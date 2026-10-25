"""Two-tenant adversarial isolation tests.

Alice+Bob own tenant A (a project with a connected repo, an approved scope, an
assessment, a finding, artifacts, jobs, ingest tokens, an incident and a ready
snapshot). Eve owns a *different* org (tenant B) and knows every tenant-A ID.
Every lookup, mutation, stream, storage accessor and forged claim must fail
closed: 404 for non-members (no existence leak), never a 200, never data.

A test that returns anything but the expected denial is a security regression.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from helpers import HEAD, REPO, add_member, setup_project

from backend import jobs as jobq
from backend.artifacts import ArtifactStore
from backend.db import assessments, findings, snapshots, transaction
from backend.intel_store import PostgresPayloadStore
from backend.intelligence_memory import PostgresMemoryStore
from shared.base import utcnow
from shared.evidence import ArtifactKind
from shared.ids import IdPrefix, new_id
from shared.intelligence import SnapshotRef
from shared.intelligence_memory import ExpectationDecision


def _make_finding(project_id: str, repo_id: str, scope_id: str) -> tuple[str, str]:
    asm, fnd = new_id(IdPrefix.asm), new_id(IdPrefix.fnd)
    now = utcnow()
    with transaction() as conn:
        conn.execute(
            sa.insert(assessments).values(
                id=asm,
                project_id=project_id,
                repo_id=repo_id,
                scope_id=scope_id,
                commit_sha=HEAD,
                trigger="manual",
                state="COMPLETED",
                doc={
                    "id": asm,
                    "project_id": project_id,
                    "commit_sha": HEAD,
                    "repo_id": repo_id,
                    "scope_id": scope_id,
                    "trigger": "manual",
                    "state": "COMPLETED",
                    "budget": {},
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
            )
        )
        conn.execute(
            sa.insert(findings).values(
                id=fnd,
                project_id=project_id,
                assessment_id=asm,
                kind="security",
                status="open",
                paths=[],
                doc={
                    "id": fnd,
                    "assessment_id": asm,
                    "commit_sha": HEAD,
                    "kind": "security",
                    "title": "SQL injection in payments handler",
                    "status": "validated",
                    "confidence": 0.9,
                    "evidence": [
                        {
                            "kind": "source_span",
                            "path": "src/payments.py",
                            "commit_sha": HEAD,
                            "span": {"start_line": 10, "end_line": 12},
                            "provenance": "agent:scout@test",
                        }
                    ],
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
            )
        )
    return asm, fnd


def _make_snapshot(repo_id: str) -> str:
    sid = new_id(IdPrefix.snp)
    now = utcnow()
    with transaction() as conn:
        conn.execute(
            sa.insert(snapshots).values(
                id=sid,
                repo_id=repo_id,
                commit_sha="c" * 40,
                index_version="test",
                status="ready",
                doc={
                    "id": sid,
                    "repo_id": repo_id,
                    "commit_sha": "c" * 40,
                    "status": "ready",
                    "index_version": "test",
                    "created_at": now.isoformat(),
                },
                created_at=now,
            )
        )
    return sid


def _make_job(project_id: str) -> str:
    with transaction() as conn:
        job_id, _ = jobq.enqueue(
            conn,
            kind="index_snapshot",
            payload={"project_id": project_id},
        )
        jobq.fail(
            conn,
            job_id,
            owner="w1",
            error={"code": "X", "message": "m", "trace": "SECRET-TRACE"},
            retryable=False,
        )
    return job_id


@pytest.fixture
def two_tenants(client_factory):
    """Tenant A (alice/bob) fully provisioned; tenant B (eve) is a separate org."""
    alice, bob, eve = client_factory("alice"), client_factory("bob"), client_factory("eve")
    ids = setup_project(alice, bob)  # scope approved + active
    eve.post("/api/v1/projects", json={"name": "eve-tenant"})
    ids["asm"], ids["fnd"] = _make_finding(ids["project"], ids["repo"], ids["scope"])
    ids["snp"] = _make_snapshot(ids["repo"])
    ids["job"] = _make_job(ids["project"])
    ids["alice"], ids["bob"], ids["eve"] = alice, bob, eve
    return ids


# ------------------------------------------------------------------
# 1. Resource-ID guessing across every project-scoped read surface.
# ------------------------------------------------------------------


def test_outsider_cannot_read_any_project_surface(two_tenants) -> None:
    eve = two_tenants["eve"]
    pid, rid, sid = two_tenants["project"], two_tenants["repo"], two_tenants["scope"]
    asm, fnd, snp = two_tenants["asm"], two_tenants["fnd"], two_tenants["snp"]
    for path in (
        f"/api/v1/projects/{pid}",
        f"/api/v1/projects/{pid}/repositories",
        f"/api/v1/projects/{pid}/scopes",
        f"/api/v1/projects/{pid}/assessments",
        f"/api/v1/projects/{pid}/findings",
        f"/api/v1/projects/{pid}/approvals",
        f"/api/v1/projects/{pid}/reassessments",
        f"/api/v1/projects/{pid}/ingest-tokens",
        f"/api/v1/projects/{pid}/incidents",
        f"/api/v1/projects/{pid}/events",
        f"/api/v1/repositories/{rid}",
        f"/api/v1/repositories/{rid}/commits",
        f"/api/v1/repositories/{rid}/snapshots",
        f"/api/v1/scopes/{sid}",
        f"/api/v1/assessments/{asm}",
        f"/api/v1/assessments/{asm}/report",
        f"/api/v1/assessments/{asm}/runs",
        f"/api/v1/assessments/{asm}/events",
        f"/api/v1/assessments/{asm}/events/log",
        f"/api/v1/assessments/{asm}/rules-of-engagement",
        f"/api/v1/findings/{fnd}",
        f"/api/v1/findings/{fnd}/patches",
        f"/api/v1/snapshots/{snp}",
        f"/api/v1/snapshots/{snp}/expectations",
        f"/api/v1/snapshots/{snp}/regressions",
        f"/api/v1/snapshots/{snp}/symbols",
        f"/api/v1/jobs/{two_tenants['job']}",
    ):
        r = eve.get(path)
        assert r.status_code == 404, f"{path} -> {r.status_code} {r.text[:200]}"
        assert r.json()["error"]["code"] == "NOT_FOUND", path


def test_outsider_cannot_mutate_any_project_surface(two_tenants) -> None:
    eve = two_tenants["eve"]
    pid, rid, sid = two_tenants["project"], two_tenants["repo"], two_tenants["scope"]
    asm, fnd = two_tenants["asm"], two_tenants["fnd"]
    assert eve.patch(f"/api/v1/projects/{pid}", json={"name": "hijack"}).status_code == 404
    assert (
        eve.patch(f"/api/v1/repositories/{rid}", json={"automatic_assessments": False}).status_code
        == 404
    )
    assert (
        eve.post(
            f"/api/v1/projects/{pid}/assessments",
            json={"scope_id": sid, "repo_id": rid, "commit_sha": HEAD},
        ).status_code
        == 404
    )
    assert eve.post(f"/api/v1/assessments/{asm}/cancel").status_code == 404
    assert (
        eve.post(
            f"/api/v1/findings/{fnd}/accept-risk", json={"reason": "not my problem"}
        ).status_code
        == 404
    )
    assert eve.post(f"/api/v1/scopes/{sid}/approve").status_code == 404
    assert eve.post(f"/api/v1/scopes/{sid}/revoke").status_code == 404
    assert eve.post(f"/api/v1/projects/{pid}/ingest-tokens").status_code == 404
    assert eve.post(f"/api/v1/projects/{pid}/incidents", json={"title": "x"}).status_code == 404


def test_outsider_gets_no_sse_stream(two_tenants) -> None:
    """SSE replays durable assessment_events; outsiders must never attach."""
    eve = two_tenants["eve"]
    r = eve.get(
        f"/api/v1/assessments/{two_tenants['asm']}/events", headers={"accept": "text/event-stream"}
    )
    assert r.status_code == 404


def test_artifact_id_guess_grants_nothing(two_tenants) -> None:
    alice, eve = two_tenants["alice"], two_tenants["eve"]
    with transaction() as conn:
        ref = ArtifactStore().put_text(
            conn, "private source diff", kind=ArtifactKind.diff, project_id=two_tenants["project"]
        )
    r = eve.get(f"/api/v1/artifacts/{ref.id}")
    assert r.status_code == 404
    assert alice.get(f"/api/v1/artifacts/{ref.id}").status_code == 200


def test_projectless_artifacts_are_unreadable_by_anyone(two_tenants) -> None:
    """Webhook payload artifacts have project_id NULL — no role must reach them."""
    alice, eve = two_tenants["alice"], two_tenants["eve"]
    with transaction() as conn:
        ref = ArtifactStore().put_text(
            conn, "raw webhook body", kind=ArtifactKind.log, project_id=None
        )
    assert eve.get(f"/api/v1/artifacts/{ref.id}").status_code == 404
    assert alice.get(f"/api/v1/artifacts/{ref.id}").status_code == 404


def test_job_lookup_never_leaks_trace_even_to_owner(two_tenants) -> None:
    alice, eve = two_tenants["alice"], two_tenants["eve"]
    job = two_tenants["job"]
    assert eve.get(f"/api/v1/jobs/{job}").status_code == 404
    r = alice.get(f"/api/v1/jobs/{job}")
    assert r.status_code == 200 and "SECRET-TRACE" not in r.text


def test_audit_log_denied_to_outsider(two_tenants) -> None:
    eve = two_tenants["eve"]
    assert (
        eve.get("/api/v1/audit", params={"project_id": two_tenants["project"]}).status_code == 404
    )


# ------------------------------------------------------------------
# 2. Forged claims: outsider supplies tenant-A IDs inside her own org.
# ------------------------------------------------------------------


def test_scope_cannot_borrow_foreign_repo(two_tenants) -> None:
    """Eve's own project cannot scope a target against Alice's repo."""
    eve = two_tenants["eve"]
    eve_pid = eve.get("/api/v1/projects").json()["items"][0]["id"]
    from datetime import UTC, datetime, timedelta

    r = eve.post(
        f"/api/v1/projects/{eve_pid}/scopes",
        json={
            "name": "borrow",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "targets": [
                {"kind": "sandbox_build", "repo_id": two_tenants["repo"], "build_recipe_ref": "r"}
            ],
        },
    )
    assert r.status_code in (403, 404)
    if r.status_code == 403:
        assert r.json()["error"]["code"] == "SCOPE_DENIED"


def test_cannot_connect_repo_without_recorded_access(two_tenants) -> None:
    """Eve never had access to Alice's private repo; connect must be denied."""
    eve = two_tenants["eve"]
    eve_pid = eve.get("/api/v1/projects").json()["items"][0]["id"]
    r = eve.post(
        f"/api/v1/projects/{eve_pid}/repositories",
        json={"full_name": REPO, "installation_id": None},
    )
    assert r.status_code in (403, 404, 409, 422)
    # installation_id=None registers a manual repo (never fetched — no GitHub App
    # token exists for it) so it is legitimately allowed; the real boundary is a
    # claimed installation_id Eve has no access row for.
    r = eve.post(
        f"/api/v1/projects/{eve_pid}/repositories",
        json={"full_name": "acme/never-granted", "installation_id": 987654321},
    )
    assert r.status_code == 403


def test_ingest_token_is_bound_to_its_project(two_tenants) -> None:
    """A bearer token minted for tenant B cannot write into tenant A's stream."""
    eve = two_tenants["eve"]
    eve_pid = eve.get("/api/v1/projects").json()["items"][0]["id"]
    token = eve.post(f"/api/v1/projects/{eve_pid}/ingest-tokens").json()["token"]
    r = eve.post(
        f"/api/v1/ingest/runtime/{two_tenants['project']}",
        content=b'{"route":"/","signals":[]}',
        headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
    )
    assert r.status_code in (401, 403, 404)


def test_context_pack_requires_exact_matching_snapshot(two_tenants) -> None:
    """Forging project_id inside a SnapshotRef must not unlock the context pack."""
    eve = two_tenants["eve"]
    r = eve.post(
        "/api/v1/intelligence/context",
        json={
            "snapshot_ref": {
                "project_id": two_tenants["project"],
                "repo_id": two_tenants["repo"],
                "commit_sha": "c" * 40,
                "snapshot_id": two_tenants["snp"],
                "index_version": "test",
            },
            "purpose": "scout",
            "focus": ["auth"],
            "token_budget": 4096,
        },
    )
    assert r.status_code in (403, 404)


# ------------------------------------------------------------------
# 3. Role boundary inside one tenant: viewer reads, never mutates.
# ------------------------------------------------------------------


def test_viewer_role_boundary(two_tenants, client_factory) -> None:
    carol = client_factory("carol")
    add_member(two_tenants["org"], carol.me["user"]["id"], "viewer")
    pid, rid, sid = two_tenants["project"], two_tenants["repo"], two_tenants["scope"]
    assert carol.get(f"/api/v1/projects/{pid}").status_code == 200
    assert carol.get(f"/api/v1/assessments/{two_tenants['asm']}").status_code == 200
    assert carol.patch(f"/api/v1/projects/{pid}", json={"name": "x"}).status_code == 403
    assert (
        carol.patch(
            f"/api/v1/repositories/{rid}", json={"automatic_assessments": False}
        ).status_code
        == 403
    )
    assert carol.post(f"/api/v1/scopes/{sid}/revoke").status_code == 403
    assert carol.post(f"/api/v1/projects/{pid}/ingest-tokens").status_code == 403
    assert carol.get("/api/v1/audit", params={"project_id": pid}).status_code == 403


# ------------------------------------------------------------------
# 4. Storage-accessor layer: forged SnapshotRef / decision must fail.
# ------------------------------------------------------------------


def test_payload_store_rejects_forged_ref(two_tenants) -> None:
    store = PostgresPayloadStore()
    forged = SnapshotRef(
        project_id=two_tenants["project"],
        repo_id=two_tenants["repo"],
        commit_sha="c" * 40,
        snapshot_id=two_tenants["snp"],
        index_version="test",
    )
    # A ref mixing Eve's *own* project id with Alice's repo must be rejected.
    eve_pid = two_tenants["eve"].get("/api/v1/projects").json()["items"][0]["id"]
    mixed = SnapshotRef(
        project_id=eve_pid,
        repo_id=two_tenants["repo"],
        commit_sha="c" * 40,
        snapshot_id=two_tenants["snp"],
        index_version="test",
    )
    with pytest.raises(PermissionError):
        store.put_payload(mixed, "file", "k", b"x")
    with pytest.raises(PermissionError):
        store.get_payload(mixed, "file", "k")
    # The correctly-matching ref works — accessors are strict, not blanket-deny.
    from shared.intelligence import SnapshotStats

    store.put_payload(forged, "file", "k", b"x")
    store.mark_ready(forged, SnapshotStats(files=1))
    assert store.get_payload(forged, "file", "k") == b"x"


def test_memory_store_decision_requires_membership(two_tenants) -> None:
    eve = two_tenants["eve"]
    ref = SnapshotRef(
        project_id=two_tenants["project"],
        repo_id=two_tenants["repo"],
        commit_sha="c" * 40,
        snapshot_id=two_tenants["snp"],
        index_version="test",
    )
    decision = ExpectationDecision(
        snapshot_ref=ref,
        expectation_id=new_id(IdPrefix.exp),
        status="confirmed",
        reason="approved behavior",
        expected_revision=0,
        revision=1,
        decided_by=eve.me["user"]["id"],
        decided_at=utcnow(),
    )
    with pytest.raises(PermissionError):
        PostgresMemoryStore().put_decision(decision)
    # Reading another tenant's memory with a forged ref also fails.
    with pytest.raises(PermissionError):
        PostgresMemoryStore().decisions(
            ref.model_copy(
                update={"project_id": eve.get("/api/v1/projects").json()["items"][0]["id"]}
            )
        )


# ------------------------------------------------------------------
# 5. Row-level security: the private-source tables read empty without a
#    tenant GUC, verified under a non-superuser probe role per tenant.
# ------------------------------------------------------------------


def _probe_role(conn) -> None:
    if not conn.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = 'ari_probe'")).scalar():
        conn.execute(sa.text("CREATE ROLE ari_probe NOLOGIN"))
    conn.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON index_payloads TO ari_probe"))
    conn.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON index_snapshots TO ari_probe"))


def test_rls_blocks_reads_without_tenant_context(two_tenants) -> None:
    from backend.db import get_engine
    from shared.intelligence import SnapshotStats

    alice_pid = two_tenants["project"]
    eve_pid = two_tenants["eve"].get("/api/v1/projects").json()["items"][0]["id"]
    ref = SnapshotRef(
        project_id=alice_pid,
        repo_id=two_tenants["repo"],
        commit_sha="c" * 40,
        snapshot_id=two_tenants["snp"],
        index_version="test",
    )
    store = PostgresPayloadStore()
    store.put_payload(ref, "file", "secret.py", b"source-code-bytes")
    store.mark_ready(ref, SnapshotStats(files=1))

    with get_engine().begin() as conn:
        _probe_role(conn)
    with get_engine().begin() as conn:
        try:
            # ari_probe is not a superuser and does not own the tables: RLS applies.
            conn.execute(sa.text("SET ROLE ari_probe"))
            count = sa.text("SELECT count(*) FROM index_payloads")
            # No tenant GUC -> the table reads as empty even though data exists.
            assert conn.execute(count).scalar() == 0
            # Tenant B's identity -> still cannot see tenant A's source bytes.
            conn.execute(sa.text("SELECT set_config('ari.project_id', :p, true)"), {"p": eve_pid})
            assert conn.execute(count).scalar() == 0
            # Tenant A's identity -> sees only its own rows.
            conn.execute(sa.text("SELECT set_config('ari.project_id', :p, true)"), {"p": alice_pid})
            assert conn.execute(count).scalar() == 1
            # Writes under the wrong tenant are rejected by WITH CHECK.
            with pytest.raises(sa.exc.DBAPIError), conn.begin_nested():
                conn.execute(
                    sa.text(
                        "INSERT INTO index_payloads (project_id, repo_id, commit_sha,"
                        " index_version, snapshot_id, kind, key, sha256, size_bytes, data)"
                        " VALUES (:other, :rid, :sha, 'test', :sid, 'file', 'x', 'a', 1, '\\x00')"
                    ),
                    {
                        "other": eve_pid,
                        "rid": two_tenants["repo"],
                        "sha": "c" * 40,
                        "sid": two_tenants["snp"],
                    },
                )
        finally:
            conn.execute(sa.text("RESET ROLE"))


def test_store_layer_sets_tenant_context_itself(two_tenants) -> None:
    """RLS must not regress normal operation: the store sets the GUC per txn."""
    ref = SnapshotRef(
        project_id=two_tenants["project"],
        repo_id=two_tenants["repo"],
        commit_sha="d" * 40,
        snapshot_id=new_id(IdPrefix.snp),
        index_version="test",
    )
    from shared.intelligence import SnapshotStats

    store = PostgresPayloadStore()
    store.put_payload(ref, "file", "a.py", b"x")
    store.mark_ready(ref, SnapshotStats(files=1))
    assert store.get_payload(ref, "file", "a.py") == b"x"
    found = store.find_snapshot(two_tenants["project"], two_tenants["repo"], "d" * 40, "test")
    assert found.snapshot_id == ref.snapshot_id


# ------------------------------------------------------------------
# 6. Retention/deletion: the documented purge removes every trace.
# ------------------------------------------------------------------


def test_purge_project_deletes_every_surface(two_tenants, tmp_path) -> None:
    from backend.db import get_engine
    from backend.privacy import purge_project
    from shared.intelligence import SnapshotStats

    alice, eve = two_tenants["alice"], two_tenants["eve"]
    pid = two_tenants["project"]
    ref = SnapshotRef(
        project_id=pid,
        repo_id=two_tenants["repo"],
        commit_sha="c" * 40,
        snapshot_id=two_tenants["snp"],
        index_version="test",
    )
    store = PostgresPayloadStore()
    store.put_payload(ref, "file", "s.py", b"secret-source")
    store.mark_ready(ref, SnapshotStats(files=1))
    with transaction() as conn:
        art = ArtifactStore().put_text(
            conn, "log with sk-ant-SECRETKEY", kind=ArtifactKind.log, project_id=pid
        )
        sealed = ArtifactStore().put_bytes(
            conn,
            b"sealed diff bytes",
            kind=ArtifactKind.sealed_diff,
            project_id=pid,
            media_type="text/x-diff",
            redacted=False,
        )
        sealed_path = ArtifactStore()._path(sealed.sha256)
    workdir = tmp_path / "wd"
    clone = workdir / "repos" / f"{two_tenants['repo']}.git"
    clone.mkdir(parents=True)

    counts = purge_project(get_engine(), pid, workdir=workdir)
    assert counts["projects"] == 1 and counts["index_payloads"] >= 1

    # every API surface now denies even the owner — the project is gone
    for path in (
        f"/api/v1/projects/{pid}",
        f"/api/v1/findings/{two_tenants['fnd']}",
        f"/api/v1/artifacts/{art.id}",
        f"/api/v1/artifacts/{sealed.id}",
        f"/api/v1/jobs/{two_tenants['job']}",
    ):
        assert alice.get(path).status_code == 404, path
    with pytest.raises(PermissionError):
        store.get_payload(ref, "file", "s.py")
    assert not clone.exists()
    assert not sealed_path.exists()
    # tenant B's data is untouched
    assert len(eve.get("/api/v1/projects").json()["items"]) == 1
