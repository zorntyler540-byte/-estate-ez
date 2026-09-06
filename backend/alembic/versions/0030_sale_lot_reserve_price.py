"""Sale-wide and per-lot CT Seller reserve price."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0030_sale_lot_reserve_price"
down_revision = "0029_sale_cover_photos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "sales" in insp.get_table_names():
        sale_cols = {c["name"] for c in insp.get_columns("sales")}
        if "ctseller_reserve_price" not in sale_cols:
            op.add_column("sales", sa.Column("ctseller_reserve_price", sa.Float(), nullable=True))
    if "lots" in insp.get_table_names():
        lot_cols = {c["name"] for c in insp.get_columns("lots")}
        if "reserve_price" not in lot_cols:
            op.add_column("lots", sa.Column("reserve_price", sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "lots" in insp.get_table_names():
        lot_cols = {c["name"] for c in insp.get_columns("lots")}
        if "reserve_price" in lot_cols:
            op.drop_column("lots", "reserve_price")
    if "sales" in insp.get_table_names():
        sale_cols = {c["name"] for c in insp.get_columns("sales")}
        if "ctseller_reserve_price" in sale_cols:
            op.drop_column("sales", "ctseller_reserve_price")
