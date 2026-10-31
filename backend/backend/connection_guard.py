"""Re-check a repository's GitHub connection right before each GitHub write.

A job that writes to GitHub (fix delivery, build-recipe PRs, PR review comments) can run
for minutes after it was queued. In that window the owner may disconnect the repository
(`repositories.disconnected_at`) or GitHub may detach the App installation
(`repositories.installation_id` cleared or replaced by a reconnect). The initial check at
job start does not cover that, so handlers wrap every write-capable provider request
(minting a write token, pushing, opening a pull request, posting a review) in
`github_write`, which holds the repository's write lease and re-checks the connection
under it. A disconnect that has returned 200 is therefore never followed by a new request.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from backend.db import get_engine, repositories, transaction

# Per-repository write lease. Distinct from the other advisory-lock classes in the backend.
_GITHUB_WRITE_LOCK_CLASS = 6171
_LEASE = sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:repo_id, :klass))")
_LOCK_NOT_AVAILABLE = "55P03"


class RepositoryDetached(Exception):
    """The repository was disconnected or its installation detached after the job started."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def connection_problem(repo_id: str, installation_id: int) -> str | None:
    """None while `repo_id` is still connected through `installation_id`; else the reason."""
    with transaction() as conn:
        row = (
            conn.execute(
                sa.select(repositories.c.installation_id, repositories.c.disconnected_at).where(
                    repositories.c.id == repo_id
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        return "repository no longer exists"
    if row["disconnected_at"] is not None:
        return "repository is disconnected"
    if row["installation_id"] is None:
        return "GitHub App installation was detached"
    if int(row["installation_id"]) != installation_id:
        return "repository installation changed"
    return None


def require_connected(repo_id: str, installation_id: int) -> None:
    reason = connection_problem(repo_id, installation_id)
    if reason is not None:
        raise RepositoryDetached(reason)


class WriteInProgress(Exception):
    """Disconnect could not take the write lease before its timeout: a write is still running."""


@contextmanager
def github_write(repo_id: str, installation_id: int) -> Iterator[None]:
    """Hold the repository's write lease around one GitHub write (push, pull request, review).

    `POST /repositories/{id}/disconnect` takes the same lease before it sets
    `disconnected_at`, so the two serialize: either this write finishes before the
    disconnect commits, or the disconnect committed first and the re-check here skips the
    write. A plain re-check alone leaves a window between its commit and the write.
    The lease lives on its own connection and is released when the block exits.
    """
    with get_engine().begin() as lease:
        lease.execute(sa.text("SET LOCAL lock_timeout = '120s'"))
        lease.execute(_LEASE, {"repo_id": repo_id, "klass": _GITHUB_WRITE_LOCK_CLASS})
        require_connected(repo_id, installation_id)
        yield


def hold_github_writes(conn: Connection, repo_id: str, *, timeout_s: int = 20) -> None:
    """Disconnect side: wait for in-flight GitHub writes, then keep new ones out until commit."""
    conn.execute(sa.text(f"SET LOCAL lock_timeout = '{int(timeout_s)}s'"))
    try:
        conn.execute(_LEASE, {"repo_id": repo_id, "klass": _GITHUB_WRITE_LOCK_CLASS})
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE:
            raise WriteInProgress from exc
        raise
