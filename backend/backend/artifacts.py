"""Content-addressed artifact store (diffs, logs, transcripts, webhook payloads, RL summaries).

Bytes live under ``ARTIFACT_STORE_URI`` (``file://`` for MVP; S3 later behind the
same interface) at ``sha256/<aa>/<sha256>``; the ``artifacts`` table indexes them.
Content is redacted before hashing unless the caller marks it already-safe.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend.db import artifacts
from backend.redaction import redact, redact_text
from backend.settings import get_settings
from shared.evidence import ArtifactKind, ArtifactRef
from shared.ids import IdPrefix, new_id

MAX_ARTIFACT_BYTES = 50 * 1024 * 1024


class ArtifactStore:
    def __init__(self, uri: str | None = None) -> None:
        uri = uri or get_settings().artifact_store_uri
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise ValueError(f"unsupported ARTIFACT_STORE_URI scheme: {parsed.scheme!r}")
        self.root = Path((parsed.netloc + parsed.path) or ".").expanduser().resolve()

    def _path(self, sha: str) -> Path:
        return self.root / "sha256" / sha[:2] / sha

    def put_bytes(
        self,
        conn: Connection,
        data: bytes,
        *,
        kind: ArtifactKind,
        project_id: str | None,
        media_type: str = "application/octet-stream",
        # False by default: raw bytes are NOT redacted here, and the stored flag
        # must never claim otherwise. put_text/put_json pass True after redacting.
        redacted: bool = False,
    ) -> ArtifactRef:
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ValueError("artifact exceeds 50 MiB limit")
        sha = hashlib.sha256(data).hexdigest()
        path = self._path(sha)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            # Concurrent writes of identical bytes share the final content address,
            # but each publisher must own its temporary file (including within a PID).
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=f"{sha}.", suffix=".tmp", delete=False
            ) as stream:
                tmp = Path(stream.name)
                try:
                    stream.write(data)
                    stream.close()
                    os.replace(tmp, path)
                finally:
                    tmp.unlink(missing_ok=True)
        art_id = new_id(IdPrefix.art)
        conn.execute(
            sa.insert(artifacts).values(
                id=art_id,
                project_id=project_id,
                kind=kind.value,
                sha256=sha,
                size_bytes=len(data),
                media_type=media_type,
                storage_key=str(path.relative_to(self.root)),
                redacted=redacted,
            )
        )
        return ArtifactRef(
            id=art_id,
            kind=kind,
            sha256=sha,
            size_bytes=len(data),
            media_type=media_type,
            redacted=redacted,
        )

    def put_text(
        self,
        conn: Connection,
        text: str,
        *,
        kind: ArtifactKind,
        project_id: str | None,
        media_type: str = "text/plain",
    ) -> ArtifactRef:
        return self.put_bytes(
            conn,
            redact_text(text).encode(),
            kind=kind,
            project_id=project_id,
            media_type=media_type,
            redacted=True,
        )

    def put_json(
        self, conn: Connection, value: Any, *, kind: ArtifactKind, project_id: str | None
    ) -> ArtifactRef:
        data = json.dumps(redact(value), sort_keys=True, default=str).encode()
        return self.put_bytes(
            conn,
            data,
            kind=kind,
            project_id=project_id,
            media_type="application/json",
            redacted=True,
        )

    def get(self, conn: Connection, artifact_id: str) -> tuple[dict[str, Any], bytes]:
        row = (
            conn.execute(sa.select(artifacts).where(artifacts.c.id == artifact_id))
            .mappings()
            .first()
        )
        if row is None:
            raise KeyError(artifact_id)
        data = (self.root / row["storage_key"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise OSError(f"artifact {artifact_id} failed integrity check")
        return dict(row), data
