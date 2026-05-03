"""Per-lot SKU sanity: duplicate SKU in sale + mixed lot numbers in listing text."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0014_lot_sku_sanity_flags"
down_revision = "0013_photo_sort_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("lots")}
    if "sku_duplicate_in_sale" not in cols:
        op.add_column(
            "lots",
            sa.Column("sku_duplicate_in_sale", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    if "sku_text_mismatch" not in cols:
        op.add_column(
            "lots",
            sa.Column("sku_text_mismatch", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("lots")}
    if "sku_text_mismatch" in cols:
        op.drop_column("lots", "sku_text_mismatch")
    if "sku_duplicate_in_sale" in cols:
        op.drop_column("lots", "sku_duplicate_in_sale")
