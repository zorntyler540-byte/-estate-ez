"""CT Seller closed-sale item snapshots for Team sale analytics."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0035_ctseller_history_items"
down_revision = "0034_photo_captured_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "ctseller_history_items" in insp.get_table_names():
        return
    op.create_table(
        "ctseller_history_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("ctseller_sale_id", sa.Integer(), nullable=False),
        sa.Column("ctseller_item_id", sa.Integer(), nullable=False),
        sa.Column("sale_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("sale_open", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("sale_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sku", sa.String(length=80), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=120), nullable=False, server_default="Uncategorized"),
        sa.Column("subcategory", sa.String(length=120), nullable=True),
        sa.Column("item_status", sa.String(length=40), nullable=True),
        sa.Column("sold_price", sa.Float(), nullable=True),
        sa.Column("lot_id", sa.String(length=36), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("account_id", "ctseller_item_id", name="uq_ctseller_history_item"),
    )
    op.create_index("ix_ctseller_history_items_account_id", "ctseller_history_items", ["account_id"])
    op.create_index("ix_ctseller_history_items_ctseller_sale_id", "ctseller_history_items", ["ctseller_sale_id"])
    op.create_index("ix_ctseller_history_items_ctseller_item_id", "ctseller_history_items", ["ctseller_item_id"])
    op.create_index("ix_ctseller_history_items_lot_id", "ctseller_history_items", ["lot_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "ctseller_history_items" not in insp.get_table_names():
        return
    op.drop_table("ctseller_history_items")
