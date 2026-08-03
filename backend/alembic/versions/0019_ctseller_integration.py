"""CT Seller integration: credentials on accounts, sale mapping, lot post tracking."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0019_ctseller_integration"
down_revision = "0018_lot_ai_regenerate_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    acct_cols = {c["name"] for c in insp.get_columns("accounts")}
    if "ctseller_username" not in acct_cols:
        op.add_column("accounts", sa.Column("ctseller_username", sa.String(length=255), nullable=True))
    if "ctseller_password_enc" not in acct_cols:
        op.add_column("accounts", sa.Column("ctseller_password_enc", sa.Text(), nullable=True))
    if "ctseller_access_token_enc" not in acct_cols:
        op.add_column("accounts", sa.Column("ctseller_access_token_enc", sa.Text(), nullable=True))
    if "ctseller_refresh_token_enc" not in acct_cols:
        op.add_column("accounts", sa.Column("ctseller_refresh_token_enc", sa.Text(), nullable=True))

    sale_cols = {c["name"] for c in insp.get_columns("sales")}
    if "ctseller_sale_id" not in sale_cols:
        op.add_column("sales", sa.Column("ctseller_sale_id", sa.Integer(), nullable=True))

    lot_cols = {c["name"] for c in insp.get_columns("lots")}
    if "ctseller_item_id" not in lot_cols:
        op.add_column("lots", sa.Column("ctseller_item_id", sa.Integer(), nullable=True))
    if "ctseller_posted_at" not in lot_cols:
        op.add_column("lots", sa.Column("ctseller_posted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    lot_cols = {c["name"] for c in insp.get_columns("lots")}
    if "ctseller_posted_at" in lot_cols:
        op.drop_column("lots", "ctseller_posted_at")
    if "ctseller_item_id" in lot_cols:
        op.drop_column("lots", "ctseller_item_id")

    sale_cols = {c["name"] for c in insp.get_columns("sales")}
    if "ctseller_sale_id" in sale_cols:
        op.drop_column("sales", "ctseller_sale_id")

    acct_cols = {c["name"] for c in insp.get_columns("accounts")}
    if "ctseller_refresh_token_enc" in acct_cols:
        op.drop_column("accounts", "ctseller_refresh_token_enc")
    if "ctseller_access_token_enc" in acct_cols:
        op.drop_column("accounts", "ctseller_access_token_enc")
    if "ctseller_password_enc" in acct_cols:
        op.drop_column("accounts", "ctseller_password_enc")
    if "ctseller_username" in acct_cols:
        op.drop_column("accounts", "ctseller_username")
