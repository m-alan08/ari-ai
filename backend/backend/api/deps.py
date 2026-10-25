"""Request plumbing: errors, request ids, sessions/CSRF, authorization, rate limits, idempotency."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from fastapi import Request
from sqlalchemy.engine import Connection

from backend.db import idempotency_keys, memberships, projects, sessions, users
from backend.settings import get_settings
from shared.base import utcnow
from shared.errors import ErrorCode
from shared.tenancy import MemberRole

SESSION_COOKIE = "hackatl_session"
CSRF_HEADER = "X-CSRF-Token"
_ROLE_RANK = {MemberRole.viewer: 0, MemberRole.member: 1, MemberRole.owner: 2}


class ApiError(Exception):
    def __init__(
        self, code: ErrorCode, message: str, status: int, details: dict[str, Any] | None = None
    ):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details or {}


def not_found(what: str) -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, f"{what} not found", 404)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    github_login: str
    csrf_token: str
    session_hash: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: Connection, user_id: str) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    expires = utcnow().timestamp() + get_settings().session_ttl_s
    conn.execute(
        sa.insert(sessions).values(
            token_hash=_hash(token),
            user_id=user_id,
            csrf_token=csrf,
            expires_at=sa.func.to_timestamp(expires),
        )
    )
    return token, csrf


def session_user(conn: Connection, request: Request) -> CurrentUser | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = conn.execute(
        sa.select(users.c.id, users.c.github_login, sessions.c.csrf_token, sessions.c.token_hash)
        .join(users, users.c.id == sessions.c.user_id)
        .where(sessions.c.token_hash == _hash(token), sessions.c.expires_at > sa.func.now())
    ).first()
    return CurrentUser(*row) if row else None


def require_user(conn: Connection, request: Request) -> CurrentUser:
    user = session_user(conn, request)
    if user is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "sign in required", 401)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get(CSRF_HEADER, "")
        if not secrets.compare_digest(sent.encode(), user.csrf_token.encode()):
            raise ApiError(ErrorCode.CSRF_FAILED, "missing or invalid X-CSRF-Token", 403)
    limiter.check(f"user:{user.id}", get_settings().rate_limit_per_minute, 60)
    return user


def org_role(conn: Connection, user_id: str, org_id: str) -> MemberRole | None:
    role = conn.execute(
        sa.select(memberships.c.role).where(
            memberships.c.user_id == user_id, memberships.c.org_id == org_id
        )
    ).scalar()
    return MemberRole(role) if role else None


def require_project(
    conn: Connection, user: CurrentUser, project_id: str, min_role: MemberRole = MemberRole.viewer
) -> tuple[str, MemberRole]:
    """Resolve project -> org membership. Non-members get 404 (no existence leak)."""
    org_id = conn.execute(sa.select(projects.c.org_id).where(projects.c.id == project_id)).scalar()
    role = org_role(conn, user.id, org_id) if org_id else None
    if org_id is None or role is None:
        raise not_found("project")
    if _ROLE_RANK[role] < _ROLE_RANK[min_role]:
        raise ApiError(ErrorCode.FORBIDDEN, f"requires {min_role.value} role", 403)
    return org_id, role


class RateLimiter:
    """In-process sliding-window limiter (docs/13: no Redis for MVP; per API process)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_s: float) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                raise ApiError(
                    ErrorCode.RATE_LIMITED,
                    "rate limit exceeded",
                    429,
                    {"retry_after_s": int(window_s - (now - q[0])) + 1},
                )
            q.append(now)


limiter = RateLimiter()


def idempotent_lookup(
    conn: Connection, user_id: str, key: str | None, body: Any
) -> dict[str, Any] | None:
    if not key:
        return None
    if len(key) > 200:
        raise ApiError(ErrorCode.VALIDATION_FAILED, "Idempotency-Key too long", 422)
    # A unique insert at store time cannot undo duplicate work already created.
    # Serialize lookup + work + store across API processes until this transaction
    # commits (or rolls back). Unrelated users/keys do not share a lock.
    lock_id = int.from_bytes(
        hashlib.sha256(json.dumps([user_id, key]).encode()).digest()[:8], signed=True
    )
    conn.execute(sa.text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    row = (
        conn.execute(
            sa.select(idempotency_keys).where(
                idempotency_keys.c.user_id == user_id, idempotency_keys.c.key == key
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    if row["request_hash"] != digest:
        raise ApiError(
            ErrorCode.IDEMPOTENCY_CONFLICT, "Idempotency-Key reused with a different request", 409
        )
    return {"status": row["response_status"], "body": row["response_body"]}


def idempotent_store(
    conn: Connection, user_id: str, key: str | None, body: Any, status: int, response: Any
) -> None:
    if not key:
        return
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    conn.execute(
        sa.dialects.postgresql.insert(idempotency_keys)
        .values(
            user_id=user_id,
            key=key,
            request_hash=digest,
            response_status=status,
            response_body=response,
        )
        .on_conflict_do_nothing()
    )


def idempotent_replay(conn: Connection, user_id: str, key: str | None, request: Any) -> Any | None:
    """Call AFTER authorization: a stored response for (user, key) is replayed only to a
    caller allowed to make the request; a different request under the same key is 409."""
    from fastapi.responses import JSONResponse

    replay = idempotent_lookup(conn, user_id, key, request)
    return JSONResponse(replay["body"], status_code=replay["status"]) if replay else None


def idempotent_finish(
    conn: Connection, user_id: str, key: str | None, request: Any, status: int, result: Any
) -> Any:
    """Store the response for a retried key (same transaction as the work) and return it."""
    body = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    idempotent_store(conn, user_id, key, request, status, body)
    return result
