"""Repository disconnect: owner-controlled off-switch for all repo-driven work."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repositories", sa.Column("disconnected_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("repositories", "disconnected_at")
