"""Organizations, users, projects, repositories, budgets and usage."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from shared.base import ShortText, StrictModel, UtcDatetime
from shared.ids import OrgId, ProjectId, RepositoryId, UserId


class MemberRole(StrEnum):
    owner = "owner"
    member = "member"
    viewer = "viewer"


class Budget(StrictModel):
    max_tokens: int = Field(default=400_000, ge=0)
    max_usd: float = Field(default=10.0, ge=0)
    max_wall_clock_s: int = Field(default=1800, ge=1)


class Usage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)
    usd: float = Field(default=0.0, ge=0)
    sandbox_seconds: float = Field(default=0.0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)


class TriggerPolicy(StrictModel):
    """Monitoring trigger policy (docs/10). Defaults match the MVP table."""

    enabled: bool = False
    min_interval_s: int = Field(default=600, ge=0, description="Coalescing window per repo")
    ignore_paths_only: list[ShortText] = Field(
        default_factory=lambda: ["tests/**", "docs/**", "**/*.md"],
        description="If every changed path matches these globs, record without assessment",
    )
    monitoring_budget: Budget = Field(
        default_factory=lambda: Budget(max_tokens=150_000, max_usd=3.0, max_wall_clock_s=900),
        # Smaller than an assessment's default_budget on purpose: a push-triggered run is
        # expected to stop at BUDGET_EXHAUSTED around the remediation phase. Owners that
        # want the whole seven-agent + remediation chain on every push must size this to
        # the full-run budget; remediation has no separate budget.
        description="Budget for one push-triggered monitoring run (smaller than default_budget by design)",
    )


class DeliveryMode(StrEnum):
    """How verified repairs reach the repository (ADR-0021)."""

    auto = "auto"  # commit straight to the default branch (falls back to a PR)
    pull_request = "pull_request"  # open a PR for the team to review and merge
    manual = "manual"  # a human approves each remediation; nothing is pushed


class ProjectSettings(StrictModel):
    default_budget: Budget = Field(default_factory=Budget)
    monitoring_policy: TriggerPolicy = Field(default_factory=TriggerPolicy)
    languages: list[ShortText] = Field(default_factory=lambda: ["python"])
    delivery_mode: DeliveryMode = DeliveryMode.auto
    delivery_authorized_by: UserId | None = Field(
        default=None,
        description="Owner on whose standing authorization Ari approves and delivers fixes; "
        "set by the server, never the client. Null on projects that predate delivery modes: "
        "they stay manual until an owner saves settings.",
    )


class Organization(StrictModel):
    id: OrgId
    name: ShortText
    created_at: UtcDatetime


class User(StrictModel):
    id: UserId
    github_login: ShortText
    display_name: ShortText | None = None
    created_at: UtcDatetime


class Membership(StrictModel):
    org_id: OrgId
    user_id: UserId
    role: MemberRole


class Project(StrictModel):
    id: ProjectId
    org_id: OrgId
    name: ShortText
    settings: ProjectSettings = Field(default_factory=ProjectSettings)
    created_at: UtcDatetime


class RepoProvider(StrEnum):
    github = "github"


class Repository(StrictModel):
    automatic_assessments: bool = False
    id: RepositoryId
    project_id: ProjectId
    provider: RepoProvider = RepoProvider.github
    full_name: str = Field(pattern=r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")
    default_branch: ShortText = "main"
    installation_id: int | None = Field(default=None, ge=1)
    disconnected_at: UtcDatetime | None = None
    created_at: UtcDatetime
