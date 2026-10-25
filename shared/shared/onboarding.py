"""Customer onboarding contracts (additive, contract 0.24.0).

``OnboardingStatus`` is a *computed* view: the backend derives each step from the
same tables the product reads, so the wizard can never claim progress that did
not happen. ``OnboardingTestEvent`` records the result of the owner-triggered
test event, which travels the real ``owned_staging`` ingest path — synthetic
counters, real pipeline.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from shared.base import ShortText, StrictModel, UtcDatetime
from shared.ids import (
    ConnectedApplicationId,
    MonitoringEventId,
    OrgId,
    ProjectId,
)


class OnboardingStepId(StrEnum):
    """Ordered customer journey steps. The frontend renders them in this order."""

    sign_in = "sign_in"
    organization = "organization"
    repository = "repository"
    github_access = "github_access"
    snapshot = "snapshot"
    scope = "scope"
    application = "application"
    deploy_mapping = "deploy_mapping"
    monitoring = "monitoring"
    test_event = "test_event"
    evidence = "evidence"


class OnboardingStepState(StrEnum):
    done = "done"
    todo = "todo"  # a customer action is available now
    in_progress = "in_progress"  # the platform is working (e.g. indexing a snapshot)
    blocked = "blocked"  # waiting on another person or a prerequisite step
    failed = "failed"  # the last attempt failed; the customer may retry


class OnboardingStep(StrictModel):
    id: OnboardingStepId
    state: OnboardingStepState
    detail: ShortText = ""
    subject_id: ShortText | None = Field(
        default=None, description="Entity the step is about (repo, snapshot, app, ...)"
    )


class OnboardingStatus(StrictModel):
    project_id: ProjectId
    org_id: OrgId
    project_name: ShortText
    steps: list[OnboardingStep] = Field(min_length=1)
    complete: bool
    checked_at: UtcDatetime


class OnboardingTestEventCreate(StrictModel):
    """Owner-triggered test event: synthetic counters on one route of a registered
    staging application, ingested through the real ``owned_staging`` channel."""

    application_id: ConnectedApplicationId | None = Field(
        default=None,
        description="Required when the project has more than one connected application",
    )
    route: ShortText = "GET /ari/onboarding-test"
    requests: int = Field(default=100, ge=1, le=1_000_000)
    status_4xx: int = Field(default=80, ge=0)
    auth_failures: int = Field(default=80, ge=0)


class OnboardingTestEventResult(StrictModel):
    application_id: ConnectedApplicationId
    route: ShortText
    anomalies: int
    monitoring_event_ids: list[MonitoringEventId] = Field(default_factory=list)
    window_end: UtcDatetime
    source: Literal["owned_staging"] = "owned_staging"
    executed: Literal[False] = Field(
        default=False,
        description="Test events never execute enforcement; shadow output is recommendation-only",
    )
