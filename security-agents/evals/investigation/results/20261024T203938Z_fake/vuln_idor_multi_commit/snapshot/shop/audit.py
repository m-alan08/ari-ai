"""Audit helpers for shop-lite."""

from __future__ import annotations


def audit_trail(order_id: str) -> list[str]:
    """Placeholder audit log lookup."""
    return [f"order {order_id} viewed"]
