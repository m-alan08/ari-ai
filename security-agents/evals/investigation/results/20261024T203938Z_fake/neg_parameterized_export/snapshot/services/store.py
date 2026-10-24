"""In-memory store for report-hub."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Report:
    id: str
    tenant: str
    title: str
    status: str


@dataclass
class Store:
    reports: dict[str, Report] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)  # bearer token -> tenant


def seeded_store() -> Store:
    s = Store()
    s.tokens = {"fixture-token-tenant-a": "tenant_a", "fixture-token-tenant-b": "tenant_b"}
    s.reports = {
        "rep_a1": Report("rep_a1", "tenant_a", "Weekly revenue", "ready"),
        "rep_a2": Report("rep_a2", "tenant_a", "Churn cohort", "draft"),
        "rep_b1": Report("rep_b1", "tenant_b", "Usage summary", "ready"),
    }
    return s
