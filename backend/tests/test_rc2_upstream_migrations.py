"""Exercise the actual latest-main database stamp against release migrations."""

import subprocess
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from conftest import _admin_engine

from backend.migrate import MIGRATIONS

UPSTREAM_SHA = "eea084bc87ac6ca94f578073251c478655b09366"


@pytest.mark.parametrize("direction", ["main_to_release"])
def test_latest_upstream_database_can_upgrade_to_release(tmp_path, direction):
    path = "backend/backend/migrations"
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", UPSTREAM_SHA, path], text=True
    ).splitlines()
    scripts = tmp_path / "upstream"
    for name in names:
        dest = scripts / name.removeprefix(path + "/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(subprocess.check_output(["git", "show", UPSTREAM_SHA + ":" + name]))
    admin = _admin_engine()
    dbname = "rereview_" + uuid.uuid4().hex[:12]
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{dbname}"'))
    engine = sa.create_engine(admin.url.set(database=dbname))
    try:
        with engine.begin() as conn:
            for root in (
                [scripts, MIGRATIONS] if direction == "main_to_release" else [MIGRATIONS, scripts]
            ):
                cfg = Config()
                cfg.set_main_option("script_location", str(root))
                cfg.attributes["connection"] = conn
                command.upgrade(cfg, "heads")
            columns = {item["name"] for item in sa.inspect(conn).get_columns("jobs")}
            if direction == "main_to_release":
                assert {"deadline_at", "effect_state", "defer_count"} <= columns, columns
            else:
                protected = conn.execute(
                    sa.text(
                        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname='index_payloads'"
                    )
                ).scalar_one()
                assert protected, "latest-main RLS migration silently skipped"
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
        admin.dispose()
