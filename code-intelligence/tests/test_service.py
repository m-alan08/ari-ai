from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from intel_test_helpers import commit
from intel_test_helpers import repository as repository
from shared.base import utcnow
from shared.intelligence import ContextPack, ContextPackRequest, Snapshot, SnapshotStatus

from code_intelligence.api import IntelligenceService, RepositoryBinding, build_service
from code_intelligence.models import INDEX_VERSION, IntelligenceError
from code_intelligence.retrieval import estimate
from code_intelligence.store import SQLiteIndexStore


def service(path: Path, database: Path) -> IntelligenceService:
    return IntelligenceService(
        SQLiteIndexStore(database),
        [
            RepositoryBinding(
                project_id="prj_fixture00",
                repo_id="rep_fixture00",
                full_name="owned/fixture",
                local_path=path,
            )
        ],
    )


def test_durable_context_for_reviewer_and_scout(
    repository: tuple[Path, str], tmp_path: Path
) -> None:
    root, sha = repository
    db = tmp_path / "index.sqlite"
    engine = service(root, db)
    ref = engine.index_repository("prj_fixture00", "rep_fixture00", sha)
    restarted = service(root, db)
    assert restarted.index_repository("prj_fixture00", "rep_fixture00", sha) == ref
    subject = restarted.get_symbol(ref, "app.payments.read_payment")
    assert {s.qualified_name for s in restarted.graph_neighbors(ref, subject.id)} >= {
        "app.payments.read_payment",
        "app.auth.current_user",
        "app.payments.refund",
    }
    for purpose, focus in [("review", "app.payments.refund"), ("scout", "/payments/{payment_id}")]:
        pack = restarted.build_context_pack(
            ContextPackRequest(
                snapshot_ref=ref,
                purpose=purpose,
                focus=[focus],
                token_budget=20_000,
            )
        )
        assert ContextPack.model_validate_json(pack.model_dump_json()) == pack
        assert estimate(pack.model_dump_json()) <= pack.token_count <= pack.token_budget
        text = "\n".join(c.content for c in pack.chunks)
        assert "def refund" in text
        assert "current_user" in text
        assert "test_refund_bounds" in text
        assert "# Payments fixture" not in text
        assert all(p.commit_sha == sha for p in pack.provenance)
        assert all(c.trust == "untrusted" for c in pack.chunks)
        assert all("<untrusted_repository_data" in c.content for c in pack.chunks)
    assert len(restarted.symbols(ref, offset=0, limit=2)) == 2
    with pytest.raises(IntelligenceError, match="Pagination"):
        restarted.symbols(ref, limit=1000)
    assert restarted.read_span(ref, "app/payments.py", 5, 7).startswith("def refund")


def test_context_budget_stale_and_project_scope(
    repository: tuple[Path, str], tmp_path: Path
) -> None:
    root, sha = repository
    engine = service(root, tmp_path / "index.sqlite")
    ref = engine.index_repository("prj_fixture00", "rep_fixture00", sha)
    for changes in [
        dict(commit_sha="f" * 40),
        dict(project_id="prj_other000"),
        dict(repo_id="rep_other000"),
        dict(index_version="outdated"),
    ]:
        stale = ref.model_copy(update=changes)
        with pytest.raises(IntelligenceError):
            engine.build_context_pack(
                ContextPackRequest(
                    snapshot_ref=stale,
                    purpose="review",
                    focus=["app.payments.refund"],
                )
            )
    with pytest.raises(IntelligenceError, match="BUDGET_EXHAUSTED"):
        engine.build_context_pack(
            ContextPackRequest(
                snapshot_ref=ref,
                purpose="review",
                focus=["app.payments.refund"],
                token_budget=256,
            )
        )
    with pytest.raises(IntelligenceError, match="does not belong"):
        engine.index_repository("prj_other000", "rep_fixture00", sha)


