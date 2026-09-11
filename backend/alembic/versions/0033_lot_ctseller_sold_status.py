"""CT Seller item status + hammer price (sold vs unsold)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0033_lot_ctseller_sold_status"
down_revision = "0032_lot_ctseller_sale_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "lots" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("lots")}
    if "ctseller_item_status" not in cols:
        op.add_column("lots", sa.Column("ctseller_item_status", sa.String(length=40), nullable=True))
    if "ctseller_sold_price" not in cols:
        op.add_column("lots", sa.Column("ctseller_sold_price", sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "lots" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("lots")}
    if "ctseller_sold_price" in cols:
        op.drop_column("lots", "ctseller_sold_price")
    if "ctseller_item_status" in cols:
        op.drop_column("lots", "ctseller_item_status")
