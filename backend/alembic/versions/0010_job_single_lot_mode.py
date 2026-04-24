"""Add single_lot_mode to processing_jobs (multi-photo upload = one catalog lot)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0010_job_single_lot_mode"
down_revision = "0009_job_inherit_sku_from_subfolder"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("processing_jobs")}
    if "single_lot_mode" in cols:
        return
    default = sa.text("false") if bind.dialect.name == "postgresql" else sa.text("0")
    op.add_column(
        "processing_jobs",
        sa.Column("single_lot_mode", sa.Boolean(), nullable=False, server_default=default),
    )


def downgrade() -> None:
    op.drop_column("processing_jobs", "single_lot_mode")
