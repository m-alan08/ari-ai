"""Sign-in: GitHub OAuth (read:user) and an explicitly gated local dev login."""

from __future__ import annotations

import logging
import secrets
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.engine import Connection

from backend import audit
from backend.api.deps import (
    SESSION_COOKIE,
    ApiError,
    create_session,
    limiter,
    require_user,
    session_user,
)
from backend.api.github_repos import store_repo_access
from backend.db import memberships, organizations, sessions, transaction, users
from backend.github import user_repositories
from backend.settings import get_settings
from shared.api import DevLoginRequest, MembershipView, MeResponse
from shared.audit import ActorType
from shared.errors import ErrorCode
from shared.ids import IdPrefix, new_id
from shared.tenancy import MemberRole, Organization, User

log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])
STATE_COOKIE = "hackatl_oauth_state"


def _release_login(conn: Connection, user_id: str, login: str, org_id: str | None) -> None:
    """Detach a stale github_login from an account.

    GitHub logins are mutable: when the owner of ``login`` renames away, a different
    account (different immutable github_id) can later register the same name. The
    stored row still belongs to the original github_id, so identity must follow the
    id, not the name — the new holder of the login must never inherit this row.
    """
    conn.execute(
        sa.update(users).where(users.c.id == user_id).values(github_login=f"released:{user_id}")
    )
    if org_id:
        audit.record(
            conn,
            org_id=org_id,
            actor_type=ActorType.system,
            actor_id="auth",
            action="user.login_released",
            subject_type="user",
            subject_id=user_id,
            details={"released_login": login},
        )


def upsert_user(
    conn: Connection, login: str, display_name: str | None, github_id: int | None
) -> str:
    if github_id is not None:
        # Identity is anchored on the immutable github_id; the login is only a label.
        row = (
            conn.execute(
                sa.select(users.c.id, users.c.github_login, users.c.personal_org_id).where(
                    users.c.github_id == github_id
                )
            )
            .mappings()
            .first()
        )
        if row is not None:
            if row["github_login"] != login:
                holder = (
                    conn.execute(
                        sa.select(users.c.id, users.c.personal_org_id).where(
                            users.c.github_login == login
                        )
                    )
                    .mappings()
                    .first()
                )
                if holder is not None and holder["id"] != row["id"]:
                    _release_login(conn, holder["id"], login, holder["personal_org_id"])
                conn.execute(
                    sa.update(users).where(users.c.id == row["id"]).values(github_login=login)
                )
            return row["id"]
    row = (
        conn.execute(
            sa.select(users.c.id, users.c.github_id, users.c.personal_org_id).where(
                users.c.github_login == login
            )
        )
        .mappings()
        .first()
    )
    if row is not None:
        if github_id is not None and row["github_id"] is not None and row["github_id"] != github_id:
            # A different GitHub account now owns this login: never link it here.
            _release_login(conn, row["id"], login, row["personal_org_id"])
        else:
            if github_id is not None and row["github_id"] is None:
                conn.execute(
                    sa.update(users).where(users.c.id == row["id"]).values(github_id=github_id)
                )
            return row["id"]
    user_id, org_id = new_id(IdPrefix.usr), new_id(IdPrefix.org)
    conn.execute(sa.insert(organizations).values(id=org_id, name=f"{login} (personal)"))
    conn.execute(
        sa.insert(users).values(
            id=user_id,
            github_login=login,
            github_id=github_id,
            display_name=display_name,
            personal_org_id=org_id,
        )
    )
    conn.execute(
        sa.insert(memberships).values(org_id=org_id, user_id=user_id, role=MemberRole.owner.value)
    )
    audit.record(
        conn,
        org_id=org_id,
        actor_type=ActorType.system,
        actor_id="auth",
        action="user.created",
        subject_type="user",
        subject_id=user_id,
        details={"github_login": login},
    )
    return user_id


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _audit_login(conn: Connection, user_id: str, method: str) -> None:
    org_id = conn.execute(sa.select(users.c.personal_org_id).where(users.c.id == user_id)).scalar()
    if not org_id:
        return
    audit.record(
        conn,
        org_id=org_id,
        actor_type=ActorType.user,
        actor_id=user_id,
        action="auth.login",
        subject_type="user",
        subject_id=user_id,
        details={"method": method},
    )


def _set_session_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=not s.is_dev,
        max_age=s.session_ttl_s,
        path="/",
    )


@router.post("/auth/dev/login", response_model=MeResponse)
def dev_login(body: DevLoginRequest, request: Request, response: Response) -> MeResponse:
    s = get_settings()
    if not (s.is_dev and s.auth_dev_login_enabled):
        raise ApiError(ErrorCode.FORBIDDEN, "dev login is disabled", 403)
    limiter.check(f"login:{_client_ip(request)}", s.auth_dev_login_burst, 300)
    with transaction() as conn:
        user_id = upsert_user(conn, body.github_login, body.display_name, None)
        token, csrf = create_session(conn, user_id)
        _audit_login(conn, user_id, "dev")
        _set_session_cookie(response, token)
        return _me(conn, user_id, csrf)


