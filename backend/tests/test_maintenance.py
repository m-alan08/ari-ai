"""Storage maintenance: bounded retention, ops endpoint, and the storage report.

Runs against the real test PostgreSQL — retention deletes are exercised end-to-end,
including RLS-scoped index_payloads/index_snapshots pruning.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from helpers import setup_project

from backend import jobs, maintenance
from backend.db import (
    agent_runs,
    assessment_events,
    assessments,
    idempotency_keys,
    index_payloads,
    index_snapshots,
    monitoring_events,
    reassessment_requests,
    runtime_windows,
    sessions,
    transaction,
    webhook_deliveries,
)
from backend.intel_store import PostgresPayloadStore
from backend.settings import get_settings
from shared.ids import IdPrefix, new_id
from shared.intelligence import SnapshotRef, SnapshotStats

OLD = datetime.now(UTC) - timedelta(days=400)


def _settings(**overrides):
    # Tests exercise real deletes; production defaults to dry-run until an operator flips it.
    overrides.setdefault("retention_dry_run", False)
    return get_settings().model_copy(update=overrides)


def _ids(client_factory) -> dict[str, str]:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    ids["alice_uid"] = alice.me["user"]["id"]
    return ids


def _ready_payloads(ids: dict[str, str], sha: str) -> SnapshotRef:
    """A ready index snapshot with two payloads, via the real store path."""
    ref = SnapshotRef(
        project_id=ids["project"],
        repo_id=ids["repo"],
        commit_sha=sha,
        snapshot_id=new_id(IdPrefix.snp),
        index_version="intel-0.1",
    )
    store = PostgresPayloadStore()
    store.put_payload(ref, "intelligence_index", "index.json", b'{"symbols":[]}')
    store.put_payload(ref, "files", "app/main.py", b"{}")
    store.mark_ready(ref, SnapshotStats(files=1, symbols=0))
    return ref


def _age_scoped(table: sa.Table, project_id: str, where, when: datetime = OLD) -> None:
    with transaction() as conn:
        maintenance._scope_tenant(conn, project_id)
        conn.execute(sa.update(table).where(where).values(created_at=when))


def test_expired_sessions_and_idempotency_pruned(client_factory) -> None:
    ids = _ids(client_factory)
    with transaction() as conn:
        conn.execute(
            sa.insert(sessions).values(
                token_hash="dead" * 16,
                user_id=ids["alice_uid"],
                csrf_token="x" * 64,
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        conn.execute(
            sa.insert(sessions).values(
                token_hash="live" * 16,
                user_id=ids["alice_uid"],
                csrf_token="y" * 64,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        for key, when in (("old", OLD), ("new", datetime.now(UTC))):
            conn.execute(
                sa.insert(idempotency_keys).values(
                    user_id=ids["alice_uid"],
                    key=key,
                    request_hash="h" * 64,
                    response_status=200,
                    response_body={},
                    created_at=when,
                )
            )
    out = maintenance.run_retention(s=_settings())
    assert out["deleted"]["expired_sessions"] == 1
    assert out["deleted"]["idempotency_keys"] == 1
    with transaction() as conn:
        # No expired session survives; live ones are untouched.
        assert (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(sessions)
                .where(sessions.c.expires_at < sa.func.now())
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(sessions)
                .where(sessions.c.token_hash == "live" * 16)
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(sa.select(sa.func.count()).select_from(idempotency_keys)).scalar_one() == 1
        )


def test_terminal_jobs_pruned_active_untouched(client_factory) -> None:
    _ids(client_factory)
    with transaction() as conn:
        for status, when in (
            ("succeeded", OLD),  # pruned
            ("failed", OLD),  # pruned
            ("dead", OLD),  # pruned
            ("succeeded", datetime.now(UTC)),  # fresh terminal: kept
            ("queued", OLD),  # old but non-terminal: kept
        ):
            jid, _ = jobs.enqueue(conn, "index_snapshot", {"n": 1})
            conn.execute(
                sa.update(jobs.jobs)
                .where(jobs.jobs.c.id == jid)
                .values(status=status, updated_at=when)
            )
    out = maintenance.run_retention(s=_settings())
    assert out["deleted"]["terminal_jobs"] == 3
    with transaction() as conn:
        left = dict(
            conn.execute(
                sa.select(jobs.jobs.c.status, sa.func.count()).group_by(jobs.jobs.c.status)
            ).all()
        )
    assert left == {"succeeded": 1, "queued": 1}


def test_history_tables_pruned(client_factory) -> None:
    ids = _ids(client_factory)
    with transaction() as conn:
        for when in (OLD, datetime.now(UTC)):
            conn.execute(
                sa.insert(webhook_deliveries).values(
                    delivery_id=new_id(IdPrefix.evt) + str(when.timestamp()),
                    source="github",
                    event_name="push",
                    payload_sha256="a" * 64,
                    received_at=when,
                )
            )
            conn.execute(
                sa.insert(monitoring_events).values(
                    id=new_id(IdPrefix.evt),
                    project_id=ids["project"],
                    repo_id=ids["repo"],
                    dedupe_key=new_id(IdPrefix.evt),
                    status="processed",
                    doc={},
                    created_at=when,
                )
            )
            conn.execute(
                sa.insert(reassessment_requests).values(
                    id=new_id(IdPrefix.evt),
                    project_id=ids["project"],
                    repo_id=ids["repo"],
                    status="skipped",
                    doc={},
                    created_at=when,
                )
            )
            conn.execute(
                sa.insert(runtime_windows).values(
                    id=new_id(IdPrefix.evt),
                    project_id=ids["project"],
                    source="api",
                    route="/x" + str(when.timestamp()),
                    window_start=when - timedelta(minutes=5),
                    window_end=when,
                    counters={},
                    score={},
                    created_at=when,
                )
            )
    out = maintenance.run_retention(s=_settings())
    assert out["deleted"]["webhook_deliveries"] == 1
    assert out["deleted"]["monitoring_events"] == 1
    assert out["deleted"]["reassessment_requests"] == 1
    assert out["deleted"]["runtime_windows"] == 1


def test_orphan_payloads_pruned_ready_untouched(client_factory) -> None:
    ids = _ids(client_factory)
    store = PostgresPayloadStore()
    # Orphan: payloads written, never mark_ready'd, aged past the orphan grace.
    orphan = SnapshotRef(
        project_id=ids["project"],
        repo_id=ids["repo"],
        commit_sha="a" * 40,
        snapshot_id=new_id(IdPrefix.snp),
        index_version="intel-0.1",
    )
    store.put_payload(orphan, "intelligence_index", "index.json", b"orphan")
    _age_scoped(index_payloads, ids["project"], index_payloads.c.snapshot_id == orphan.snapshot_id)
    # Ready: normal snapshot must survive.
    ready = _ready_payloads(ids, "b" * 40)
    out = maintenance.run_retention(s=_settings(retention_orphan_grace_hours=24))
    per_project = out["deleted"]["index_payloads"][ids["project"]]
    assert per_project["payload_rows"] == 1
    assert per_project["orphan_snapshots"] == 1
    with transaction() as conn:
        maintenance._scope_tenant(conn, ids["project"])
        survivors = set(conn.execute(sa.select(index_payloads.c.snapshot_id)).scalars())
    assert survivors == {ready.snapshot_id}
    # Ready snapshot still resolves.
    assert store.find_snapshot(ids["project"], ids["repo"], "b" * 40, "intel-0.1") is not None


def test_superseded_ready_snapshots_pruned_with_audit(client_factory) -> None:
    ids = _ids(client_factory)
    store = PostgresPayloadStore()
    refs = [_ready_payloads(ids, f"{i:040x}") for i in range(8)]
    # Stagger created_at (all past the grace window) so "newest N kept" is deterministic.
    _age_scoped(index_payloads, ids["project"], sa.true())
    for i, ref in enumerate(refs):
        _age_scoped(
            index_snapshots,
            ids["project"],
            index_snapshots.c.snapshot_id == ref.snapshot_id,
            when=OLD + timedelta(minutes=i),
        )
    s = _settings(retention_snapshots_per_repo=5, retention_snapshot_grace_days=7)
    out = maintenance.run_retention(s=s)
    per_project = out["deleted"]["index_payloads"][ids["project"]]
    assert per_project["superseded_snapshots"] == 3
    assert per_project["payload_rows"] == 6  # 2 payloads per snapshot
    with transaction() as conn:
        maintenance._scope_tenant(conn, ids["project"])
        left = conn.execute(sa.select(index_snapshots.c.snapshot_id)).scalars().all()
    assert sorted(left) == sorted(r.snapshot_id for r in refs[3:])  # newest 5 kept
    # Pruned snapshots no longer resolve.
    with pytest.raises(KeyError):
        store.find_snapshot(ids["project"], ids["repo"], refs[0].commit_sha, "intel-0.1")
    # An audit row was appended for the prune.
    with transaction() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT doc->>'action' FROM audit_events WHERE doc->>'action' = 'maintenance.retention'"
            )
        ).all()
    assert rows


def test_active_assessment_protects_snapshot(client_factory) -> None:
    ids = _ids(client_factory)
    refs = [_ready_payloads(ids, f"{i:040x}") for i in range(7)]
    _age_scoped(index_payloads, ids["project"], sa.true())
    for i, ref in enumerate(refs):
        _age_scoped(
            index_snapshots,
            ids["project"],
            index_snapshots.c.snapshot_id == ref.snapshot_id,
            when=OLD + timedelta(minutes=i),
        )
    protected = refs[0].snapshot_id  # oldest → always a prune candidate unless protected
    with transaction() as conn:
        conn.execute(
            sa.insert(assessments).values(
                id=new_id(IdPrefix.asm),
                project_id=ids["project"],
                repo_id=ids["repo"],
                scope_id=ids["scope"],
                commit_sha=refs[0].commit_sha,
                trigger="api",
                state="CREATED",
                doc={"snapshot_id": protected},
            )
        )
    s = _settings(retention_snapshots_per_repo=1, retention_snapshot_grace_days=7)
    out = maintenance.run_retention(s=s)
    per_project = out["deleted"]["index_payloads"][ids["project"]]
    assert per_project["superseded_snapshots"] == 5  # 7 - 1 keep - 1 protected
    with transaction() as conn:
        maintenance._scope_tenant(conn, ids["project"])
        left = set(conn.execute(sa.select(index_snapshots.c.snapshot_id)).scalars())
    assert protected in left


def test_dry_run_deletes_nothing(client_factory) -> None:
    ids = _ids(client_factory)
    orphan = SnapshotRef(
        project_id=ids["project"],
        repo_id=ids["repo"],
        commit_sha="c" * 40,
        snapshot_id=new_id(IdPrefix.snp),
        index_version="intel-0.1",
    )
    PostgresPayloadStore().put_payload(orphan, "intelligence_index", "i.json", b"x")
    _age_scoped(index_payloads, ids["project"], sa.true())
    with transaction() as conn:
        jid, _ = jobs.enqueue(conn, "index_snapshot", {})
        conn.execute(
            sa.update(jobs.jobs).where(jobs.jobs.c.id == jid).values(status="dead", updated_at=OLD)
        )
    out = maintenance.run_retention(s=_settings(), dry_run=True)
    assert out["dry_run"] is True
    assert out["deleted"]["terminal_jobs"] == 1
    assert out["deleted"]["index_payloads"][ids["project"]]["payload_rows"] == 1
    with transaction() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(jobs.jobs)).scalar_one() == 1
        maintenance._scope_tenant(conn, ids["project"])
        assert (
            conn.execute(sa.select(sa.func.count()).select_from(index_payloads)).scalar_one() == 1
        )


def test_artifact_orphan_gc(client_factory) -> None:
    _ids(client_factory)
    root = maintenance._artifact_root(get_settings())
    assert root is not None
    sha = "e" * 64
    p = root / "sha256" / sha[:2] / sha
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"orphan")
    old = datetime.now(UTC) - timedelta(hours=72)
    os.utime(p, (old.timestamp(), old.timestamp()))
    with transaction() as conn:
        out = maintenance._gc_artifact_orphans(conn, _settings(), dry_run=False)
    assert out == {"artifact_orphan_files": 1, "artifact_orphan_bytes": 6}
    assert not p.exists()


def test_storage_report_smoke(client_factory) -> None:
    ids = _ids(client_factory)
    _ready_payloads(ids, "f" * 40)
    report = maintenance.storage_report(s=_settings(postgres_volume_bytes=1_000_000))
    assert report["database"]["size_bytes"] > 0
    assert report["volume"]["pct_used"] is not None
    assert report["volume"]["pct_used"] > 70  # tiny configured volume → all alerts
    assert len(report["alerts"]) == 3
    names = {t["table"] for t in report["tables"]}
    assert {"jobs", "index_payloads", "assessments"} <= names
    kinds = {p["kind"] for p in report["payloads_by_kind"]}
    assert kinds == {"intelligence_index", "files"}
    assert "reclaimable" in report


def test_ops_endpoint(client_factory, settings_override) -> None:
    client = client_factory()
    # Disabled surface: 404 whether or not an auth header is sent.
    assert client.get("/api/v1/ops/storage").status_code == 404
    assert (
        client.get("/api/v1/ops/storage", headers={"Authorization": "Bearer x"}).status_code == 404
    )
    settings_override(OPS_API_TOKEN="test-ops-token")
    assert client.get("/api/v1/ops/storage").status_code == 401
    assert (
        client.get("/api/v1/ops/storage", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    ok = client.get("/api/v1/ops/storage", headers={"Authorization": "Bearer test-ops-token"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["database"]["size_bytes"] > 0
    prom = client.get(
        "/api/v1/ops/storage/prometheus", headers={"Authorization": "Bearer test-ops-token"}
    )
    assert prom.status_code == 200
    assert "ari_postgres_bytes" in prom.text
    dry = client.post(
        "/api/v1/ops/maintenance",
        headers={"Authorization": "Bearer test-ops-token"},
        json={"dry_run": True},
    )
    assert dry.status_code == 200
    assert dry.json()["dry_run"] is True


def test_assessment_detail_disabled_by_default(client_factory) -> None:
    ids = _ids(client_factory)
    with transaction() as conn:
        aid = new_id(IdPrefix.asm)
        conn.execute(
            sa.insert(assessments).values(
                id=aid,
                project_id=ids["project"],
                repo_id=ids["repo"],
                scope_id=ids["scope"],
                commit_sha="a" * 40,
                trigger="api",
                state="COMPLETED",
                doc={},
            )
        )
        conn.execute(
            sa.insert(assessment_events).values(
                assessment_id=aid, seq=1, type="state_changed", doc={}, created_at=OLD
            )
        )
        conn.execute(
            sa.insert(agent_runs).values(
                id=new_id(IdPrefix.evt),
                assessment_id=aid,
                agent="scout",
                status="done",
                doc={},
                created_at=OLD,
            )
        )
    # Default: disabled — nothing pruned.
    out = maintenance.run_retention(s=_settings())
    assert "assessment_events" not in out["deleted"]
    # Opt-in prunes only terminal assessments' detail.
    out = maintenance.run_retention(s=_settings(retention_assessment_detail_days=90))
    assert out["deleted"]["assessment_events"] == 1
    assert out["deleted"]["agent_runs"] == 1


def test_checkout_ref_gc(client_factory, tmp_path) -> None:
    _ids(client_factory)
    bare = tmp_path / "repos" / "r.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(work),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "c",
            "--allow-empty",
        ],
        check=True,
    )
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    refs = []
    for i in range(8):
        env = dict(os.environ, GIT_COMMITTER_DATE=f"2026-01-0{i + 1}T00:00:00Z")
        subprocess.run(
            [
                "git",
                "-C",
                str(work),
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                f"c{i}",
                "--allow-empty",
            ],
            check=True,
            env=env,
        )
        sha = subprocess.run(
            ["git", "-C", str(work), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(work), "push", "-q", str(bare), f"{sha}:refs/ari/{sha}"], check=True
        )
        refs.append(sha)
    s = _settings(security_workdir=tmp_path, retention_checkout_refs_keep=5)
    out = maintenance._gc_checkout_refs(s, dry_run=False)
    info = out["checkouts"]["r.git"]
    assert info["refs"] == 8 and info["prunable_refs"] == 3
    left = subprocess.run(
        ["git", "-C", str(bare), "for-each-ref", "--format=%(refname)", "refs/ari/"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert len(left) == 5
    kept_shas = {r.rsplit("/", 1)[1] for r in left}
    assert kept_shas == set(refs[3:])  # newest 5 kept


def test_maintenance_enabled_loop_gate(settings_override) -> None:
    settings_override(MAINTENANCE_ENABLED="false")
    assert maintenance.main(["loop"]) == 0


def test_projects_without_rows(client_factory) -> None:
    # A bare project with no payloads/history is a no-op, not an error.
    _ids(client_factory)
    out = maintenance.run_retention(s=_settings())
    assert out["deleted"].get("index_payloads") is None