def test_rename_delete_and_test_change_invalidation(
    repository: tuple[Path, str], tmp_path: Path
) -> None:
    root, sha = repository
    engine = service(root, tmp_path / "index.sqlite")
    base = engine.index_repository("prj_fixture00", "rep_fixture00", sha)
    file = root / "app/payments.py"
    file.write_text(file.read_text().replace("return min(amount, paid)", "return amount"))
    (root / "app/auth.py").rename(root / "app/identity.py")
    tests = root / "tests/test_payments.py"
    tests.write_text(tests.read_text().replace("== 20", "<= 20"))
    (root / "image.bin").write_bytes(b"\x00\x01")
    head_sha = commit(root, "weaken bounds and rename auth")
    head = engine.index_repository("prj_fixture00", "rep_fixture00", head_sha, base_snapshot=base)
    impact = engine.affected_by_diff("rep_fixture00", sha, head_sha)
    assert impact.resolution == "symbol"
    assert set(impact.changed_paths) == {
        "app/auth.py",
        "app/identity.py",
        "app/payments.py",
        "tests/test_payments.py",
        "image.bin",
    }
    assert engine.get_symbol(base, "app.auth.current_user").id in impact.symbols
    assert engine.get_symbol(head, "app.payments.read_payment").id in impact.symbols
    assert impact.expectations
    assert "tests/test_payments.py" in impact.regression_tests
    assert engine.store.get(head).carried_from == {}
    assert engine.store.get(head).base_snapshot_id == base.snapshot_id
    fresh = engine.build_context_pack(
        ContextPackRequest(
            snapshot_ref=head,
            purpose="monitoring",
            focus=["app.payments.refund"],
            token_budget=16_000,
        )
    )
    assert all(p.commit_sha == head_sha for p in fresh.provenance)
    assert "return min" not in "\n".join(c.content for c in fresh.chunks)
    assert engine.affected_by_diff("rep_fixture00", None, head_sha).base_snapshot_id is None


def test_store_integrity_and_backend_adapter(repository: tuple[Path, str], tmp_path: Path) -> None:
    root, sha = repository
    db = tmp_path / "index.sqlite"
    engine = service(root, db)
    pending = Snapshot(
        id="snp_backend00",
        repo_id="rep_fixture00",
        commit_sha=sha,
        status=SnapshotStatus.pending,
        index_version="requested",
        created_at=utcnow(),
    )
    ready = engine.index_snapshot(pending, "owned/fixture")
    assert ready.id == pending.id and ready.status == "ready"
    assert engine.index_snapshot(pending, "owned/fixture") == ready
    with pytest.raises(IntelligenceError, match="does not match"):
        engine.index_snapshot(pending, "outside/fixture")
    ref = engine.store.find("prj_fixture00", "rep_fixture00", sha, INDEX_VERSION).ref
    index = engine.store.get(ref)
    engine.store.put(index)
    changed = index.model_copy(update={"tree_sha": "f" * 40})
    with pytest.raises(IntelligenceError, match="Immutable"):
        engine.store.put(changed)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE intelligence_indexes SET doc='{}'")
    with pytest.raises(IntelligenceError, match="integrity"):
        engine.store.get(ref)


