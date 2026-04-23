"""Add sku_needs_review to lots."""

from alembic import op
import sqlalchemy as sa

revision = "0008_lot_sku_needs_review"
down_revision = "0007_sale_closed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    default = sa.text("false") if bind.dialect.name == "postgresql" else sa.text("0")
    op.add_column(
        "lots",
        sa.Column("sku_needs_review", sa.Boolean(), nullable=False, server_default=default),
    )


def downgrade() -> None:
    op.drop_column("lots", "sku_needs_review")
