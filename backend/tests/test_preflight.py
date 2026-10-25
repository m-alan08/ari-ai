"""Preflight: staging/production configuration must fail fast, migrations verified."""

from __future__ import annotations

import sqlalchemy as sa

from backend.db import get_engine
from backend.preflight import (
    check_migrations,
    migration_status,
    run,
    validate_config,
)
from backend.settings import Settings


def _staging_settings(**over) -> Settings:
    base = dict(
        _env_file=None,  # type: ignore[arg-type]
        app_env="staging",
        database_url="postgresql+psycopg://app:real-password@db.internal:5432/hackatl",
        artifact_store_uri="file:///data/artifacts",
        anthropic_api_key="sk-ant-test",  # type: ignore[arg-type]
        github_app_id="123",
        github_app_private_key="pem",  # type: ignore[arg-type]
        github_webhook_secret="whsec",  # type: ignore[arg-type]
        github_oauth_client_id="cid",
        github_oauth_client_secret="csec",  # type: ignore[arg-type]
        api_base_url="https://staging-api.example.com",
        frontend_origin="https://staging.example.com",
        # conftest exports AUTH_DEV_LOGIN_ENABLED=true for the whole test session;
        # staging settings being validated must override it explicitly.
        auth_dev_login_enabled=False,
    )
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def test_dev_config_never_blocks() -> None:
    assert validate_config(Settings(_env_file=None)).ok  # type: ignore[call-arg]


def test_staging_missing_everything_fails(monkeypatch) -> None:
    for var in (
        "ANTHROPIC_API_KEY",
        "GITHUB_WEBHOOK_SECRET",
        "GITHUB_APP_ID",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_APP_PRIVATE_KEY_PATH",
        "GITHUB_OAUTH_CLIENT_ID",
        "GITHUB_OAUTH_CLIENT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    r = validate_config(Settings(_env_file=None, app_env="staging"))  # type: ignore[call-arg]
    assert not r.ok
    joined = " ".join(r.errors)
    for needle in ("ANTHROPIC_API_KEY", "GITHUB_WEBHOOK_SECRET", "GitHub App", "OAuth"):
        assert needle in joined, needle


def test_staging_complete_config_passes() -> None:
    r = validate_config(_staging_settings())
    assert r.ok, r.errors


def test_staging_rejects_dev_login_and_local_sandbox() -> None:
    r = validate_config(
        _staging_settings(auth_dev_login_enabled=True, security_sandbox="local-dev")
    )
    assert not r.ok
    assert any("AUTH_DEV_LOGIN_ENABLED" in e for e in r.errors)
    assert any("SECURITY_SANDBOX" in e for e in r.errors)


def test_staging_rejects_compose_default_db_credentials() -> None:
    r = validate_config(
        _staging_settings(database_url="postgresql+psycopg://app:app-local-only@db:5432/hackatl")
    )
    assert not r.ok
    assert any("local-only credentials" in e for e in r.errors)


def test_worker_requires_rl_pin_outside_dev() -> None:
    r = validate_config(_staging_settings(), worker=True)
    assert not r.ok
    assert any("RL_CHECKPOINT_SHA256" in e for e in r.errors)
    ok = validate_config(
        _staging_settings(
            rl_checkpoint_path="/app/x.pt",
            rl_checkpoint_sha256="a" * 64,
            rl_topology_path="/app/t.yaml",
        ),
        worker=True,
    )
    assert ok.ok, ok.errors


def test_migration_status_reports_at_head() -> None:
    with get_engine().begin() as conn:
        st = migration_status(conn)
    assert st.at_head
    assert st.expected  # conftest migrated the embedded database


def test_check_migrations_passes_when_migrated() -> None:
    assert check_migrations().ok


def test_check_migrations_flags_unmigrated_database() -> None:
    with get_engine().begin() as conn:
        conn.execute(sa.text("DROP TABLE alembic_version"))
        conn.execute(sa.text("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)"))
        conn.execute(sa.text("INSERT INTO alembic_version VALUES ('0000')"))
        st = migration_status(conn)
        conn.execute(sa.text("DROP TABLE alembic_version"))
        conn.execute(sa.text("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)"))
        conn.execute(sa.text("INSERT INTO alembic_version VALUES ('0015')"))
    assert st.pending


def test_run_config_only_in_dev(monkeypatch, capsys) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    from backend.settings import get_settings

    get_settings.cache_clear()
    assert run(["--config"]) == 0
    assert "preflight: PASS" in capsys.readouterr().out
