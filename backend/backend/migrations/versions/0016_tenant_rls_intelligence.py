"""Row-level security on the private-source intelligence store.

``index_payloads`` holds customer source bytes and ``index_snapshots`` indexes
them. Application-level tenancy is already enforced in the payload/memory
stores; RLS adds a second, database-level boundary: rows are visible only
inside a transaction that first sets ``ari.project_id`` via
``set_config('ari.project_id', <project>, true)``. Without that tenant GUC the
tables read as empty — a missed app check can never yield another tenant's
source. ``FORCE ROW LEVEL SECURITY`` applies the policy to the table owner as
well (superusers remain exempt, so verification uses a probe role).
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_TENANT = "project_id = nullif(current_setting('ari.project_id', true), '')"


def upgrade() -> None:
    for table in ("index_payloads", "index_snapshots"):
        # Idempotent under retry: a container restart mid-migration can leave the
        # policy or the RLS flags half-applied while alembic_version is unbumped,
        # which would otherwise crash-loop every boot on "policy already exists".
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            FOR ALL
            USING ({_TENANT})
            WITH CHECK ({_TENANT})
            """
        )


def downgrade() -> None:
    for table in ("index_payloads", "index_snapshots"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
