"""Execution reliability: job lifecycle states, transition history, worker registry.

Additive and backward compatible: new nullable columns on ``jobs``, three new
statuses (``waiting``, ``timed_out``, ``recovery_required``) appended to the
status check, and two new tables. Existing rows keep their status and are
backfilled with ``project_id`` where the payload (or assessment) identifies one.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_OLD_STATUSES = "status in ('queued','running','succeeded','failed','cancelled','dead')"
_NEW_STATUSES = (
    "status in ('queued','waiting','running','succeeded','failed','cancelled','dead',"
    "'timed_out','recovery_required')"
)


def upgrade() -> None:
    op.add_column("jobs", sa.Column("project_id", sa.String(length=80), nullable=True))
    op.add_column("jobs", sa.Column("depends_on", sa.String(length=80), nullable=True))
    op.add_column("jobs", sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("first_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("effect_state", sa.String(length=20), nullable=True))
    op.add_column("jobs", sa.Column("effect_key", sa.String(length=200), nullable=True))
    op.add_column("jobs", sa.Column("error_category", sa.String(length=40), nullable=True))
    op.add_column("jobs", sa.Column("recovery", JSONB(), nullable=True))
    op.drop_constraint(op.f("ck_jobs_status"), "jobs", type_="check")
    op.create_check_constraint(op.f("ck_jobs_status"), "jobs", _NEW_STATUSES)
    op.create_check_constraint(
        op.f("ck_jobs_effect_state"),
        "jobs",
        "effect_state is null or effect_state in ('in_flight','settled')",
    )
    op.create_index("ix_jobs_project_status", "jobs", ["project_id", "status"], unique=False)
    op.create_index(
        "ix_jobs_depends_on",
        "jobs",
        ["depends_on"],
        unique=False,
        postgresql_where=sa.text("depends_on IS NOT NULL"),
    )
    op.execute(
        "UPDATE jobs SET project_id = payload->>'project_id' "
        "WHERE project_id IS NULL AND payload ? 'project_id'"
    )
    op.execute(
        "UPDATE jobs SET project_id = a.project_id FROM assessments a "
        "WHERE jobs.project_id IS NULL AND jobs.payload->>'assessment_id' = a.id"
    )
    op.create_table(
        "job_events",
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("job_id", sa.String(length=80), nullable=False),
        sa.Column("project_id", sa.String(length=80), nullable=True),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("worker", sa.String(length=120), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("details", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_job_events_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_job_events")),
    )
    op.create_index("ix_job_events_job_seq", "job_events", ["job_id", "seq"], unique=False)
    op.create_index(
        "ix_job_events_project_created", "job_events", ["project_id", "created_at"], unique=False
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("current_job_id", sa.String(length=80), nullable=True),
        sa.Column("jobs_finished", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state in ('idle','busy','stopping')", name=op.f("ck_worker_heartbeats_state")
        ),
        sa.PrimaryKeyConstraint("worker_id", name=op.f("pk_worker_heartbeats")),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    op.drop_index("ix_job_events_project_created", table_name="job_events")
    op.drop_index("ix_job_events_job_seq", table_name="job_events")
    op.drop_table("job_events")
    # New statuses cannot be represented by the old check; map them conservatively.
    op.execute("UPDATE jobs SET status = 'queued' WHERE status = 'waiting'")
    op.execute(
        "UPDATE jobs SET status = 'failed' WHERE status in ('timed_out','recovery_required')"
    )
    op.drop_index("ix_jobs_depends_on", table_name="jobs")
    op.drop_index("ix_jobs_project_status", table_name="jobs")
    op.drop_constraint(op.f("ck_jobs_effect_state"), "jobs", type_="check")
    op.drop_constraint(op.f("ck_jobs_status"), "jobs", type_="check")
    op.create_check_constraint(op.f("ck_jobs_status"), "jobs", _OLD_STATUSES)
    for col in (
        "recovery",
        "error_category",
        "effect_key",
        "effect_state",
        "finished_at",
        "first_started_at",
        "started_at",
        "deadline_at",
        "depends_on",
        "project_id",
    ):
        op.drop_column("jobs", col)
