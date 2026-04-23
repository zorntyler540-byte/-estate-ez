"""processing_jobs.cancel_requested for cross-process cancel

Revision ID: 0002_job_cancel_requested
Revises: 0001_initial
Create Date: 2026-04-17
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_job_cancel_requested"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        default = sa.text("false")
    else:
        default = sa.text("0")
    op.add_column(
        "processing_jobs",
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=default),
    )


def downgrade() -> None:
    op.drop_column("processing_jobs", "cancel_requested")
