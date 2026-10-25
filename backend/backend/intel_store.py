"""PostgreSQL implementation of code-intelligence's ``PayloadStore`` port (Sarosh).

Protocol: ``code_intelligence.persistence.PayloadStore`` (coordination message
20261017T010930Z-sarosh-to-srihith-persistence-protocol). Semantics enforced here:

* every row is keyed by the complete SnapshotRef (project, repo, SHA, index version)
  plus ``kind``/``key``; the repository must belong to the project (tenant isolation);
* payloads are immutable: an identical rewrite is idempotent, different bytes fail;
* a snapshot becomes visible only after ``mark_ready``; reads and ``find_snapshot``
  of an unready snapshot raise ``KeyError``; a ready snapshot accepts no new keys;
* ``mark_ready`` also records the Snapshot in the backend ``snapshots`` table so the
  API (``GET /snapshots/{id}``) and monitoring see the same state.

Factory for the intelligence service: ``backend.intel_store:build_payload_store``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from backend.db import (
    get_engine,
    index_payloads,
    index_snapshots,
    projects,
    repositories,
    snapshots,
)
from shared.base import utcnow
from shared.intelligence import Snapshot, SnapshotRef, SnapshotStats, SnapshotStatus

_KIND = re.compile(r"^[a-z0-9_.\-]{1,80}$")
_KEY = re.compile(r"^[A-Za-z0-9_.\-/]{1,300}$")
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024

try:  # use the producer's KeyPage type when the intelligence package is installed
    from code_intelligence.persistence import KeyPage
except ModuleNotFoundError:  # pragma: no cover - structural stand-in for the same fields

    @dataclass(frozen=True)
    class KeyPage:  # type: ignore[no-redef]
        keys: list[str]
        cursor: str | None = None


class PayloadConflict(ValueError):
    """An immutable payload key was rewritten with different bytes."""


def _ref_where(table: sa.Table, ref: SnapshotRef) -> list[Any]:
    return [
        table.c.project_id == ref.project_id,
        table.c.repo_id == ref.repo_id,
        table.c.commit_sha == ref.commit_sha,
        table.c.index_version == ref.index_version,
    ]


def _check_key(kind: str, key: str) -> None:
    if not _KIND.match(kind):
        raise ValueError(f"invalid payload kind {kind!r}")
    if not _KEY.match(key) or key.startswith("/") or ".." in key.split("/"):
        raise ValueError(f"invalid payload key {key!r}")


class PostgresPayloadStore:
    def __init__(self, engine: Engine | None = None) -> None:
        self.engine = engine or get_engine()

    def memory_store(self) -> object:
        """Companion append-only memory; source payload immutability is unchanged."""
        from backend.intelligence_memory import PostgresMemoryStore

        return PostgresMemoryStore(self.engine)

    # -------------------------------------------------------------- helpers
    def _scope_tenant(self, conn: sa.Connection, project_id: str) -> None:
        """Scope this transaction to one tenant for row-level security.

        index_payloads/index_snapshots carry FORCE RLS: without this GUC the
        tables read as empty and writes are rejected. Transaction-local only —
        it reverts at commit and never leaks into pooled connections.
        """
        conn.execute(
            sa.text("SELECT set_config('ari.project_id', :pid, true)"), {"pid": project_id}
        )

    def _validate_ref(self, conn: sa.Connection, ref: SnapshotRef) -> None:
        ref = SnapshotRef.model_validate(ref.model_dump())
        owner = conn.execute(
            sa.select(repositories.c.project_id).where(repositories.c.id == ref.repo_id)
        ).scalar()
        if owner is None:
            raise PermissionError(f"repository {ref.repo_id} is not connected")
        if owner != ref.project_id:
            raise PermissionError("repository does not belong to the SnapshotRef project")
        self._scope_tenant(conn, ref.project_id)

    def _ready_row(self, conn: sa.Connection, ref: SnapshotRef) -> Any:
        return conn.execute(
            sa.select(index_snapshots).where(*_ref_where(index_snapshots, ref))
        ).first()

    # ------------------------------------------------------------- protocol
    def put_payload(self, snapshot_ref: SnapshotRef, kind: str, key: str, payload: bytes) -> str:
        _check_key(kind, key)
        if not isinstance(payload, bytes | bytearray):
            raise TypeError("payload must be bytes")
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload exceeds 64 MiB")
        digest = hashlib.sha256(payload).hexdigest()
        with self.engine.begin() as conn:
            self._validate_ref(conn, snapshot_ref)
            ready = self._ready_row(conn, snapshot_ref)
            existing = conn.execute(
                sa.select(index_payloads.c.sha256).where(
                    *_ref_where(index_payloads, snapshot_ref),
                    index_payloads.c.kind == kind,
                    index_payloads.c.key == key,
                )
            ).scalar()
            if existing is not None:
                if existing == digest:
                    return digest
                if ready is not None:
                    raise PayloadConflict(
                        f"{kind}/{key} is immutable and differs from the stored payload"
                    )
                # Not-ready payload: leftover scratch from an indexing attempt that
                # never reached ready, so nothing could have read it. Replace it —
                # otherwise one crashed run permanently poisons the commit's index.
                conn.execute(
                    index_payloads.delete().where(
                        *_ref_where(index_payloads, snapshot_ref),
                        index_payloads.c.kind == kind,
                        index_payloads.c.key == key,
                    )
                )
            if ready is not None:
                raise PayloadConflict(
                    "snapshot is already marked ready; it accepts no new payloads"
                )
            conn.execute(
                pg_insert(index_payloads)
                .values(
                    project_id=snapshot_ref.project_id,
                    repo_id=snapshot_ref.repo_id,
                    commit_sha=snapshot_ref.commit_sha,
                    index_version=snapshot_ref.index_version,
                    snapshot_id=snapshot_ref.snapshot_id,
                    kind=kind,
                    key=key,
                    sha256=digest,
                    size_bytes=len(payload),
                    data=bytes(payload),
                )
                .on_conflict_do_nothing()
            )
            stored = conn.execute(
                sa.select(index_payloads.c.sha256).where(
                    *_ref_where(index_payloads, snapshot_ref),
                    index_payloads.c.kind == kind,
                    index_payloads.c.key == key,
                )
            ).scalar_one()
            if stored != digest:  # lost a concurrent race to different bytes
                raise PayloadConflict(f"{kind}/{key} was concurrently written with different bytes")
        return digest

    def get_payload(self, snapshot_ref: SnapshotRef, kind: str, key: str) -> bytes:
        _check_key(kind, key)
        with self.engine.begin() as conn:
            self._validate_ref(conn, snapshot_ref)
            if self._ready_row(conn, snapshot_ref) is None:
                raise KeyError(f"snapshot {snapshot_ref.snapshot_id} is not ready")
            row = conn.execute(
                sa.select(index_payloads.c.data, index_payloads.c.sha256).where(
                    *_ref_where(index_payloads, snapshot_ref),
                    index_payloads.c.kind == kind,
                    index_payloads.c.key == key,
                )
            ).first()
        if row is None:
            raise KeyError(f"{kind}/{key}")
        data = bytes(row.data)
        if hashlib.sha256(data).hexdigest() != row.sha256:
            raise OSError(f"payload {kind}/{key} failed integrity check")
        return data

    def list_keys(
        self,
        snapshot_ref: SnapshotRef,
        kind: str,
        prefix: str = "",
        limit: int = 100,
        cursor: str | None = None,
    ) -> KeyPage:
        _check_key(kind, "x")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be within 1..500")
        with self.engine.begin() as conn:
            self._validate_ref(conn, snapshot_ref)
            if self._ready_row(conn, snapshot_ref) is None:
                raise KeyError(f"snapshot {snapshot_ref.snapshot_id} is not ready")
            q = sa.select(index_payloads.c.key).where(
                *_ref_where(index_payloads, snapshot_ref), index_payloads.c.kind == kind
            )
            if prefix:
                escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                q = q.where(index_payloads.c.key.like(escaped + "%", escape="\\"))
            if cursor:
                q = q.where(index_payloads.c.key > cursor)
            keys = list(conn.execute(q.order_by(index_payloads.c.key).limit(limit + 1)).scalars())
        page = keys[:limit]
        return KeyPage(keys=page, cursor=page[-1] if len(keys) > limit else None)

    def mark_ready(self, snapshot_ref: SnapshotRef, stats: SnapshotStats) -> None:
        stats = SnapshotStats.model_validate(stats.model_dump())
        now = utcnow()
        with self.engine.begin() as conn:
            self._validate_ref(conn, snapshot_ref)
            if not conn.execute(
                sa.select(sa.func.count())
                .select_from(index_payloads)
                .where(*_ref_where(index_payloads, snapshot_ref))
            ).scalar():
                raise ValueError("cannot mark a snapshot ready without payloads")
            conn.execute(
                pg_insert(index_snapshots)
                .values(
                    project_id=snapshot_ref.project_id,
                    repo_id=snapshot_ref.repo_id,
                    commit_sha=snapshot_ref.commit_sha,
                    index_version=snapshot_ref.index_version,
                    snapshot_id=snapshot_ref.snapshot_id,
                    stats=stats.model_dump(mode="json"),
                )
                .on_conflict_do_nothing()
            )
            row = conn.execute(
                sa.select(snapshots.c.doc)
                .where(snapshots.c.id == snapshot_ref.snapshot_id)
                .with_for_update()
            ).scalar()
            if row is not None:
                snap = Snapshot.model_validate(row)
                if (
                    snap.repo_id != snapshot_ref.repo_id
                    or snap.commit_sha != snapshot_ref.commit_sha
                ):
                    raise PermissionError("snapshot id belongs to a different repo/SHA")
            else:
                snap = Snapshot(
                    id=snapshot_ref.snapshot_id,
                    repo_id=snapshot_ref.repo_id,
                    commit_sha=snapshot_ref.commit_sha,
                    status=SnapshotStatus.pending,
                    index_version=snapshot_ref.index_version,
                    created_at=now,
                )
            snap = snap.model_copy(
                update={
                    "status": SnapshotStatus.ready,
                    "stats": stats,
                    "index_version": snapshot_ref.index_version,
                    "completed_at": snap.completed_at or now,
                    "error_message": None,
                }
            )
            values = {
                "status": snap.status.value,
                "index_version": snap.index_version,
                "doc": snap.model_dump(mode="json"),
            }
            if row is not None:
                conn.execute(sa.update(snapshots).where(snapshots.c.id == snap.id).values(**values))
            else:
                conn.execute(
                    sa.insert(snapshots).values(
                        id=snap.id, repo_id=snap.repo_id, commit_sha=snap.commit_sha, **values
                    )
                )

    def find_snapshot(
        self, project_id: str, repo_id: str, commit_sha: str, index_version: str
    ) -> SnapshotRef:
        with self.engine.begin() as conn:
            self._scope_tenant(conn, project_id)
            row = conn.execute(
                sa.select(index_snapshots.c.snapshot_id).where(
                    index_snapshots.c.project_id == project_id,
                    index_snapshots.c.repo_id == repo_id,
                    index_snapshots.c.commit_sha == commit_sha,
                    index_snapshots.c.index_version == index_version,
                )
            ).first()
            if row is None:
                raise KeyError(f"no ready snapshot for {repo_id}@{commit_sha} ({index_version})")
            owner = conn.execute(
                sa.select(projects.c.id)
                .join(repositories, repositories.c.project_id == projects.c.id)
                .where(repositories.c.id == repo_id)
            ).scalar()
        if owner != project_id:
            raise PermissionError("repository does not belong to project")
        return SnapshotRef(
            project_id=project_id,
            repo_id=repo_id,
            commit_sha=commit_sha,
            snapshot_id=row.snapshot_id,
            index_version=index_version,
        )


def build_payload_store() -> PostgresPayloadStore:
    """Factory for ``code_intelligence`` (e.g. CODE_INTELLIGENCE_PAYLOAD_STORE_FACTORY)."""
    return PostgresPayloadStore()
