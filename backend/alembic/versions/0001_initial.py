"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False, server_default="owner"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_users_account_id", "users", ["account_id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "lots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("sku", sa.String(length=40)),
        sa.Column("auction_title", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("professional_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("specification_lines_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("brands_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("labels_and_tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("starting_price", sa.Float()),
        sa.Column("condition_text", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("photo_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_lots_account_id", "lots", ["account_id"])
    op.create_index("ix_lots_sku", "lots", ["sku"])

    op.create_table(
        "photos",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("lot_id", sa.String(length=36), sa.ForeignKey("lots.id")),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=600), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_photos_account_id", "photos", ["account_id"])
    op.create_index("ix_photos_lot_id", "photos", ["lot_id"])

    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("input_folder", sa.String(length=600), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_processing_jobs_account_id", "processing_jobs", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_processing_jobs_account_id", table_name="processing_jobs")
    op.drop_table("processing_jobs")
    op.drop_index("ix_photos_lot_id", table_name="photos")
    op.drop_index("ix_photos_account_id", table_name="photos")
    op.drop_table("photos")
    op.drop_index("ix_lots_sku", table_name="lots")
    op.drop_index("ix_lots_account_id", table_name="lots")
    op.drop_table("lots")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_account_id", table_name="users")
    op.drop_table("users")
    op.drop_table("accounts")

