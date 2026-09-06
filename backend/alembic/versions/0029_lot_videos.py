"""Lot videos table (phone/web clips; not Gemini or CT Bids)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0029_lot_videos"
down_revision = "0028_lot_search_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "lot_videos" in insp.get_table_names():
        return
    op.create_table(
        "lot_videos",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), nullable=False, index=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("lot_id", sa.String(length=36), sa.ForeignKey("lots.id"), nullable=False, index=True),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=600), nullable=False),
        sa.Column("content_type", sa.String(length=80), nullable=False, server_default="video/mp4"),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "lot_videos" in insp.get_table_names():
        op.drop_table("lot_videos")
