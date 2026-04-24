"""Optional manual SKU for single-lot jobs (mobile entry)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0011_job_single_lot_sku"
down_revision = "0010_job_single_lot_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("processing_jobs")}
    if "single_lot_manual_sku" in cols:
        return
    op.add_column(
        "processing_jobs",
        sa.Column("single_lot_manual_sku", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("processing_jobs", "single_lot_manual_sku")
