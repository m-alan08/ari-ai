"""Alembic environment: migrations use the migration database URL and backend.db metadata."""

from __future__ import annotations

from alembic import context

from backend.db import get_engine, metadata
from backend.migrate import migration_database_url

config = context.config


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:
        context.configure(connection=connectable, target_metadata=metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    with get_engine(migration_database_url()).connect() as connection:
        context.configure(connection=connection, target_metadata=metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
