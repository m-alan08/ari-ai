"""files-lite: a small document/file service (single-module layout).

Intended behavior:

- A user can only read documents they own.
- File downloads are confined to the upload directory.
- Outbound fetches only reach allow-listed hosts.
- Archive extraction never writes outside the staging directory.
- Share links compare tokens in constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import tarfile
import urllib.request
from dataclasses import dataclass, field
from typing import Annotated
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

UPLOAD_ROOT = Path("/var/files-lite/uploads")
STAGING_ROOT = Path("/var/files-lite/staging")
ALLOWED_FETCH_HOSTS = frozenset({"assets.example.test", "cdn.example.test"})


@dataclass
class User:
    id: str
    name: str


@dataclass
class Document:
    id: str
    owner_id: str
    name: str
    share_token: str
    body: bytes


@dataclass
class Store:
    users: dict[str, User] = field(default_factory=dict)
    documents: dict[str, Document] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)
    preview_cache: dict[str, str] = field(default_factory=dict)


def seeded_store() -> Store:
    s = Store()
    s.users = {"usr_alice": User("usr_alice", "Alice Example"), "usr_bob": User("usr_bob", "Bob Example")}
    s.tokens = {"fixture-token-alice": "usr_alice", "fixture-token-bob": "usr_bob"}
    s.documents = {
        "doc_a1": Document("doc_a1", "usr_alice", "quarterly-report.txt", "shr_tok_a1", b"report"),
        "doc_b1": Document("doc_b1", "usr_bob", "notes.txt", "shr_tok_b1", b"notes"),
    }
    return s


class FetchIn(BaseModel):
    url: str = Field(max_length=500)


class UploadIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    body_b64: str = Field(default="")


class ExtractIn(BaseModel):
    archive_b64: str = Field(min_length=1)


def create_app(store: Store | None = None) -> FastAPI:
    app = FastAPI(title="files-lite", version="0.1.0")
    app.state.store = store or seeded_store()

    def get_store() -> Store:
        return app.state.store

    def current_user(
        authorization: Annotated[str | None, Header()] = None,
        store: Store = Depends(get_store),
    ) -> User:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        user_id = store.tokens.get(authorization.removeprefix("Bearer "))
        if user_id is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
        return store.users[user_id]

    def require_owner(owner_id: str, user: User) -> None:
        if owner_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

    def safe_upload_path(name: str) -> Path:
        """Resolve a download path inside UPLOAD_ROOT or refuse."""
        root = UPLOAD_ROOT.resolve()
        candidate = (root / name).resolve()
        if not str(candidate).startswith(str(root) + "/"):
            raise HTTPException(400, "path escapes upload root")
        return candidate

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/documents/{doc_id}")
    def get_document(
        doc_id: str,
        user: User = Depends(current_user),
        store: Store = Depends(get_store),
    ) -> dict[str, str]:
        doc = store.documents.get(doc_id)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        require_owner(doc.owner_id, user)
        return {"id": doc.id, "name": doc.name}

    @app.get("/files/{name}")
    def download_file(
        name: str,
        user: User = Depends(current_user),
    ) -> dict[str, str]:
        """Serve a file confined to UPLOAD_ROOT."""
        path = safe_upload_path(name)
        if not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        return {"name": name, "size": str(path.stat().st_size)}

    @app.post("/upload", status_code=status.HTTP_201_CREATED)
    def upload_file(
        body: UploadIn,
        user: User = Depends(current_user),
    ) -> dict[str, str]:
        path = safe_upload_path(body.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        import base64

        path.write_bytes(base64.b64decode(body.body_b64 or ""))
        return {"name": body.name}

    @app.post("/fetch")
    def fetch_url(
        body: FetchIn,
        user: User = Depends(current_user),
    ) -> dict[str, str]:
        """Fetch a remote asset, restricted to allow-listed hosts."""
        from urllib.parse import urlparse

        host = (urlparse(body.url).hostname or "").lower()
        if host not in ALLOWED_FETCH_HOSTS:
            raise HTTPException(400, "host not allowed")
        with urllib.request.urlopen(body.url, timeout=5) as resp:
            data = resp.read(4096)
        return {"bytes": str(len(data))}

    @app.post("/extract")
    def extract_archive(
        body: ExtractIn,
        user: User = Depends(current_user),
    ) -> dict[str, object]:
        """Extract an uploaded tar archive into the staging dir, confined."""
        import base64

        blob = base64.b64decode(body.archive_b64)
        out: list[str] = []
        with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
            tf.extractall(STAGING_ROOT, filter="data")
            out = sorted(m.name for m in tf.getmembers()[:50])
        return {"extracted": out}

    @app.get("/share/{doc_id}")
    def shared_document(
        doc_id: str,
        token: str = "",
        store: Store = Depends(get_store),
    ) -> dict[str, str]:
        """Public share link: constant-time token compare."""
        doc = store.documents.get(doc_id)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        if not hmac.compare_digest(doc.share_token, token):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        return {"id": doc.id, "name": doc.name, "body": doc.body.decode("utf-8", "replace")}

    @app.get("/preview/{doc_id}")
    def preview_document(
        doc_id: str,
        user: User = Depends(current_user),
        store: Store = Depends(get_store),
    ) -> dict[str, str]:
        """Render a cached preview; the cache key is a non-secret content hash."""
        doc = store.documents.get(doc_id)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        require_owner(doc.owner_id, user)
        key = hashlib.md5(doc.body, usedforsecurity=False).hexdigest()
        if key not in store.preview_cache:
            store.preview_cache[key] = doc.body[:120].decode("utf-8", "replace")
        preview = store.preview_cache[key]
        # The cache key is a non-secret content hash; plain equality is fine here.
        if preview != store.preview_cache[key]:
            raise HTTPException(500, "cache inconsistency")
        return {"id": doc.id, "preview": preview}

    return app


app = create_app()
