"""PostgresPayloadStore against real PostgreSQL: immutability, readiness visibility,
tenant isolation, pagination, snapshot registration; plus Sarosh's PayloadIndexStore
adapter when code_intelligence is installed."""

from __future__ import annotations

import hashlib

import pytest
from helpers import setup_project

from backend.intel_store import PayloadConflict, PostgresPayloadStore
from shared.ids import IdPrefix, new_id
from shared.intelligence import SnapshotRef, SnapshotStats

SHA = "d" * 40


@pytest.fixture
def ref(client_factory) -> SnapshotRef:
    alice, bob = client_factory("alice"), client_factory("bob")
    ids = setup_project(alice, bob)
    ref = SnapshotRef(
        project_id=ids["project"],
        repo_id=ids["repo"],
        commit_sha=SHA,
        snapshot_id=new_id(IdPrefix.snp),
        index_version="intel-0.1",
    )
    ref.__dict__["_alice"] = alice
    return ref


def test_write_once_ready_then_read(ref: SnapshotRef) -> None:
    store = PostgresPayloadStore()
    data = b'{"symbols": []}'
    assert (
        store.put_payload(ref, "intelligence_index", "index.json", data)
        == hashlib.sha256(data).hexdigest()
    )
    assert store.put_payload(
        ref, "intelligence_index", "index.json", data
    )  # identical rewrite is idempotent
    # A not-ready payload is scratch from an unfinished attempt — rewriting it is
    # what lets a crashed indexer retry the same commit instead of deadlocking.
    store.put_payload(ref, "intelligence_index", "index.json", b"different")
    with pytest.raises(KeyError):  # invisible until ready
        store.get_payload(ref, "intelligence_index", "index.json")
    with pytest.raises(KeyError):
        store.find_snapshot(ref.project_id, ref.repo_id, SHA, "intel-0.1")
    store.mark_ready(ref, SnapshotStats(files=3, symbols=12))
    assert store.get_payload(ref, "intelligence_index", "index.json") == b"different"
    assert store.find_snapshot(ref.project_id, ref.repo_id, SHA, "intel-0.1") == ref
    with pytest.raises(PayloadConflict):
        store.put_payload(ref, "intelligence_index", "index.json", b"v3")
    with pytest.raises(PayloadConflict, match="ready"):
        store.put_payload(ref, "intelligence_index", "late.json", b"{}")
    with pytest.raises(KeyError):
        store.get_payload(ref, "intelligence_index", "missing.json")
    # The backend snapshot record and API reflect readiness.
    snap = ref.__dict__["_alice"].get(f"/api/v1/snapshots/{ref.snapshot_id}").json()
    assert (
        snap["status"] == "ready" and snap["stats"]["symbols"] == 12 and snap["commit_sha"] == SHA
    )


def test_versions_and_tenants_are_isolated(ref: SnapshotRef, client_factory) -> None:
    store = PostgresPayloadStore()
    store.put_payload(ref, "intelligence_index", "index.json", b"v1")
    store.mark_ready(ref, SnapshotStats())
    other_version = ref.model_copy(
        update={"index_version": "intel-0.2", "snapshot_id": new_id(IdPrefix.snp)}
    )
    with pytest.raises(KeyError):
        store.get_payload(other_version, "intelligence_index", "index.json")
    other_sha = ref.model_copy(update={"commit_sha": "e" * 40})
    with pytest.raises(KeyError):
        store.find_snapshot(ref.project_id, ref.repo_id, other_sha.commit_sha, "intel-0.1")
    eve = client_factory("eve")
    foreign = eve.post("/api/v1/projects", json={"name": "eve"}).json()["id"]
    wrong_project = ref.model_copy(update={"project_id": foreign})
    with pytest.raises(PermissionError):
        store.get_payload(wrong_project, "intelligence_index", "index.json")
    with pytest.raises(PermissionError):
        store.put_payload(wrong_project, "intelligence_index", "x.json", b"{}")
    with pytest.raises(ValueError):
        store.put_payload(ref, "intelligence_index", "../escape", b"{}")
    with pytest.raises(ValueError):
        store.put_payload(ref, "Bad Kind", "k", b"{}")


def test_list_keys_paginates_with_prefix(ref: SnapshotRef) -> None:
    store = PostgresPayloadStore()
    for i in range(7):
        store.put_payload(ref, "files", f"app/m{i}.json", b"{}")
    store.put_payload(ref, "files", "tests/t.json", b"{}")
    store.put_payload(ref, "files", "app_x/odd.json", b"{}")
    with pytest.raises(ValueError):
        store.mark_ready(
            ref.model_copy(update={"commit_sha": "f" * 40}), SnapshotStats()
        )  # no payloads
    store.mark_ready(ref, SnapshotStats())
    seen, cursor = [], None
    while True:
        page = store.list_keys(ref, "files", prefix="app/", limit=3, cursor=cursor)
        seen += page.keys
        if page.cursor is None:
            break
        cursor = page.cursor
    assert seen == [f"app/m{i}.json" for i in range(7)]  # '_' in prefix is literal, not a wildcard
    with pytest.raises(ValueError):
        store.list_keys(ref, "files", limit=501)


def test_sarosh_payload_index_store_adapter(ref: SnapshotRef) -> None:
    persistence = pytest.importorskip("code_intelligence.persistence")
    models = pytest.importorskip("code_intelligence.models")
    store = persistence.PayloadIndexStore(PostgresPayloadStore())
    fields = models.StoredIndex.model_fields
    if "ref" not in fields or "snapshot" not in fields:
        pytest.skip("StoredIndex shape differs from the agreed protocol")
    with pytest.raises(models.IntelligenceError, match="SNAPSHOT_NOT_READY"):
        store.find(ref.project_id, ref.repo_id, SHA, "intel-0.1")
