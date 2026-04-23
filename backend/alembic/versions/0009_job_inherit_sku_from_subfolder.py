"""Add inherit_sku_from_subfolder to processing_jobs."""

from alembic import op
import sqlalchemy as sa

revision = "0009_job_inherit_sku_from_subfolder"
down_revision = "0008_lot_sku_needs_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "processing_jobs",
        sa.Column("inherit_sku_from_subfolder", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("processing_jobs", "inherit_sku_from_subfolder")
