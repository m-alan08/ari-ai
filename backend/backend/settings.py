"""Environment configuration (.env.example documents every variable)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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
    # Empty preserves legacy monitoring; comma-separated GitHub logins or * enables per-commit runs.
    github_auto_commit_authors: str = ""
    automatic_assessments_per_hour: int = Field(default=5, ge=1)
    automatic_assessments_per_day: int = Field(default=20, ge=1)
    automatic_usd_per_day: float = Field(default=50.0, gt=0)  # all projects together
    webhook_max_bytes: int = 1_048_576

    # Workers
    worker_lease_s: int = 60
    worker_poll_interval_s: float = 1.0
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
    assessment_starts_per_hour: int = 5

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