def test_factory_requires_explicit_durable_config(
    repository: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, sha = repository
    monkeypatch.delenv("INTELLIGENCE_DB_PATH", raising=False)
    monkeypatch.delenv("INTELLIGENCE_REPOSITORIES_FILE", raising=False)
    with pytest.raises(IntelligenceError, match="Set INTELLIGENCE"):
        build_service()
    config = tmp_path / "repositories.json"
    config.write_text(
        json.dumps(
            [
                dict(
                    project_id="prj_fixture00",
                    repo_id="rep_fixture00",
                    full_name="owned/fixture",
                    local_path=str(root),
                )
            ]
        )
    )
    monkeypatch.setenv("INTELLIGENCE_REPOSITORIES_FILE", str(config))
    monkeypatch.setenv("INTELLIGENCE_DB_PATH", str(tmp_path / "index.sqlite"))
    assert build_service().index_repository("prj_fixture00", "rep_fixture00", sha).commit_sha == sha
    monkeypatch.delenv("INTELLIGENCE_DB_PATH")
    monkeypatch.setenv(
        "INTELLIGENCE_PAYLOAD_STORE_FACTORY", "backend.intel_store:build_payload_store"
    )
    from backend.intel_store import PostgresPayloadStore

    from code_intelligence.persistence import PayloadIndexStore

    postgres_service = build_service()
    assert isinstance(postgres_service.store, PayloadIndexStore)
    assert isinstance(postgres_service.store.payloads, PostgresPayloadStore)
    monkeypatch.setenv("INTELLIGENCE_DB_PATH", str(tmp_path / "index.sqlite"))
    with pytest.raises(IntelligenceError, match="exactly one"):
        build_service()


def test_diff_preserves_unchanged_callers_and_excludes_unrelated_sibling(
    repository: tuple[Path, str],
    tmp_path: Path,
) -> None:
    root, _ = repository
    file = root / "app/payments.py"
    file.write_text(file.read_text() + "\ndef unrelated():\n    return 99\n")
    base_sha = commit(root)
    engine = service(root, tmp_path / "index.sqlite")
    base = engine.index_repository("prj_fixture00", "rep_fixture00", base_sha)
    file.write_text(
        file.read_text().replace("return min(amount, paid)", "return max(amount, paid)")
    )
    head_sha = commit(root)
    head = engine.index_repository("prj_fixture00", "rep_fixture00", head_sha, base_snapshot=base)
    impact = engine.affected_by_diff("rep_fixture00", base_sha, head_sha)
    assert engine.get_symbol(head, "app.payments.read_payment").id in impact.symbols
    assert engine.get_symbol(base, "app.payments.read_payment").id in impact.symbols
    assert engine.get_symbol(head, "app.payments.unrelated").id not in impact.symbols


def test_backend_adapter_reports_invalid_commit_as_terminal_failure(
    repository: tuple[Path, str],
    tmp_path: Path,
) -> None:
    root, _ = repository
    engine = service(root, tmp_path / "index.sqlite")
    pending = Snapshot(
        id="snp_missing00",
        repo_id="rep_fixture00",
        commit_sha="f" * 40,
        status=SnapshotStatus.pending,
        index_version=INDEX_VERSION,
        created_at=utcnow(),
    )
    failed = engine.index_snapshot(pending, "owned/fixture")
    assert failed.status == "failed" and failed.completed_at is not None
    assert failed.id == pending.id and failed.commit_sha == pending.commit_sha
    assert failed.error_message.startswith("INDEX_FAILED")
    with pytest.raises(IntelligenceError, match="SNAPSHOT_NOT_READY"):
        engine.store.find("prj_fixture00", "rep_fixture00", pending.commit_sha, INDEX_VERSION)


def test_payload_index_store_reads_each_snapshot_once(
    repository: tuple[Path, str], tmp_path: Path
) -> None:
    from code_intelligence.persistence import PayloadIndexStore

    root, sha = repository
    index = service(root, tmp_path / "index.sqlite").index_repository(
        "prj_fixture00", "rep_fixture00", sha
    )
    stored = service(root, tmp_path / "index.sqlite").store.get(index)

    class Payloads:
        reads = 0

        def get_payload(self, ref: object, kind: str, key: str) -> bytes:
            Payloads.reads += 1
            return stored.model_dump_json().encode()

    store = PayloadIndexStore(Payloads())  # type: ignore[arg-type]
    assert store.get(index) == store.get(index) == stored
    assert PayloadIndexStore(Payloads()).get(index) == stored  # shared across instances
    assert Payloads.reads == 1
    other = index.model_copy(update={"project_id": "prj_fixture01"})
    with pytest.raises(IntelligenceError, match="mismatched"):
        store.get(other)  # another tenant's ref never hits the cache


def test_batched_blob_reads_refuse_a_missing_object(repository: tuple[Path, str]) -> None:
    from code_intelligence.ingest import GitReader

    root, sha = repository
    _, files, _ = GitReader(root).read(sha)
    import subprocess

    for f in files:  # the batched read returns exactly each blob
        shown = subprocess.run(
            ["git", "-C", str(root), "show", f"{sha}:{f.path}"], capture_output=True, check=True
        ).stdout.decode()
        assert f.content == shown, f.path
    with pytest.raises(IntelligenceError, match="blob read failed"):
        GitReader(root)._blobs(["0" * 40], 0)


def test_stored_index_payload_contains_no_unredacted_secret_literals(
    repository: tuple[Path, str], tmp_path: Path
) -> None:
    """End-to-end: the StoredIndex JSON is what SQLite/Postgres persist and what
    context packs are built from — the raw literals must never survive into it."""
    root, _ = repository
    secret = "hunter2" + "z" * 12
    (root / "app/secrets_file.py").write_text(
        f'DB_PASSWORD = "{secret}"\n'
        f'r1 = f(password={secret}, user="alice")\n'
        f"r2 = dict(password={secret})\n"
        f'r3 = {{"password": {secret}}}\n'
        "secret_configured = bool(True)\n"
    )
    sha = commit(root)
    db = tmp_path / "index.sqlite"
    engine = service(root, db)
    ref = engine.index_repository("prj_fixture00", "rep_fixture00", sha)
    stored = service(root, db).store.get(ref)
    blob = stored.model_dump_json()
    assert secret not in blob
    assert "[REDACTED]" in blob
    src = next(f for f in stored.files if f.path == "app/secrets_file.py")
    assert 'DB_PASSWORD = "[REDACTED]"' in src.content
    assert "password=[REDACTED]" in src.content
    # the raw database bytes carry nothing else
    conn = sqlite3.connect(db)
    doc = conn.execute("SELECT doc FROM intelligence_indexes").fetchone()[0]
    conn.close()
    assert secret not in doc