@router.get("/auth/github/login")
def github_login() -> Response:
    s = get_settings()
    if not s.github_oauth_client_id:
        raise ApiError(ErrorCode.SUBSYSTEM_UNAVAILABLE, "GitHub OAuth is not configured", 503)
    state = secrets.token_urlsafe(24)
    url = f"{s.github_web_url}/login/oauth/authorize?" + urlencode(
        {
            "client_id": s.github_oauth_client_id,
            "scope": "read:user",
            "state": state,
            "redirect_uri": f"{s.api_base_url}/api/v1/auth/github/callback",
        }
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        STATE_COOKIE, state, httponly=True, samesite="lax", secure=not s.is_dev, max_age=600
    )
    return resp


@router.get("/auth/github/callback")
def github_callback(
    request: Request, code: str = "", state: str = "", setup_action: str = ""
) -> Response:
    s = get_settings()
    if setup_action in ("install", "update") and not state:
        # Returning from installing the App: GitHub sends a code without our state. Never
        # trust it; start a normal sign-in instead, which refreshes the repository list.
        return RedirectResponse(f"{s.api_base_url}/api/v1/auth/github/login", status_code=302)
    limiter.check(f"oauth:{_client_ip(request)}", 30, 300)
    expected = request.cookies.get(STATE_COOKIE, "")
    public_host = urlsplit(s.api_base_url).netloc
    arrived_on = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    if not expected and public_host and arrived_on != public_host:
        # GitHub sent the browser to this backend's own domain (a GitHub App callback URL
        # that points here), but the state cookie lives on the public origin that started
        # sign-in. Bounce once to the public callback; the code is not used before that.
        return RedirectResponse(
            f"{s.api_base_url}/api/v1/auth/github/callback?{request.url.query}", status_code=302
        )
    if not code or not state or not secrets.compare_digest(state.encode(), expected.encode()):
        raise ApiError(ErrorCode.FORBIDDEN, "invalid OAuth state", 403)
    if not (s.github_oauth_client_id and s.github_oauth_client_secret):
        raise ApiError(ErrorCode.SUBSYSTEM_UNAVAILABLE, "GitHub OAuth is not configured", 503)
    with httpx.Client(timeout=15) as client:
        tok = client.post(
            f"{s.github_web_url}/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": s.github_oauth_client_id,
                "client_secret": s.github_oauth_client_secret.get_secret_value(),
                "code": code,
            },
        ).json()
        access = tok.get("access_token")
        if not access:
            raise ApiError(ErrorCode.UNAUTHENTICATED, "GitHub did not issue a token", 401)
        gh = client.get(
            f"{s.github_api_url}/user",
            headers={"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json"},
        ).json()
    login, gid = gh.get("login"), gh.get("id")
    if not isinstance(login, str):
        raise ApiError(ErrorCode.UNAUTHENTICATED, "GitHub user lookup failed", 401)
    # Which repositories this user can connect. A GitHub outage here must not block sign-in;
    # the previous list is kept and the picker says to sign in again.
    try:
        repos: list[dict[str, Any]] | None = user_repositories(access, settings=s)
    except httpx.HTTPError:
        log.warning("could not list GitHub App repositories for %s", login)
        repos = None
    with transaction() as conn:
        user_id = upsert_user(conn, login, gh.get("name"), gid if isinstance(gid, int) else None)
        if repos is not None:
            store_repo_access(conn, user_id, repos)
        token, _ = create_session(conn, user_id)
        _audit_login(conn, user_id, "github")
    # Straight to choosing an organization and repository.
    resp = RedirectResponse(f"{s.frontend_origin}/connect", status_code=302)
    _set_session_cookie(resp, token)
    resp.delete_cookie(STATE_COOKIE)
    return resp


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    with transaction() as conn:
        user = require_user(conn, request)
        conn.execute(sa.delete(sessions).where(sessions.c.token_hash == user.session_hash))
    response.delete_cookie(SESSION_COOKIE, path="/")


def _me(conn: Connection, user_id: str, csrf: str) -> MeResponse:
    u = conn.execute(sa.select(users).where(users.c.id == user_id)).mappings().one()
    rows = conn.execute(
        sa.select(organizations, memberships.c.role)
        .join(memberships, memberships.c.org_id == organizations.c.id)
        .where(memberships.c.user_id == user_id)
    ).mappings()
    return MeResponse(
        user=User(
            id=u["id"],
            github_login=u["github_login"],
            display_name=u["display_name"],
            created_at=u["created_at"],
        ),
        memberships=[
            MembershipView(
                organization=Organization(id=r["id"], name=r["name"], created_at=r["created_at"]),
                role=MemberRole(r["role"]),
            )
            for r in rows
        ],
        csrf_token=csrf,
    )


@router.get("/me", response_model=MeResponse)
def me(request: Request) -> MeResponse:
    with transaction() as conn:
        user = session_user(conn, request)
        if user is None:
            raise ApiError(ErrorCode.UNAUTHENTICATED, "sign in required", 401)
        return _me(conn, user.id, user.csrf_token)
