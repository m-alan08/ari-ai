"""Deployment preflight: fail fast on bad configuration or unmigrated databases.

    hackatl-preflight             # config validation + migration status (exit 1 on error)
    hackatl-preflight --config    # configuration only (no DB required)

The API and worker entrypoints run the same checks before serving, so a staging
deploy with a missing secret or a skipped migration stops at boot instead of
degrading in production. In development/test nothing here blocks startup.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

import sqlalchemy as sa
from alembic.script import ScriptDirectory

from backend.migrate import alembic_config
from backend.settings import Settings, get_settings

# Credentials that appear in docker-compose.yml / .env.example. They are fine on a
# developer laptop; they must never authenticate a staging or production database.
_LOCAL_DB_CREDENTIALS = ("app-local-only", "app:app@", "app:replace-me@")


@dataclass
class PreflightResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [f"[ERROR] {e}" for e in self.errors]
        lines += [f"[WARN]  {w}" for w in self.warnings]
        lines.append("preflight: FAIL" if self.errors else "preflight: PASS")
        return "\n".join(lines)


def _secret_value(value: object) -> str:
    if value is None:
        return ""
    get = getattr(value, "get_secret_value", None)
    return get() if callable(get) else str(value)


def validate_config(s: Settings, *, worker: bool = False) -> PreflightResult:
    """Static configuration checks. Errors only outside development/test."""
    r = PreflightResult()
    if s.is_dev:
        return r

    env = s.app_env
    if s.auth_dev_login_enabled:
        r.errors.append(f"AUTH_DEV_LOGIN_ENABLED=true must never reach {env}")
    if s.security_sandbox == "local-dev":
        r.errors.append(f"SECURITY_SANDBOX=local-dev must never reach {env}")
    if any(cred in s.database_url for cred in _LOCAL_DB_CREDENTIALS):
        r.errors.append(f"DATABASE_URL still carries local-only credentials in {env}")
    if not s.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        r.errors.append("DATABASE_URL is not a PostgreSQL URL")

    if not _secret_value(s.anthropic_api_key):
        r.errors.append(f"ANTHROPIC_API_KEY is required in {env}")
    if not s.github_app_id or not (
        _secret_value(s.github_app_private_key) or _secret_value(s.github_app_private_key_path)
    ):
        r.errors.append(f"GitHub App credentials are required in {env} (APP_ID + private key)")
    if not _secret_value(s.github_webhook_secret):
        r.errors.append(f"GITHUB_WEBHOOK_SECRET is required in {env}")
    if not (s.github_oauth_client_id and _secret_value(s.github_oauth_client_secret)):
        r.errors.append(f"GitHub OAuth client id/secret are required in {env} (user sign-in)")

    if not s.artifact_store_uri:
        r.errors.append("ARTIFACT_STORE_URI is empty; artifacts would not persist")
    elif s.artifact_store_uri.startswith("file://") and env == "production":
        r.warnings.append(
            "ARTIFACT_STORE_URI is a local path in production; confirm it is a mounted volume"
        )

    if not _secret_value(s.session_secret):
        r.warnings.append("SESSION_SECRET is unset (declared in .env.example for signed payloads)")
    if not _secret_value(s.ops_token):
        r.warnings.append("OPS_TOKEN unset: /api/v1/ops/status stays disabled in this environment")
    if not _secret_value(s.alert_webhook_url):
        r.warnings.append("ALERT_WEBHOOK_URL unset: no critical-failure alerting")
    for name in ("api_base_url", "frontend_origin"):
        if not getattr(s, name).startswith("https://"):
            r.warnings.append(f"{name.upper()} is not https ({getattr(s, name)})")

    if worker:
        if not s.rl_checkpoint_path:
            r.errors.append("RL_CHECKPOINT_PATH is required for workers outside dev/test")
        if not _secret_value(s.rl_checkpoint_sha256):
            r.errors.append("RL_CHECKPOINT_SHA256 is required for workers outside dev/test")
        if not s.rl_topology_path:
            r.errors.append("RL_TOPOLOGY_PATH is required for workers outside dev/test")
    return r


@dataclass
class MigrationStatus:
    current: list[str]
    expected: list[str]
    pending: list[str]

    @property
    def at_head(self) -> bool:
        return not self.pending and set(self.current) == set(self.expected)


def migration_status(conn: sa.Connection) -> MigrationStatus:
    scripts = ScriptDirectory.from_config(alembic_config())
    expected = list(scripts.get_heads())
    try:
        rows = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalars().all()
    except sa.exc.ProgrammingError:
        rows = []
    current = list(rows)
    pending = [h for h in expected if h not in current]
    return MigrationStatus(current=current, expected=expected, pending=pending)


def check_migrations() -> PreflightResult:
    """Compare alembic_version with the code's heads. Needs a reachable database."""
    r = PreflightResult()
    try:
        from backend.db import get_engine

        with get_engine().begin() as conn:
            status = migration_status(conn)
    except Exception as exc:
        r.errors.append(f"database unreachable for migration check: {type(exc).__name__}")
        return r
    if not status.current:
        r.errors.append(
            "database has no alembic_version: run `hackatl-migrate` before starting services"
        )
    elif status.pending:
        r.errors.append(
            f"database is behind code heads {status.expected} (at {status.current}): "
            "run `hackatl-migrate`"
        )
    return r


def run(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hackatl-preflight")
    p.add_argument("--config", action="store_true", help="configuration checks only (no DB)")
    p.add_argument("--worker", action="store_true", help="include worker-only requirements")
    args = p.parse_args(argv)

    result = validate_config(get_settings(), worker=args.worker)
    if not args.config:
        mig = check_migrations()
        result.errors += mig.errors
        result.warnings += mig.warnings
    print(result.render())
    return 0 if result.ok else 1


def require_config(*, worker: bool = False) -> None:
    """Entrypoint guard: exit loudly when the environment is misconfigured."""
    result = validate_config(get_settings(), worker=worker)
    for w in result.warnings:
        print(f"[WARN]  {w}", file=sys.stderr)
    if not result.ok:
        for e in result.errors:
            print(f"[ERROR] {e}", file=sys.stderr, flush=True)
        raise SystemExit("preflight configuration check failed")


def require_migrated() -> None:
    """Worker guard: refuse to claim jobs on a schema the code did not migrate."""
    result = check_migrations()
    for w in result.warnings:
        print(f"[WARN]  {w}", file=sys.stderr)
    if not result.ok:
        for e in result.errors:
            print(f"[ERROR] {e}", file=sys.stderr, flush=True)
        raise SystemExit("preflight migration check failed")
