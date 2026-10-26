"""Monitoring events and reassessment requests (docs/10)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from shared.base import CommitSha, ShortText, StrictModel, UtcDatetime
from shared.ids import (
    ArtifactId,
    AssessmentId,
    IngestTokenId,
    MonitoringEventId,
    ProjectId,
    ReassessmentRequestId,
    RepositoryId,
)
from shared.intelligence import ImpactSet


class EventSource(StrEnum):
    github = "github"
    runtime = "runtime"
    internal = "internal"
    advisory = "advisory"


class EventType(StrEnum):
    push = "push"
    pull_request = "pull_request"
    log_anomaly = "log_anomaly"
    assessment_completed = "assessment_completed"
    advisory = "advisory"


class EventStatus(StrEnum):
    received = "received"
    processing = "processing"
    processed = "processed"
    ignored = "ignored"
    failed = "failed"


class CommitRange(StrictModel):
    base_sha: CommitSha | None = None
    head_sha: CommitSha


class MonitoringEvent(StrictModel):
    id: MonitoringEventId
    project_id: ProjectId
    repo_id: RepositoryId | None = None
    source: EventSource
    type: EventType
    occurred_at: UtcDatetime
    received_at: UtcDatetime
    dedupe_key: ShortText
    payload_artifact_id: ArtifactId | None = None
    commit_range: CommitRange | None = None
    ref: ShortText | None = Field(default=None, description="git ref, e.g. refs/heads/main")
    pr_number: int | None = Field(default=None, ge=1)
    simulated: bool = Field(
        default=False, description="True for replayed/fixture events; UI must label them"
    )
    test_event: bool = Field(
        default=False,
        description=(
            "True for owner-authorized onboarding test events. They traverse the real "
            "authenticated ingest channel (simulated stays False) but customer-facing "
            "views must label them unmistakably as tests."
        ),
    )
    status: EventStatus
    status_reason: ShortText | None = None
    impact: ImpactSet | None = None


class ReassessmentScope(StrEnum):
    full = "full"
    targeted = "targeted"
    regression_only = "regression_only"


class ReassessmentStatus(StrEnum):
    queued = "queued"
    coalesced = "coalesced"
    started = "started"
    blocked_no_scope = "blocked_no_scope"
    skipped = "skipped"


class ReassessmentRequest(StrictModel):
    id: ReassessmentRequestId
    project_id: ProjectId
    repo_id: RepositoryId
    event_ids: list[MonitoringEventId] = Field(min_length=1)
    scope: ReassessmentScope
    head_sha: CommitSha
    targets: list[ShortText] = Field(default_factory=list)
    status: ReassessmentStatus
    reason: ShortText
    assessment_id: AssessmentId | None = None
    created_at: UtcDatetime


# --------------------------------------------------------------- runtime signals (docs/10 "S")

RuntimeSource = Literal["runtime_demo", "replay", "simulated", "drill", "owned_staging"]
"""Provenance label. There is NO production telemetry source in MVP.

* ``owned_staging`` — authenticated counters from an owner-registered sandbox/owned_test
  app (Live Defense Phase 3). Simulated is false; never labelled as live replay.
* ``runtime_demo`` — sandboxed demo target (also authenticated when ingested with a
  valid token; kept for Phase 2 compatibility).
* ``replay`` / ``simulated`` — recorded or synthetic windows.
* ``drill`` — attack-drill synthetic counters (backend.simulated_attack).

Each source has its own scoring history, so a drill never becomes the baseline of a
real reporter.
"""

AUTHENTICATED_RUNTIME_SOURCES: frozenset[str] = frozenset({"owned_staging", "runtime_demo"})
"""Sources whose ingest events are labelled authenticated (``simulated=False``)."""


PrincipalRef = Annotated[
    str,
    Field(
        pattern=r"^prn_[A-Za-z0-9_-]{4,64}$",
        description="Pseudonymous principal reference chosen by the reporter (e.g. an HMAC "
        "of its user id under a key the platform never sees); never an email or raw id",
    ),
]


class PrincipalCounts(StrictModel):
    """One principal's requests on one route in one window, as classified by the
    operator's own instrumentation: who the bearer token belongs to versus who owns the
    requested object, with the business's delegation grants applied (contracts 0.14)."""

    principal: PrincipalRef
    cross_tenant: int = Field(
        default=0, ge=0, description="reads of another tenant's object not covered by a grant"
    )
    delegated: int = Field(
        default=0, ge=0, description="reads of another tenant's object covered by a grant"
    )
    own: int = Field(
        default=0, ge=0, description="every other request (own tenant or no tenant object)"
    )


class RouteWindow(StrictModel):
    """Aggregated counters for one route over one window (no raw logs, no payloads)."""

    route: str = Field(pattern=r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /[^\s]{0,300}$")
    requests: int = Field(ge=0, le=10_000_000)
    status_4xx: int = Field(default=0, ge=0)
    status_5xx: int = Field(default=0, ge=0)
    auth_failures: int = Field(default=0, ge=0, description="401/403 responses")
    principals: list[PrincipalCounts] = Field(
        default_factory=list,
        max_length=500,
        description="optional per-principal counters from a reporter that detects principals",
    )

    @model_validator(mode="after")
    def _bounded(self) -> RouteWindow:
        if (
            self.status_4xx + self.status_5xx > self.requests
            or self.auth_failures > self.status_4xx
        ):
            raise ValueError("error counts exceed request counts")
        if len({p.principal for p in self.principals}) != len(self.principals):
            raise ValueError("duplicate principal in route window")
        if sum(p.cross_tenant + p.delegated + p.own for p in self.principals) > self.requests:
            raise ValueError("principal counts exceed request counts")
        return self


class RuntimeSignalBatch(StrictModel):
    source: RuntimeSource
    repo_id: RepositoryId | None = None
    window_start: UtcDatetime
    window_end: UtcDatetime
    routes: list[RouteWindow] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _window(self) -> RuntimeSignalBatch:
        seconds = (self.window_end - self.window_start).total_seconds()
        if not 1 <= seconds <= 3600:
            raise ValueError("window must be between 1 second and 1 hour")
        if len({r.route for r in self.routes}) != len(self.routes):
            raise ValueError("duplicate route in batch")
        return self


class RouteScore(StrictModel):
    """Explainable per-route score against that route's own recent history."""

    route: str
    source: RuntimeSource
    window_end: UtcDatetime
    request_rate_z: float
    error_ratio: float = Field(ge=0, le=1)
    error_ratio_delta: float
    auth_failure_ratio: float = Field(ge=0, le=1)
    auth_failure_ratio_delta: float
    history_windows: int = Field(ge=0)
    alert_rate: float = Field(ge=0, description="Number of triggered rules in this window")
    anomaly_score: float = Field(ge=0, le=1)
    reasons: list[ShortText] = Field(default_factory=list)


class RuntimeIngestAck(StrictModel):
    accepted_routes: int
    anomalies: int
    monitoring_event_ids: list[MonitoringEventId] = Field(default_factory=list)
    scores: list[RouteScore]


class IngestTokenCreated(StrictModel):
    id: IngestTokenId
    project_id: ProjectId
    token: str = Field(description="Shown once; only its SHA-256 is stored")
    created_at: UtcDatetime
