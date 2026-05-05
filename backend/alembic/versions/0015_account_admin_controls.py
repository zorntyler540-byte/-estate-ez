"""Add account activation + plan code for beta admin controls."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0015_account_admin_controls"
down_revision = "0014_lot_sku_sanity_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("accounts")}
    if "plan_code" not in cols:
        op.add_column(
            "accounts",
            sa.Column("plan_code", sa.String(length=40), nullable=False, server_default="beta_free"),
        )
    if "is_active" not in cols:
        op.add_column(
            "accounts",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("accounts")}
    if "is_active" in cols:
        op.drop_column("accounts", "is_active")
    if "plan_code" in cols:
        op.drop_column("accounts", "plan_code")
