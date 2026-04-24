"""Sale-wide catalog photo ZIP export (async, large sales)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0012_sale_zip_exports"
down_revision = "0011_job_single_lot_sku"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = insp.get_table_names()
    if "sale_photo_zip_exports" in tables:
        return
    op.create_table(
        "sale_photo_zip_exports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("sale_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("zip_path", sa.String(length=700), nullable=True),
        sa.Column("total_photos_expected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("photo_files_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sale_zip_exports_account", "sale_photo_zip_exports", ["account_id"])
    op.create_index("ix_sale_zip_exports_user_sale", "sale_photo_zip_exports", ["user_id", "sale_id"])


def downgrade() -> None:
    op.drop_table("sale_photo_zip_exports")
