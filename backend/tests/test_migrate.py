from __future__ import annotations

from backend import migrate
from backend.settings import Settings


def test_migration_database_url_prefers_configured_migration_url(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://app@localhost/app",
        migration_database_url="postgresql://migrator@localhost/app",
    )
    monkeypatch.setattr(migrate, "get_settings", lambda: settings)

    assert migrate.migration_database_url() == "postgresql://migrator@localhost/app"


def test_migration_database_url_falls_back_to_database_url(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://app@localhost/app",
        migration_database_url=None,
    )
    monkeypatch.setattr(migrate, "get_settings", lambda: settings)

    assert migrate.migration_database_url() == "postgresql://app@localhost/app"
