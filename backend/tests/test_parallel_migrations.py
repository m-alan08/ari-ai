"""Parallel implementation branches cannot reuse migration IDs or strand a head."""

import shutil
import uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from conftest import _admin_engine

from backend.migrate import MIGRATIONS


def test_real_parallel_heads_upgrade_and_downgrade(tmp_path):
    scripts = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, scripts)
    (scripts / "versions" / "parallel_intelligence_test.py").write_text("""
from alembic import op
import sqlalchemy as sa
revision = 'parallel_test'
down_revision = '0004'
branch_labels = None
depends_on = None
def upgrade():
    op.create_table('parallel_proof', sa.Column('id', sa.Integer, primary_key=True))
def downgrade():
    op.drop_table('parallel_proof')
""")
    name = "migration_proof_" + uuid.uuid4().hex[:12]
    admin = _admin_engine()
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    engine = sa.create_engine(admin.url.set(database=name))
    cfg = Config()
    cfg.set_main_option("script_location", str(scripts))
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "heads")
            assert {"incidents", "parallel_proof"} <= set(sa.inspect(conn).get_table_names())
            expected_heads = set(ScriptDirectory.from_config(cfg).get_heads())
            assert "parallel_test" in expected_heads and len(expected_heads) >= 2
            assert (
                set(conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalars())
                == expected_heads
            )
            command.downgrade(cfg, "base")
            assert "incidents" not in sa.inspect(conn).get_table_names()
            command.upgrade(cfg, "heads")
            assert {"incidents", "parallel_proof"} <= set(sa.inspect(conn).get_table_names())
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_repository_has_a_single_migration_head():
    """Merged branches must leave one head (add a merge revision when two land).

    The pinned revision must equal the newest migration on this branch —
    update it when adding a migration. Three open branches defined 0016
    (see coordination/messages/*pilot-ops-migration-0016*): #535 merged
    first and kept it; this branch renumbered to 0017.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0017"]
