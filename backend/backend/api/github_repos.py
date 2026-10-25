"""Repository picker: a sign-in snapshot plus live refresh of personal App installations.

The list is captured at GitHub sign-in from the App's user-to-server token and stored
without the token (`github_repo_access`). The picker refreshes the signed-in user's
trusted personal installation with an App JWT; organization installations still rely on
the sign-in snapshot so GitHub can enforce user-to-server visibility.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Request
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from backend.api.deps import ApiError, require_project, require_user
from backend.db import github_repo_access, repositories, transaction, users
from backend.github import GitHubApp, GitHubNotConfigured
from backend.settings import get_settings
from shared.errors import ErrorCode

log = logging.getLogger(__name__)

router = APIRouter(tags=["github"])


def store_repo_access(conn: Connection, user_id: str, repos: list[dict[str, Any]]) -> None:
    """Replace the user's accessible-repository list with `repos`."""
    conn.execute(sa.delete(github_repo_access).where(github_repo_access.c.user_id == user_id))
    seen: set[str] = set()
    rows = []
    for r in repos:
        if r["full_name"] in seen:
            continue
        seen.add(r["full_name"])
        rows.append({"user_id": user_id, **r})
    if rows:
        conn.execute(sa.insert(github_repo_access), rows)


def can_connect(conn: Connection, user_id: str, full_name: str, installation_id: int) -> bool:
    return (
        conn.execute(
            sa.select(github_repo_access.c.full_name).where(
                github_repo_access.c.user_id == user_id,
                github_repo_access.c.full_name == full_name,
                github_repo_access.c.installation_id == installation_id,
            )
        ).first()
        is not None
    )


def verify_installation_access(full_name: str, installation_id: int) -> None:
    """Confirm with GitHub — right now — that `installation_id` still grants `full_name`.

    The sign-in snapshot (`can_connect`) goes stale when the installation was deleted, the
    repository was removed from it, or the App was reconfigured since the user signed in.
    When the App isn't configured (dev/test), the snapshot check stands alone.
    """
    try:
        app = GitHubApp()
    except GitHubNotConfigured:
        return
    try:
        names = {r["full_name"] for r in app.installation_repositories(installation_id)}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403, 404):
            raise ApiError(
                ErrorCode.FORBIDDEN,
                "this GitHub App installation no longer exists or no longer grants access — "
                "reinstall the App or grant it the repository, then try again",
                403,
            ) from exc
        raise ApiError(
            ErrorCode.SUBSYSTEM_UNAVAILABLE,
            "GitHub could not confirm the installation right now — try again",
            503,
        ) from exc
    except httpx.HTTPError as exc:
        raise ApiError(
            ErrorCode.SUBSYSTEM_UNAVAILABLE,
            "GitHub could not confirm the installation right now — try again",
            503,
        ) from exc
    if full_name not in names:
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "the GitHub App installation does not grant access to that repository — "
            "add it to the installation's repository list, then try again",
            403,
        )


def _refresh_personal_installation_repositories(
    user_id: str, login: str | None, github_id: int | None
) -> None:
    settings = get_settings()
    if (
        not isinstance(login, str)
        or not login.strip()
        or (github_id is None and settings.is_dev and settings.auth_dev_login_enabled)
    ):
        return

    try:
        app = GitHubApp()
        installation = app.user_installation(login)
        if installation is None:
            return
        account = installation.get("account")
        installation_id = installation.get("id")
        if (
            not isinstance(account, dict)
            or account.get("type") != "User"
            or type(installation_id) is not int
        ):
            return
        if github_id is not None:
            trusted = account.get("id") == github_id
        else:
            account_login = account.get("login")
            trusted = (
                isinstance(account_login, str) and account_login.casefold() == login.casefold()
            )
        if not trusted:
            return
        repositories_for_installation = app.installation_repositories(installation_id)
    except GitHubNotConfigured:
        log.warning(
            "could not refresh GitHub repositories for %s (GitHub App not configured)", login
        )
        return
    except httpx.HTTPError as exc:
        log.warning("could not refresh GitHub repositories for %s (%s)", login, type(exc).__name__)
        return

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for repo in repositories_for_installation:
        full_name = repo.get("full_name")
        if not isinstance(full_name, str) or full_name in seen:
            continue
        seen.add(full_name)
        rows.append(
            {
                "user_id": user_id,
                "full_name": full_name,
                "installation_id": installation_id,
                "default_branch": repo.get("default_branch") or "main",
                "private": bool(repo.get("private")),
            }
        )

    with transaction() as conn:
        conn.execute(
            sa.delete(github_repo_access).where(
                github_repo_access.c.user_id == user_id,
                github_repo_access.c.installation_id == installation_id,
            )
        )
        if rows:
            conn.execute(
                pg_insert(github_repo_access)
                .values(rows)
                .on_conflict_do_nothing(
                    index_elements=[
                        github_repo_access.c.user_id,
                        github_repo_access.c.full_name,
                    ]
                )
            )


@router.get("/github/repositories")
def list_accessible_repositories(request: Request) -> dict[str, Any]:
    """Repositories the user can connect, and whether each is already connected."""
    with transaction() as conn:
        user = require_user(conn, request)
        identity = (
            conn.execute(
                sa.select(users.c.github_login, users.c.github_id).where(users.c.id == user.id)
            )
            .mappings()
            .one_or_none()
        )
    if identity is not None:
        _refresh_personal_installation_repositories(
            user.id, identity["github_login"], identity["github_id"]
        )

    with transaction() as conn:
        rows = conn.execute(
            sa.select(github_repo_access, repositories.c.id.label("repo_id"))
            .join(
                repositories,
                repositories.c.full_name == github_repo_access.c.full_name,
                isouter=True,
            )
            .where(github_repo_access.c.user_id == user.id)
            .order_by(github_repo_access.c.full_name)
        ).mappings()
        items = [
            {
                "full_name": r["full_name"],
                "installation_id": r["installation_id"],
                "default_branch": r["default_branch"],
                "private": r["private"],
                "connected_repo_id": r["repo_id"],
            }
            for r in rows
        ]
    s = get_settings()
    slug = s.github_app_slug
    return {
        "items": items,
        "install_url": f"{s.github_web_url}/apps/{slug}/installations/new" if slug else None,
        "next_cursor": None,
    }


@router.get("/repositories/{repo_id}/commits")
def recent_commits(repo_id: str, request: Request) -> dict[str, Any]:
    """Recent commits on the default branch, read live through the GitHub App installation."""
    with transaction() as conn:
        user = require_user(conn, request)
        repo = conn.execute(sa.select(repositories).where(repositories.c.id == repo_id)).first()
        if repo is None:
            raise ApiError(ErrorCode.NOT_FOUND, "repository not found", 404)
        require_project(conn, user, repo.project_id)
    if repo.installation_id is None:
        return {
            "items": [],
            "next_cursor": None,
            "available": False,
            "reason": "Connected by name, without the GitHub App, so commits cannot be read.",
        }
    try:
        items = GitHubApp().recent_commits(
            repo.installation_id, repo.full_name, repo.default_branch
        )
    except GitHubNotConfigured as exc:
        raise ApiError(
            ErrorCode.SUBSYSTEM_UNAVAILABLE, "GitHub App is not configured", 503
        ) from exc
    except httpx.HTTPError as exc:
        log.warning("listing commits for %s failed: %s", repo.full_name, type(exc).__name__)
        raise ApiError(
            ErrorCode.SUBSYSTEM_UNAVAILABLE, "GitHub did not return commits", 503
        ) from exc
    return {"items": items, "next_cursor": None, "available": True, "reason": None}
