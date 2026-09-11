"""Per-lot CT Seller auction id + ZIP filter by that auction."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0032_lot_ctseller_sale_id"
down_revision = "0031_lot_videos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "lots" in insp.get_table_names():
        lot_cols = {c["name"] for c in insp.get_columns("lots")}
        if "ctseller_sale_id" not in lot_cols:
            op.add_column("lots", sa.Column("ctseller_sale_id", sa.Integer(), nullable=True))
            op.create_index("ix_lots_ctseller_sale_id", "lots", ["ctseller_sale_id"])
    if "sale_photo_zip_exports" in insp.get_table_names():
        zip_cols = {c["name"] for c in insp.get_columns("sale_photo_zip_exports")}
        if "filter_ctseller_sale_id" not in zip_cols:
            op.add_column(
                "sale_photo_zip_exports",
                sa.Column("filter_ctseller_sale_id", sa.Integer(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "sale_photo_zip_exports" in insp.get_table_names():
        zip_cols = {c["name"] for c in insp.get_columns("sale_photo_zip_exports")}
        if "filter_ctseller_sale_id" in zip_cols:
            op.drop_column("sale_photo_zip_exports", "filter_ctseller_sale_id")
    if "lots" in insp.get_table_names():
        lot_cols = {c["name"] for c in insp.get_columns("lots")}
        if "ctseller_sale_id" in lot_cols:
            op.drop_index("ix_lots_ctseller_sale_id", table_name="lots")
            op.drop_column("lots", "ctseller_sale_id")
