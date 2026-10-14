"""Hero ZIP can include every website sale posted to one CT Seller auction."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0037_zip_export_across_sales"
down_revision = "0036_photo_plain_bg_saved"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "sale_photo_zip_exports" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("sale_photo_zip_exports")}
    if "across_sales" in cols:
        return
    op.add_column(
        "sale_photo_zip_exports",
        sa.Column("across_sales", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "sale_photo_zip_exports" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("sale_photo_zip_exports")}
    if "across_sales" in cols:
        op.drop_column("sale_photo_zip_exports", "across_sales")
