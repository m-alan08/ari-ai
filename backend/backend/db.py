"""PostgreSQL schema (SQLAlchemy Core) and engine helpers.

Entity tables keep queryable columns plus a ``doc`` JSONB column holding the full
``shared`` contract model as JSON. ``shared`` stays the single source of truth for
shape; every write validates through the Pydantic model first.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, Engine

metadata = sa.MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

TS = sa.DateTime(timezone=True)


def _id(name: str = "id") -> sa.Column:
    return sa.Column(name, sa.String(80), primary_key=True)


def _created() -> sa.Column:
    return sa.Column("created_at", TS, nullable=False, server_default=sa.func.now())


organizations = sa.Table(
    "organizations",
    metadata,
    _id(),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("audit_seq", sa.BigInteger, nullable=False, server_default="0"),
    sa.Column("audit_head", sa.String(64), nullable=False, server_default="0" * 64),
    _created(),
)

users = sa.Table(
    "users",
    metadata,
    _id(),
    sa.Column("github_login", sa.String(80), nullable=False, unique=True),
    sa.Column("github_id", sa.BigInteger, unique=True),
    sa.Column("display_name", sa.String(200)),
    sa.Column("personal_org_id", sa.String(80), sa.ForeignKey("organizations.id")),
    _created(),
)

memberships = sa.Table(
    "memberships",
    metadata,
    sa.Column("org_id", sa.String(80), sa.ForeignKey("organizations.id"), primary_key=True),
    sa.Column("user_id", sa.String(80), sa.ForeignKey("users.id"), primary_key=True),
    sa.Column("role", sa.String(20), nullable=False),
    sa.CheckConstraint("role in ('owner','member','viewer')", name="role"),
)

sessions = sa.Table(
    "sessions",
    metadata,
    sa.Column("token_hash", sa.String(64), primary_key=True),
    sa.Column("user_id", sa.String(80), sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("csrf_token", sa.String(64), nullable=False),
    sa.Column("expires_at", TS, nullable=False),
    _created(),
)

# Repositories a user can reach through the GitHub App, refreshed at each GitHub sign-in.
# Connecting a repository with an installation_id requires a matching row (no token stored).
github_repo_access = sa.Table(
    "github_repo_access",
    metadata,
    sa.Column("user_id", sa.String(80), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("full_name", sa.String(200), nullable=False),
    sa.Column("installation_id", sa.BigInteger, nullable=False),
    sa.Column("default_branch", sa.String(200), nullable=False),
    sa.Column("private", sa.Boolean, nullable=False),
    sa.Column("refreshed_at", TS, nullable=False, server_default=sa.func.now()),
    sa.PrimaryKeyConstraint("user_id", "full_name"),
)

projects = sa.Table(
    "projects",
    metadata,
    _id(),
    sa.Column(
        "org_id", sa.String(80), sa.ForeignKey("organizations.id"), nullable=False, index=True
    ),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("settings", JSONB, nullable=False),
    _created(),
)

repositories = sa.Table(
    "repositories",
    metadata,
    sa.Column("automatic_assessments", sa.Boolean, nullable=False, server_default=sa.false()),
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column("provider", sa.String(20), nullable=False, server_default="github"),
    sa.Column("full_name", sa.String(200), nullable=False),
    sa.Column("default_branch", sa.String(200), nullable=False),
    sa.Column("installation_id", sa.BigInteger),
    _created(),
    sa.UniqueConstraint("provider", "full_name", name="uq_repositories_provider_full_name"),
)

snapshots = sa.Table(
    "snapshots",
    metadata,
    _id(),
    sa.Column("repo_id", sa.String(80), sa.ForeignKey("repositories.id"), nullable=False),
    sa.Column("commit_sha", sa.String(40), nullable=False),
    sa.Column("index_version", sa.String(80), nullable=False),
    sa.Column("status", sa.String(20), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
    sa.UniqueConstraint(
        "repo_id", "commit_sha", "index_version", name="uq_snapshots_repo_sha_version"
    ),
)

authorized_scopes = sa.Table(
    "authorized_scopes",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column("status", sa.String(20), nullable=False),
    sa.Column("expires_at", TS, nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

approvals = sa.Table(
    "approvals",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column("subject_type", sa.String(20), nullable=False),
    sa.Column("subject_id", sa.String(80), nullable=False),
    sa.Column("assessment_id", sa.String(80), index=True),
    sa.Column("status", sa.String(20), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

assessments = sa.Table(
    "assessments",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column("repo_id", sa.String(80), sa.ForeignKey("repositories.id"), nullable=False),
    sa.Column("scope_id", sa.String(80), sa.ForeignKey("authorized_scopes.id"), nullable=False),
    sa.Column("commit_sha", sa.String(40), nullable=False),
    sa.Column("trigger", sa.String(20), nullable=False),
    sa.Column("reassessment_request_id", sa.String(80), unique=True),
    sa.Column("state", sa.String(40), nullable=False),
    sa.Column("event_seq", sa.BigInteger, nullable=False, server_default="0"),
    sa.Column("cancel_requested", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
    sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
)

assessment_events = sa.Table(
    "assessment_events",
    metadata,
    sa.Column("assessment_id", sa.String(80), sa.ForeignKey("assessments.id"), primary_key=True),
    sa.Column("seq", sa.BigInteger, primary_key=True),
    sa.Column("type", sa.String(60), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

agent_runs = sa.Table(
    "agent_runs",
    metadata,
    _id(),
    sa.Column(
        "assessment_id", sa.String(80), sa.ForeignKey("assessments.id"), nullable=False, index=True
    ),
    sa.Column("agent", sa.String(20), nullable=False),
    sa.Column("status", sa.String(20), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

findings = sa.Table(
    "findings",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column(
        "assessment_id", sa.String(80), sa.ForeignKey("assessments.id"), nullable=False, index=True
    ),
    sa.Column("kind", sa.String(20), nullable=False),
    sa.Column("status", sa.String(30), nullable=False),
    sa.Column("paths", JSONB, nullable=False, server_default="[]"),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

finding_records = sa.Table(
    # Hypothesis / ValidationResult / AttackPath / RootCause / RemediationPlan / Hardening
    "finding_records",
    metadata,
    _id(),
    sa.Column(
        "finding_id", sa.String(80), sa.ForeignKey("findings.id"), nullable=False, index=True
    ),
    sa.Column("record_type", sa.String(40), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

patch_candidates = sa.Table(
    "patch_candidates",
    metadata,
    _id(),
    sa.Column(
        "finding_id", sa.String(80), sa.ForeignKey("findings.id"), nullable=False, index=True
    ),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

verification_results = sa.Table(
    "verification_results",
    metadata,
    _id(),
    sa.Column(
        "patch_id", sa.String(80), sa.ForeignKey("patch_candidates.id"), nullable=False, index=True
    ),
    sa.Column("verdict", sa.String(20), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

artifacts = sa.Table(
    "artifacts",
    metadata,
    _id(),
    sa.Column("project_id", sa.String(80), sa.ForeignKey("projects.id"), index=True),
    sa.Column("kind", sa.String(40), nullable=False),
    sa.Column("sha256", sa.String(64), nullable=False, index=True),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("media_type", sa.String(120), nullable=False),
    sa.Column("storage_key", sa.String(300), nullable=False),
    sa.Column("redacted", sa.Boolean, nullable=False),
    _created(),
)

audit_events = sa.Table(
    "audit_events",
    metadata,
    _id(),
    sa.Column("org_id", sa.String(80), sa.ForeignKey("organizations.id"), nullable=False),
    sa.Column("seq", sa.BigInteger, nullable=False),
    sa.Column("project_id", sa.String(80), index=True),
    sa.Column("doc", JSONB, nullable=False),
    sa.Column("hash", sa.String(64), nullable=False),
    _created(),
    sa.UniqueConstraint("org_id", "seq", name="uq_audit_events_org_seq"),
)

jobs = sa.Table(
    "jobs",
    metadata,
    _id(),
    sa.Column("kind", sa.String(60), nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("status", sa.String(20), nullable=False, index=True),
    sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
    sa.Column("run_after", TS, nullable=False, server_default=sa.func.now()),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
    sa.Column("dedupe_key", sa.String(200), unique=True),
    sa.Column("lease_owner", sa.String(120)),
    sa.Column("lease_expires_at", TS),
    sa.Column("heartbeat_at", TS),
    sa.Column("cancel_requested", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("last_error", JSONB),
    sa.Column("result", JSONB),
    _created(),
    sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    # 0016 execution reliability (docs/architecture/execution-reliability.md)
    sa.Column("project_id", sa.String(80)),
    sa.Column("depends_on", sa.String(80)),
    sa.Column("defer_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("deadline_at", TS),
    sa.Column("started_at", TS),
    sa.Column("first_started_at", TS),
    sa.Column("finished_at", TS),
    sa.Column("effect_state", sa.String(20)),
    sa.Column("effect_key", sa.String(200)),
    sa.Column("error_category", sa.String(40)),
    sa.Column("recovery", JSONB),
    sa.CheckConstraint(
        "status in ('queued','waiting','running','succeeded','failed','cancelled','dead',"
        "'timed_out','recovery_required')",
        name="status",
    ),
    sa.CheckConstraint(
        "effect_state is null or effect_state in ('in_flight','settled')", name="effect_state"
    ),
)

job_events = sa.Table(
    "job_events",
    metadata,
    sa.Column("seq", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column(
        "job_id", sa.String(80), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("project_id", sa.String(80)),
    sa.Column("kind", sa.String(60), nullable=False),
    sa.Column("from_status", sa.String(20)),
    sa.Column("to_status", sa.String(20), nullable=False),
    sa.Column("attempt", sa.Integer, nullable=False),
    sa.Column("worker", sa.String(120)),
    sa.Column("category", sa.String(40)),
    sa.Column("reason", sa.Text),
    sa.Column("details", JSONB),
    _created(),
)

worker_heartbeats = sa.Table(
    "worker_heartbeats",
    metadata,
    sa.Column("worker_id", sa.String(120), primary_key=True),
    sa.Column("state", sa.String(20), nullable=False),
    sa.Column("current_job_id", sa.String(80)),
    sa.Column("jobs_finished", sa.Integer, nullable=False, server_default="0"),
    sa.Column("started_at", TS, nullable=False, server_default=sa.func.now()),
    sa.Column("last_seen_at", TS, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("state in ('idle','busy','stopping')", name="state"),
)
sa.Index("ix_jobs_claim", jobs.c.status, jobs.c.priority, jobs.c.run_after)

webhook_deliveries = sa.Table(
    "webhook_deliveries",
    metadata,
    sa.Column("delivery_id", sa.String(100), primary_key=True),
    sa.Column("source", sa.String(20), nullable=False),
    sa.Column("event_name", sa.String(60), nullable=False),
    sa.Column("payload_artifact_id", sa.String(80), sa.ForeignKey("artifacts.id")),
    sa.Column("payload_sha256", sa.String(64), nullable=False),
    sa.Column("job_id", sa.String(80)),
    sa.Column("received_at", TS, nullable=False, server_default=sa.func.now()),
)

monitoring_events = sa.Table(
    "monitoring_events",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column("repo_id", sa.String(80), sa.ForeignKey("repositories.id")),
    sa.Column("dedupe_key", sa.String(200), nullable=False, unique=True),
    sa.Column("status", sa.String(20), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

reassessment_requests = sa.Table(
    "reassessment_requests",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column("repo_id", sa.String(80), sa.ForeignKey("repositories.id"), nullable=False),
    sa.Column("status", sa.String(30), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

live_policy_decisions = sa.Table(
    "live_policy_decisions",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column("application_id", sa.String(80), sa.ForeignKey("connected_applications.id")),
    sa.Column("window_end", TS, nullable=False),
    sa.Column("source", sa.String(20), nullable=False),
    sa.Column("producer", sa.String(20), nullable=False),
    sa.Column("inference_ran", sa.Boolean(), nullable=False),
    sa.Column("dedupe_key", sa.String(400), nullable=False, unique=True),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
    sa.CheckConstraint("producer in ('rules','v6_shadow','abstention')", name="producer"),
)

defense_recommendations = sa.Table(
    "defense_recommendations",
    metadata,
    _id(),
    sa.Column("project_id", sa.String(80), sa.ForeignKey("projects.id"), index=True),
    sa.Column("doc", JSONB, nullable=False),
    sa.Column("explanation", JSONB),
    _created(),
)

rl_evaluations = sa.Table(
    "rl_evaluations",
    metadata,
    _id(),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

# Code-intelligence PayloadStore (backend/intel_store.py): immutable, SnapshotRef-keyed.
index_payloads = sa.Table(
    "index_payloads",
    metadata,
    sa.Column("project_id", sa.String(80), sa.ForeignKey("projects.id"), primary_key=True),
    sa.Column("repo_id", sa.String(80), sa.ForeignKey("repositories.id"), primary_key=True),
    sa.Column("commit_sha", sa.String(40), primary_key=True),
    sa.Column("index_version", sa.String(80), primary_key=True),
    sa.Column("kind", sa.String(80), primary_key=True),
    sa.Column("key", sa.String(300), primary_key=True),
    sa.Column("snapshot_id", sa.String(80), nullable=False, index=True),
    sa.Column("sha256", sa.String(64), nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("data", sa.LargeBinary, nullable=False),
    _created(),
)

index_snapshots = sa.Table(
    "index_snapshots",
    metadata,
    sa.Column("project_id", sa.String(80), sa.ForeignKey("projects.id"), primary_key=True),
    sa.Column("repo_id", sa.String(80), sa.ForeignKey("repositories.id"), primary_key=True),
    sa.Column("commit_sha", sa.String(40), primary_key=True),
    sa.Column("index_version", sa.String(80), primary_key=True),
    sa.Column("snapshot_id", sa.String(80), nullable=False, unique=True),
    sa.Column("stats", JSONB, nullable=False),
    _created(),
)

connected_applications = sa.Table(
    "connected_applications",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("environment", sa.String(20), nullable=False),
    sa.Column("created_by", sa.String(80), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
    sa.CheckConstraint("environment in ('sandbox','owned_test')", name="environment"),
)

ingest_tokens = sa.Table(
    "ingest_tokens",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column("token_sha256", sa.String(64), nullable=False, unique=True),
    sa.Column("created_by", sa.String(80), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("revoked_at", TS),
    sa.Column("last_used_at", TS),
    sa.Column("application_id", sa.String(80), sa.ForeignKey("connected_applications.id")),
    _created(),
)

runtime_windows = sa.Table(
    "runtime_windows",
    metadata,
    _id(),
    sa.Column("project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False),
    sa.Column("source", sa.String(20), nullable=False),
    sa.Column("route", sa.String(320), nullable=False),
    sa.Column("window_start", TS, nullable=False),
    sa.Column("window_end", TS, nullable=False),
    sa.Column("counters", JSONB, nullable=False),
    sa.Column("score", JSONB, nullable=False),
    sa.Column("application_id", sa.String(80), sa.ForeignKey("connected_applications.id")),
    _created(),
)
sa.Index(
    "uq_runtime_windows_key",
    runtime_windows.c.project_id,
    runtime_windows.c.source,
    runtime_windows.c.route,
    runtime_windows.c.window_end,
    sa.func.coalesce(runtime_windows.c.application_id, ""),
    unique=True,
)
sa.Index(
    "ix_runtime_windows_lookup",
    runtime_windows.c.project_id,
    runtime_windows.c.route,
    runtime_windows.c.window_end,
)

incidents = sa.Table(
    "incidents",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column("phase", sa.String(40), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

analyst_reviews = sa.Table(
    "analyst_reviews",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column(
        "recommendation_id",
        sa.String(80),
        sa.ForeignKey("defense_recommendations.id"),
        nullable=True,
        index=True,
    ),
    sa.Column(
        "live_policy_decision_id",
        sa.String(80),
        sa.ForeignKey("live_policy_decisions.id"),
        nullable=True,
        index=True,
    ),
    sa.Column("decision", sa.String(40), nullable=False),
    sa.Column("reviewed_by", sa.String(80), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
    sa.CheckConstraint("decision in ('accept','reject','needs_investigation')", name="decision"),
    sa.CheckConstraint(
        "(recommendation_id IS NOT NULL AND live_policy_decision_id IS NULL) "
        "OR (recommendation_id IS NULL AND live_policy_decision_id IS NOT NULL)",
        name="review_subject",
    ),
)

control_grants = sa.Table(
    "control_grants",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column(
        "application_id",
        sa.String(80),
        sa.ForeignKey("connected_applications.id"),
        nullable=False,
        index=True,
    ),
    sa.Column("revoked_at", TS),
    sa.Column("expires_at", TS),
    sa.Column("created_by", sa.String(80), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

control_channels = sa.Table(
    "control_channels",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column(
        "application_id",
        sa.String(80),
        sa.ForeignKey("connected_applications.id"),
        nullable=False,
        index=True,
    ),
    sa.Column("host", sa.String(16), nullable=False),
    sa.Column("port", sa.Integer, nullable=False),
    sa.Column("hmac_secret", sa.String(200), nullable=False),
    sa.Column("revoked_at", TS),
    sa.Column("created_by", sa.String(80), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
    sa.CheckConstraint("host = '127.0.0.1'", name="control_channel_host"),
    sa.CheckConstraint("port >= 1024 AND port <= 65535", name="control_channel_port"),
)
sa.Index(
    "uq_control_channels_active_app",
    control_channels.c.application_id,
    unique=True,
    postgresql_where=control_channels.c.revoked_at.is_(None),
)

live_controls = sa.Table(
    "live_controls",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column(
        "application_id",
        sa.String(80),
        sa.ForeignKey("connected_applications.id"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "live_policy_decision_id",
        sa.String(80),
        sa.ForeignKey("live_policy_decisions.id"),
        nullable=True,
    ),
    sa.Column("kind", sa.String(40), nullable=False),
    sa.Column("status", sa.String(40), nullable=False, index=True),
    sa.Column("unresolved", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("expires_at", TS),
    sa.Column("binding_sha256", sa.String(64), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
    sa.CheckConstraint(
        "kind in ('rate_limit','block_source','increase_logging','escalate')",
        name="live_control_kind",
    ),
    sa.CheckConstraint(
        "status in ('pending_authorization','authorized','executing','verifying',"
        "'verified','failed','expired','rolled_back','escalated','rejected',"
        "'emergency_stopped')",
        name="live_control_status",
    ),
)

emergency_stops = sa.Table(
    "emergency_stops",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column(
        "application_id",
        sa.String(80),
        sa.ForeignKey("connected_applications.id"),
        nullable=True,
    ),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.Column("requested_by", sa.String(80), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)
sa.Index(
    "uq_emergency_stops_active_project",
    emergency_stops.c.project_id,
    unique=True,
    postgresql_where=sa.and_(
        emergency_stops.c.active.is_(True), emergency_stops.c.application_id.is_(None)
    ),
)


first_responder_modes = sa.Table(
    "first_responder_modes",
    metadata,
    sa.Column("project_id", sa.String(80), sa.ForeignKey("projects.id"), primary_key=True),
    sa.Column("intent", sa.String(40), nullable=False),
    sa.Column("updated_by", sa.String(80), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("updated_at", TS, nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    sa.CheckConstraint(
        "intent in ('off','monitor','assisted_response')", name="first_responder_intent"
    ),
)

deploy_commit_mappings = sa.Table(
    "deploy_commit_mappings",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column(
        "application_id",
        sa.String(80),
        sa.ForeignKey("connected_applications.id"),
        nullable=False,
        index=True,
    ),
    sa.Column("repo_id", sa.String(80), sa.ForeignKey("repositories.id"), nullable=False),
    sa.Column("commit_sha", sa.String(40), nullable=False),
    sa.Column("revoked_at", TS),
    sa.Column("created_by", sa.String(80), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

first_responder_decisions = sa.Table(
    "first_responder_decisions",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column("application_id", sa.String(80), sa.ForeignKey("connected_applications.id")),
    sa.Column("dedupe_key", sa.String(400), nullable=False, unique=True),
    sa.Column("action", sa.String(40), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

first_responder_invocations = sa.Table(
    "first_responder_invocations",
    metadata,
    _id(),
    sa.Column(
        "decision_id",
        sa.String(80),
        sa.ForeignKey("first_responder_decisions.id"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column("agent", sa.String(20), nullable=False),
    sa.Column("status", sa.String(40), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)

analyst_handoffs = sa.Table(
    "analyst_handoffs",
    metadata,
    _id(),
    sa.Column(
        "project_id", sa.String(80), sa.ForeignKey("projects.id"), nullable=False, index=True
    ),
    sa.Column("application_id", sa.String(80), sa.ForeignKey("connected_applications.id")),
    sa.Column(
        "decision_id",
        sa.String(80),
        sa.ForeignKey("first_responder_decisions.id"),
        nullable=False,
        index=True,
    ),
    sa.Column("state", sa.String(40), nullable=False),
    sa.Column("confirmation", sa.String(20), nullable=False),
    sa.Column("created_by", sa.String(80), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("doc", JSONB, nullable=False),
    _created(),
)


idempotency_keys = sa.Table(
    "idempotency_keys",
    metadata,
    sa.Column("user_id", sa.String(80), primary_key=True),
    sa.Column("key", sa.String(200), primary_key=True),
    sa.Column("request_hash", sa.String(64), nullable=False),
    sa.Column("response_status", sa.Integer, nullable=False),
    sa.Column("response_body", JSONB, nullable=False),
    _created(),
)


@lru_cache(maxsize=8)
def _engine(url: str) -> Engine:
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return sa.create_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        connect_args={"options": "-c timezone=UTC"},  # timestamps come back in UTC
    )


def get_engine() -> Engine:
    from backend.settings import get_settings

    return _engine(get_settings().database_url)


@contextmanager
def transaction(engine: Engine | None = None) -> Iterator[Connection]:
    with (engine or get_engine()).begin() as conn:
        yield conn
