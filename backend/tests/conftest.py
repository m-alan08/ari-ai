"""Backend test fixtures: a REAL PostgreSQL database (DATABASE_URL, or an embedded
pgserver instance), migrations applied via Alembic, isolated artifact dir."""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa

_TMP = Path(tempfile.mkdtemp(prefix="hackatl-test-"))
WEBHOOK_SECRET = "test-webhook-secret-not-real"


# One reusable embedded server for all local test sessions (never one per run: leaked
# postmasters exhaust SysV shared memory on macOS). Each session gets a fresh database,
# dropped again at session end.
_PGTEST_DIR = Path(__file__).resolve().parents[2] / ".dev" / "pgtest"
_ADMIN_URL: str | None = None
_DB_NAME = f"hackatl_test_{uuid.uuid4().hex[:8]}"


def _admin_engine() -> sa.Engine:
    assert _ADMIN_URL
    url = _ADMIN_URL
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return sa.create_engine(url, isolation_level="AUTOCOMMIT")


def _sandbox_target():  # type: ignore[no-untyped-def]
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "deploy" / "sandbox_target.py"
    spec = importlib.util.spec_from_file_location("sandbox_target", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _database_url() -> str:
    global _ADMIN_URL
    _ADMIN_URL = os.environ.get("TEST_DATABASE_URL")
    if not _ADMIN_URL:
        try:
            from backend.devdb import start

            _ADMIN_URL = start(_PGTEST_DIR, database="postgres")
        except ImportError:  # agent sandbox: no pgserver wheel; use the system PostgreSQL
            _ADMIN_URL = _sandbox_target()._postgres_url()
    admin = _admin_engine()
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{_DB_NAME}"'))
    admin.dispose()
    return (
        sa.engine.make_url(_ADMIN_URL).set(database=_DB_NAME).render_as_string(hide_password=False)
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    from backend.db import get_engine

    get_engine().dispose()
    admin = _admin_engine()
    with admin.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{_DB_NAME}" WITH (FORCE)'))
    admin.dispose()


def _live_key() -> str:
    """The real key for the opt-in live smoke, read BEFORE tests blank ANTHROPIC_API_KEY.
    Source: the shell environment, else the repo-root .env (git-ignored)."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "ANTHROPIC_API_KEY":
                key = value.split("#", 1)[0].strip().strip("\"'")
    return "" if key in ("", "replace-me") else key


os.environ["HACKATL_LIVE_ANTHROPIC_API_KEY"] = _live_key()

os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": _database_url(),
        "ARTIFACT_STORE_URI": f"file://{_TMP / 'artifacts'}",
        "AUTH_DEV_LOGIN_ENABLED": "true",
        "GITHUB_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "ANTHROPIC_API_KEY": "",
        "ORCHESTRATOR_FACTORY": "security_agents.api:build_orchestrator",
        "CODE_INTELLIGENCE_FACTORY": "code_intelligence.api:build_service",
        "WORKER_LEASE_S": "30",
    }
)

from backend.settings import get_settings  # noqa: E402

get_settings.cache_clear()

from backend.migrate import upgrade  # noqa: E402

upgrade()

_TABLES = [
    "analyst_handoffs",
    "first_responder_invocations",
    "first_responder_decisions",
    "deploy_commit_mappings",
    "first_responder_modes",
    "analyst_reviews",
    "live_policy_decisions",
    "incidents",
    "runtime_windows",
    "ingest_tokens",
    "connected_applications",
    "index_payloads",
    "index_snapshots",
    "idempotency_keys",
    "rl_evaluations",
    "defense_recommendations",
    "reassessment_requests",
    "monitoring_events",
    "webhook_deliveries",
    "jobs",
    "verification_results",
    "patch_candidates",
    "finding_records",
    "findings",
    "agent_runs",
    "assessment_events",
    "assessments",
    "approvals",
    "authorized_scopes",
    "snapshots",
    "repositories",
    "projects",
    "sessions",
    "memberships",
    "users",
    "artifacts",
    "audit_events",
    "organizations",
]


@pytest.fixture(autouse=True)
def clean_db() -> Iterator[None]:
    from backend import llm
    from backend.api.deps import limiter
    from backend.db import get_engine

    yield
    from backend import pipeline

    pipeline._ORCHESTRATORS.clear()
    limiter._hits.clear()
    llm.TRANSPORT_OVERRIDE = None
    get_settings.cache_clear()
    with get_engine().begin() as c:
        c.execute(sa.text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
        c.execute(sa.text("TRUNCATE " + ", ".join(_TABLES) + " CASCADE"))
        c.execute(sa.text("ALTER TABLE audit_events ENABLE TRIGGER USER"))


@pytest.fixture
def settings_override(monkeypatch: pytest.MonkeyPatch):
    def apply(**values: str) -> None:
        for k, v in values.items():
            monkeypatch.setenv(k.upper(), v)
        get_settings.cache_clear()

    return apply


@pytest.fixture
def client_factory():
    from fastapi.testclient import TestClient

    from backend.main import create_app

    clients = []

    def make(login: str | None = None):
        c = TestClient(create_app())
        clients.append(c)
        if login:
            r = c.post("/api/v1/auth/dev/login", json={"github_login": login})
            assert r.status_code == 200, r.text
            c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
            c.me = r.json()  # type: ignore[attr-defined]
        return c

    yield make
    for c in clients:
        c.close()
