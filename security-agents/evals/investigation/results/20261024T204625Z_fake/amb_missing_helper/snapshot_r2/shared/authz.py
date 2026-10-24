"""Authorization helpers (implementation pending)."""

from __future__ import annotations


def check_report_access(resource_tenant: str, tenant: str) -> bool:
    """Whether tenant may read a report owned by resource_tenant."""
    raise NotImplementedError
