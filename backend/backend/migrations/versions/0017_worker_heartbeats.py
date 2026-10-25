"""Worker liveness heartbeats for /readyz (pilot readiness).

One row per live worker process; ``heartbeat_at`` decides liveness. Downgrade drops it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Retry-safe: if a container restart half-applied this migration, the table may
    # already exist while alembic_version still points at 0016 — without the guard
    # every subsequent boot would fail on "relation already exists" forever.
    if "worker_heartbeats" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=120), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "heartbeat_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_poll_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("current_job_id", sa.String(length=80), nullable=True),
        sa.PrimaryKeyConstraint("worker_id", name=op.f("pk_worker_heartbeats")),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
