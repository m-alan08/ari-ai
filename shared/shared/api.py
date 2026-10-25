"""HTTP request/response DTOs for backend ``/api/v1`` that are not entities themselves."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from shared.assessment import AgentRun, Assessment
from shared.base import CommitSha, ShortText, StrictModel, UtcDatetime
from shared.findings import (
    AttackPath,
    Finding,
    HardeningRecommendation,
    Hypothesis,
    PatchCandidate,
    RemediationPlan,
    RootCause,
    ValidationResult,
    VerificationResult,
)
from shared.ids import FindingId, RepositoryId, ScopeId, SnapshotId
from shared.scope import ApprovalStatus, CheckCategory, ScopeTarget
from shared.standards import FindingStandards
from shared.tenancy import Budget, MemberRole, Organization, ProjectSettings, User


class HealthStatus(StrictModel):
    status: ShortText
    version: ShortText
    git_sha: ShortText | None = None
    schema_version: ShortText


class WorkerLiveness(StrictModel):
    """Live worker processes for /readyz (worker_heartbeats table)."""

    alive: int
    expected: int | None
    busy: int
    queued_jobs: int
    running_jobs: int
    freshest_heartbeat_age_s: float | None
    required: bool


class LlmCapability(StrictModel):
    mode: ShortText = Field(description="live | fake | unavailable | host")
    model: ShortText
    configured: bool


class GithubCapability(StrictModel):
    app_configured: bool
    oauth_configured: bool
    webhook_secret_configured: bool
    verified: dict[str, Any] | None = None


class SandboxCapability(StrictModel):
    mode: ShortText
    configured: bool
    detail: ShortText = ""
    sdk_importable: bool | None = None
    daemon_reachable: bool | None = None


class ArtifactStoreCapability(StrictModel):
    writable: bool
    detail: ShortText = ""


class ReadinessCapabilities(StrictModel):
    """Can this deployment actually run an assessment, each dependency named separately."""

    llm: LlmCapability
    github: GithubCapability
    sandbox: SandboxCapability
    artifact_store: ArtifactStoreCapability
    anthropic_key_configured: bool


class ReadinessStatus(StrictModel):
    status: ShortText = Field(description="ready | degraded | not_ready")
    database: bool
    queue: bool
    workers: WorkerLiveness
    capabilities: ReadinessCapabilities
    capabilities_ready: bool
    llm_mode: ShortText = Field(description="live | fake | unavailable")
    github_app_configured: bool
    webhook_secret_configured: bool
    git_sha: ShortText | None = None
    schema_version: ShortText


class DiagnosticsReport(ReadinessStatus):
    """GET /diagnostics: readiness plus live verification (GitHub /app, sandbox SDK,
    migration head, queue counts, worker rows). Ops tooling; values are metadata only."""

    migrations: dict[str, Any]
    jobs: dict[str, int]
    worker_rows: list[dict[str, Any]]
    generated_at: ShortText


class MembershipView(StrictModel):
    organization: Organization
    role: MemberRole


class MeResponse(StrictModel):
    user: User
    memberships: list[MembershipView]
    csrf_token: ShortText


class DevLoginRequest(StrictModel):
    github_login: str = Field(pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
    display_name: ShortText | None = None


class ProjectCreate(StrictModel):
    name: ShortText = Field(min_length=1)
    org_id: str | None = Field(default=None, description="Defaults to caller's personal org")
    settings: ProjectSettings | None = None


class ProjectUpdate(StrictModel):
    name: ShortText | None = None
    settings: ProjectSettings | None = None


class RepositoryConnect(StrictModel):
    full_name: str = Field(pattern=r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")
    default_branch: ShortText = "main"
    installation_id: int | None = Field(default=None, ge=1)


class RepositoryUpdate(StrictModel):
    automatic_assessments: bool


class SnapshotCreate(StrictModel):
    commit_sha: CommitSha


class ScopeCreate(StrictModel):
    name: ShortText
    targets: list[ScopeTarget] = Field(min_length=1, max_length=8)
    allowed_check_categories: list[CheckCategory] = Field(default_factory=list)
    expires_at: UtcDatetime
    notes: ShortText | None = None


class ApprovalDecision(StrictModel):
    decision: ApprovalStatus
    reason: ShortText | None = None

    @model_validator(mode="after")
    def _final(self) -> ApprovalDecision:
        if self.decision is ApprovalStatus.pending:
            raise ValueError("decision must be approved or declined")
        return self


class AssessmentCreate(StrictModel):
    scope_id: ScopeId
    repo_id: RepositoryId
    commit_sha: CommitSha | None = None
    snapshot_id: SnapshotId | None = None
    budget: Budget | None = None

    @model_validator(mode="after")
    def _target(self) -> AssessmentCreate:
        if not (self.commit_sha or self.snapshot_id):
            raise ValueError("commit_sha or snapshot_id is required")
        return self


class AcceptRisk(StrictModel):
    reason: ShortText = Field(min_length=3)


class AssessmentDetail(StrictModel):
    assessment: Assessment
    agent_runs: list[AgentRun]
    findings: list[Finding]


class PatchView(StrictModel):
    patch: PatchCandidate
    verification: VerificationResult | None = None


class WebhookAck(StrictModel):
    delivery_id: ShortText
    duplicate: bool
    accepted: bool
    job_id: str | None = None
    reason: ShortText | None = None


# ----------------------------------------------------- finding detail / report (0.8.0)


class HypothesisEntry(StrictModel):
    type: Literal["hypothesis"]
    record: Hypothesis


class ValidationResultEntry(StrictModel):
    type: Literal["validation_result"]
    record: ValidationResult


class AttackPathEntry(StrictModel):
    type: Literal["attack_path"]
    record: AttackPath


class RootCauseEntry(StrictModel):
    type: Literal["root_cause"]
    record: RootCause


class RemediationPlanEntry(StrictModel):
    type: Literal["remediation_plan"]
    record: RemediationPlan


class HardeningEntry(StrictModel):
    type: Literal["hardening"]
    record: HardeningRecommendation


FindingRecordEntry = Annotated[
    HypothesisEntry
    | ValidationResultEntry
    | AttackPathEntry
    | RootCauseEntry
    | RemediationPlanEntry
    | HardeningEntry,
    Field(discriminator="type"),
]


class FindingDetail(StrictModel):
    """``GET /findings/{id}``: the finding plus its typed evidence records, oldest first."""

    finding: Finding
    records: list[FindingRecordEntry]


class ReportFinding(StrictModel):
    finding: Finding
    patches: list[PatchView]


class ReportFindingStandards(StrictModel):
    finding_id: FindingId
    standards: FindingStandards | None = None


class ReportStandards(StrictModel):
    classification_only: Literal[True] = True
    findings: list[ReportFindingStandards]
    hardening: list[HardeningRecommendation]
    unscored_reason: ShortText


class AssessmentReport(StrictModel):
    """``GET /assessments/{id}/report``: assembled only from persisted records."""

    assessment: Assessment
    agent_runs: list[AgentRun]
    findings: list[ReportFinding]
    generated_at: UtcDatetime
    source: Literal["persisted records"] = "persisted records"
    standards: ReportStandards
