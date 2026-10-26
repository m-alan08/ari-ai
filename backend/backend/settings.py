"""Environment configuration (.env.example documents every variable)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "info"
    # auto: json outside development/test, text in dev. json = one object per line, redacted.
    log_format: Literal["auto", "text", "json"] = "auto"
    api_base_url: str = "http://localhost:8000"
    frontend_origin: str = "http://localhost:3000"

    # Operations surface
    # Bearer token for GET /api/v1/ops/status. Unset disables the endpoint outside dev/test.
    ops_token: SecretStr | None = None
    # Generic HTTPS webhook for critical-failure alerts (Slack-compatible {"text": ...}).
    alert_webhook_url: SecretStr | None = None
    # Controlled feature flags: comma-separated names, off unless listed. See flags().
    feature_flags: str = ""

    database_url: str = "postgresql+psycopg://app:app@localhost:5432/hackatl"
    migration_database_url: str | None = None
    artifact_store_uri: str = "file://./artifacts"

    session_secret: SecretStr = SecretStr("")
    session_ttl_s: int = 7 * 24 * 3600
    auth_dev_login_enabled: bool = False

    # Anthropic / LLM gateway
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-haiku-5-5"
    anthropic_model_fast: str | None = None
    agent_model_overrides: str = ""
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    llm_server_fallbacks: bool = True
    llm_max_tokens_per_assessment: int = 400_000
    llm_max_usd_per_assessment: float = 10.0
    llm_request_timeout_s: float = 600.0
    llm_max_retries: int = 4

    # GitHub
    github_app_id: str | None = None
    # A file path, or (forgiving a common dashboard mistake) the PEM contents themselves.
    # Secret-typed either way so a pasted key never appears in a repr or log.
    github_app_private_key_path: SecretStr | None = None
    # PEM contents, for hosts that pass secrets as environment variables (Railway).
    # Literal "\n" sequences are accepted for single-line dashboards. The path wins if both are set.
    github_app_private_key: SecretStr | None = None
    github_webhook_secret: SecretStr | None = None
    github_oauth_client_id: str | None = None
    github_oauth_client_secret: SecretStr | None = None
    github_app_slug: str | None = None  # github.com/apps/<slug>: link for installing the App
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"
    # Empty or * preserves impact monitoring; a non-empty comma list enables per-commit runs.
    github_auto_commit_authors: str = ""
    project_assessments_per_hour: int = Field(default=10, ge=1)
    project_assessments_per_day: int = Field(default=50, ge=1)
    assessments_usd_per_day: float = Field(default=50.0, gt=0)
    webhook_max_bytes: int = 1_048_576

    # Deployment identity (set by the platform, reported by /healthz, /readyz, /diagnostics).
    # Railway injects RAILWAY_GIT_COMMIT_SHA; DEPLOYED_GIT_SHA works anywhere else.
    deployed_git_sha: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DEPLOYED_GIT_SHA", "RAILWAY_GIT_COMMIT_SHA", "GIT_SHA"),
    )

    # Workers
    worker_lease_s: int = 60
    worker_poll_interval_s: float = 1.0
    # Execution reliability (docs/architecture/execution-reliability.md). 0 disables a limit.
    job_max_running_per_project: int = 4
    job_max_running_global: int = 0
    job_max_pending_per_project: int = 1000
    # Reserved running slots for emergency-class jobs (estop/rollback/expiry) so
    # ordinary work cannot starve them (F2).
    job_max_running_emergency: int = 2
    worker_reconcile_interval_s: float = 30.0
    worker_stale_s: int = 120
    job_stuck_pending_s: int = 900
    worker_finalize_retries: int = 3
    # Worker liveness for /readyz: each worker upserts a heartbeat row; a heartbeat older
    # than this is stale. None → require a live worker only in production/staging.
    worker_heartbeat_interval_s: float = 5.0
    worker_liveness_s: float = 30.0
    readyz_require_workers: bool | None = None
    orchestrator_factory: str = Field(
        default="security_agents.api:build_orchestrator",
        description="module:callable returning an Orchestrator; see backend/host.py",
    )
    code_intelligence_factory: str = "code_intelligence.api:build_service"

    # Hosting the security-agents runtime (security_agents host mode) and the reviewer
    repository_bindings_file: Path | None = Field(
        default=None,
        description="JSON list of {repo_id, local_path, subdir}: trusted local checkouts of owned repos",
    )
    security_sandbox: Literal["docker", "vercel", "local-dev"] = "docker"
    security_workdir: Path | None = None

    # RL
    rl_checkpoint_path: Path | None = None
    # Pinned SHA-256 of the checkpoint file. Verified BEFORE anything unpickles it; required
    # outside development/test (a sidecar manifest next to the file is not an integrity root).
    rl_checkpoint_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rl_topology_path: Path | None = None

    # Rate limits (in-process, docs/13)
    rate_limit_per_minute: int = 60
    rate_limit_read_per_minute: int = 600
    # Dev-login burst allowance per IP per 5-minute window. E2E stacks sign in
    # many distinct synthetic users from one address.
    auth_dev_login_burst: int = 120

    # Ops surface + storage maintenance (docs/deploy/POSTGRES_STORAGE.md)
    # Bearer token for /api/v1/ops/*; unset disables those endpoints entirely.
    ops_api_token: SecretStr | None = None
    # Additional token required for destructive maintenance requests.
    ops_destructive_token: SecretStr | None = None
    # Generic HTTPS webhook posted when storage crosses a threshold (e.g. a Slack
    # incoming webhook or any alert receiver that accepts a JSON POST).
    ops_alert_webhook_url: str | None = None
    # Provisioned size of the PostgreSQL volume in bytes (from the Railway dashboard).
    # 0 = unknown: the report still emits absolute usage but skips percentage alerts.
    postgres_volume_bytes: int = Field(default=0, ge=0)
    # Provisioned size of the app data volume, Railway sizeMB × 1,000,000.
    # 0 = skip data-volume alerts.
    data_volume_bytes: int = Field(default=0, ge=0)
    data_volume_path: Path = Path("/data")
    maintenance_enabled: bool = True
    maintenance_interval_s: int = Field(default=3600, ge=60)
    # True by default: the loop reports what it WOULD delete without deleting until an
    # operator reviews a report and sets RETENTION_DRY_RUN=false (or runs --apply).
    retention_dry_run: bool = True
    retention_batch_limit: int = Field(default=5000, ge=1, le=200_000)
    retention_jobs_days: int = Field(default=30, ge=1)
    retention_webhook_deliveries_days: int = Field(default=90, ge=1)
    retention_monitoring_events_days: int = Field(default=180, ge=1)
    retention_reassessment_requests_days: int = Field(default=180, ge=1)
    retention_runtime_windows_days: int = Field(default=30, ge=1)
    retention_idempotency_days: int = Field(default=7, ge=1)
    # Ready code-intelligence snapshots kept per repository (payload bytes are the bulk
    # of Postgres growth). Older snapshots beyond this count lose their payloads and
    # index_snapshots rows; the snapshots metadata row stays as history.
    retention_snapshots_per_repo: int = Field(default=5, ge=1)
    # Payloads for a ready snapshot are never pruned younger than this, and never while
    # a non-terminal assessment still references the snapshot.
    retention_snapshot_grace_days: int = Field(default=7, ge=1)
    retention_protect_assessment_snapshots: bool = True
    retention_assessment_snapshot_days: int = Field(default=30, ge=7)
    # Payloads written but never marked ready (failed/abandoned indexing) are pruned
    # after this many hours.
    retention_orphan_grace_hours: int = Field(default=24, ge=1)
    # 0 = disabled: assessment_events/agent_runs transcript rows are kept forever.
    # Set a day count only if transcript retention is acceptable; pilot evidence
    # protection keeps this off by default.
    retention_assessment_detail_days: int = Field(default=0, ge=0)
    # refs/ari/* refs kept per bare clone under SECURITY_WORKDIR/repos. Each fetched
    # commit leaves a ref behind; unbounded refs pin unbounded objects.
    retention_checkout_refs_keep: int = Field(default=50, ge=5)
    # Orphaned artifact files (on disk, no artifacts row) are removed after this age.
    retention_orphan_artifact_hours: int = Field(default=24, ge=1)

    @field_validator(
        "anthropic_api_key",
        "anthropic_model_fast",
        "github_app_id",
        "github_app_private_key_path",
        "github_app_private_key",
        "github_app_slug",
        "github_webhook_secret",
        "github_oauth_client_id",
        "github_oauth_client_secret",
        "rl_checkpoint_path",
        "rl_topology_path",
        "rl_checkpoint_sha256",
        "repository_bindings_file",
        "security_workdir",
        "ops_token",
        "alert_webhook_url",
        "ops_api_token",
        "ops_destructive_token",
        "ops_alert_webhook_url",
        mode="before",
    )
    @classmethod
    def _unset_placeholders(cls, value: object) -> object:
        """Empty values and .env.example placeholders ("replace-me…") mean "not configured"."""
        if isinstance(value, str):
            stripped = value.strip()
            # .env.example puts comments on the same line as empty assignments. Compose's
            # env parser keeps that comment as the value, which must still mean "unset".
            if not stripped or stripped.startswith(("replace-me", "#")):
                return None
        return value

    @property
    def is_dev(self) -> bool:
        return self.app_env in ("development", "test")

    @property
    def resolved_log_format(self) -> str:
        if self.log_format != "auto":
            return self.log_format
        return "text" if self.is_dev else "json"

    def flags(self) -> frozenset[str]:
        """Enabled feature flags (FEATURE_FLAGS=flag-a,flag-b). Unknown names are inert."""
        return frozenset(f.strip() for f in self.feature_flags.split(",") if f.strip())

    def feature_enabled(self, name: str) -> bool:
        return name in self.flags()

    def model_for(self, agent: str) -> str:
        if self.app_env in ("production", "staging"):
            return "claude-haiku-5-5"
        for item in filter(None, (p.strip() for p in self.agent_model_overrides.split(","))):
            name, _, model = item.partition("=")
            if name.strip() == agent and model.strip():
                return model.strip()
        return self.anthropic_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
