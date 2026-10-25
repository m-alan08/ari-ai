"""Installation lifecycle: keep `repositories` in sync with GitHub App uninstall/rename events.

A deleted installation or removed repository access leaves `repositories.installation_id`
pointing at a credential that no longer exists; a rename leaves `full_name` pointing at the
old name, so every later push is silently ignored. These webhook handlers reconcile both so
connected repositories honestly reflect what the GitHub App can still reach.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import audit
from backend.db import github_repo_access, repositories
from backend.services import project_org
from shared.audit import ActorType


def apply_lifecycle(conn: Connection, event_name: str, payload: dict[str, Any]) -> list[str]:
    """Apply a GitHub lifecycle event to connected repositories; returns human notes."""
    installation = payload.get("installation") or {}
    installation_id = installation.get("id")
    action = payload.get("action") or ""

    if event_name == "installation" and action == "deleted" and installation_id:
        return _detach(conn, int(installation_id), None, "installation deleted")
    if event_name == "installation_repositories" and action == "removed" and installation_id:
        removed = [
            repo.get("full_name")
            for repo in (payload.get("repositories_removed") or [])
            if repo.get("full_name")
        ]
        return _detach(conn, int(installation_id), removed, "removed from installation")
    if event_name == "repository" and action == "deleted":
        repo = payload.get("repository") or {}
        if repo.get("full_name"):
            return _detach(conn, None, [repo["full_name"]], "repository deleted")
    if event_name == "repository" and action in ("renamed", "transferred"):
        return _renamed(conn, installation_id, payload)
    return []


def _detach(
    conn: Connection,
    installation_id: int | None,
    full_names: list[str] | None,
    why: str,
) -> list[str]:
    """Clear installation_id so reads/clones stop using a dead credential."""
    stmt = sa.update(repositories).values(installation_id=None)
    if installation_id is not None:
        stmt = stmt.where(repositories.c.installation_id == installation_id)
    else:
        stmt = stmt.where(repositories.c.installation_id.is_not(None))
    if full_names is not None:
        stmt = stmt.where(repositories.c.full_name.in_(full_names))
    rows = conn.execute(
        stmt.returning(repositories.c.id, repositories.c.full_name, repositories.c.project_id)
    ).all()
    for row in rows:
        audit.record(
            conn,
            org_id=project_org(conn, row.project_id),
            project_id=row.project_id,
            actor_type=ActorType.system,
            actor_id="github",
            action="repository.installation_detached",
            subject_type="repository",
            subject_id=row.id,
            details={"installation_id": installation_id, "reason": why},
        )
    return [f"{row.full_name}: {why}" for row in rows]


def _renamed(conn: Connection, installation_id: int | None, payload: dict[str, Any]) -> list[str]:
    """Follow a rename/transfer so pushes keep matching the connected repository."""
    repo = payload.get("repository") or {}
    new_name = repo.get("full_name")
    changes = payload.get("changes") or {}
    # renamed: changes.repository.name.from is the old repo NAME (owner unchanged);
    # transferred: changes.owner.from is the old OWNER object (repo name unchanged).
    old_short = (((changes.get("repository") or {}).get("name") or {}).get("from")) or ""
    old_full = ""
    if old_short:
        owner = (repo.get("owner") or {}).get("login")
        if owner:
            old_full = f"{owner}/{old_short}"
    if not old_full:
        # transferred: changes.owner.from is {"organization"|"user": {"login": ...}}
        from_obj = (changes.get("owner") or {}).get("from") or {}
        owner = None
        if isinstance(from_obj, dict):
            owner = from_obj.get("login")
            for nested in from_obj.values():
                if owner is None and isinstance(nested, dict):
                    owner = nested.get("login")
        if owner and repo.get("name"):
            old_full = f"{owner}/{repo['name']}"
    if not new_name or not old_full or new_name == old_full:
        return []
    if conn.execute(
        sa.select(repositories.c.id).where(repositories.c.full_name == new_name)
    ).scalar():
        # Another project already connected the target name; a retry hits this check first.
        return [f"{old_full} renamed to {new_name} but that name is already connected"]
    row = (
        conn.execute(
            sa.update(repositories)
            .where(
                repositories.c.full_name == old_full,
                repositories.c.installation_id == installation_id,
            )
            .values(full_name=new_name)
            .returning(repositories.c.id, repositories.c.full_name, repositories.c.project_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        return []
    conn.execute(
        sa.update(github_repo_access)
        .where(
            github_repo_access.c.installation_id == installation_id,
            github_repo_access.c.full_name == old_full,
        )
        .values(full_name=new_name)
    )
    audit.record(
        conn,
        org_id=project_org(conn, row["project_id"]),
        project_id=row["project_id"],
        actor_type=ActorType.system,
        actor_id="github",
        action="repository.renamed",
        subject_type="repository",
        subject_id=row["id"],
        details={"from": old_full, "to": new_name},
    )
    return [f"{old_full} renamed to {new_name}"]
