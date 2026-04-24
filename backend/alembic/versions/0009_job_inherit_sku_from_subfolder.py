"""Add inherit_sku_from_subfolder to processing_jobs."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision id must fit alembic_version.version_num (VARCHAR(32) on Postgres default).
revision = "0009_inherit_sku_subfolder"
down_revision = "0008_lot_sku_needs_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("processing_jobs")}
    if "inherit_sku_from_subfolder" in cols:
        return
    default = sa.text("false") if bind.dialect.name == "postgresql" else sa.text("0")
    op.add_column(
        "processing_jobs",
        sa.Column("inherit_sku_from_subfolder", sa.Boolean(), nullable=False, server_default=default),
    )


def downgrade() -> None:
    op.drop_column("processing_jobs", "inherit_sku_from_subfolder")
