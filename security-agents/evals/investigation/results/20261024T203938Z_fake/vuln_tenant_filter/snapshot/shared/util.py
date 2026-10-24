"""Shared helpers for report-hub services."""

from __future__ import annotations

from fastapi import HTTPException, status

MAX_PAGE_LIMIT = 500


def clamp_limit(limit: int) -> int:
    """Page sizes are bounded so list endpoints never scan unbounded rows."""
    return max(1, min(limit, MAX_PAGE_LIMIT))


def require_tenant(resource_tenant: str, tenant: str) -> None:
    """A tenant may only access reports owned by that tenant. 404 avoids leaking existence."""
    if resource_tenant != tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
