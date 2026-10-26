"""Populated upstream 0017 to release 0019, supported rollback and re-upgrade."""

import uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from conftest import _admin_engine

from backend.migrate import MIGRATIONS


def test_populated_0017_to_0019_roundtrip_preserves_upstream_boundaries():
    admin = _admin_engine()
    name = "release0019_" + uuid.uuid4().hex[:12]
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    engine = sa.create_engine(admin.url.set(database=name))
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "0017")
            conn.execute(
                sa.text(
                    "INSERT INTO jobs(id,kind,payload,status) VALUES ('review_job','review','{\"project_id\":\"prj_review\"}','queued')"
                )
            )
            conn.execute(
                sa.text("INSERT INTO worker_heartbeats(worker_id) VALUES ('upstream_worker')")
            )
            for revision in ["0019", "0017", "0019"]:
                (command.downgrade if revision == "0017" else command.upgrade)(cfg, revision)
                assert (
                    conn.execute(
                        sa.text("SELECT status FROM jobs WHERE id='review_job'")
                    ).scalar_one()
                    == "queued"
                )
                assert (
                    conn.execute(
                        sa.text(
                            "SELECT count(*) FROM worker_heartbeats WHERE worker_id='upstream_worker'"
                        )
                    ).scalar_one()
                    == 1
                )
                assert conn.execute(
                    sa.text(
                        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname='index_payloads'"
                    )
                ).scalar_one()
            assert (
                conn.execute(
                    sa.text("SELECT project_id FROM jobs WHERE id='review_job'")
                ).scalar_one()
                == "prj_review"
            )
            assert {"worker_registry", "worker_heartbeats", "job_events"} <= set(
                sa.inspect(conn).get_table_names()
            )
            columns = {c["name"] for c in sa.inspect(conn).get_columns("worker_heartbeats")}
            assert {"heartbeat_at", "last_poll_at"} <= columns
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
