"""Programmatic Alembic entry points (no alembic.ini needed)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from backend.db import get_engine
from backend.settings import get_settings

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    return cfg


def migration_database_url() -> str:
    settings = get_settings()
    return settings.migration_database_url or settings.database_url


def upgrade(revision: str = "heads") -> None:
    cfg = alembic_config()
    with get_engine(migration_database_url()).begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


def downgrade(revision: str) -> None:
    cfg = alembic_config()
    with get_engine(migration_database_url()).begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, revision)
