"""Short Facebook click-tracking links and click rows.

Revision ID: 0042_tracked_links
Revises: 0041_push_buckets_calendar_dates
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0042_tracked_links"
down_revision = "0041_push_buckets_calendar_dates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "tracked_links" not in tables:
        op.create_table(
            "tracked_links",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("code", sa.String(16), nullable=False),
            sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("sale_id", sa.String(36), nullable=True),
            sa.Column("lot_id", sa.String(36), sa.ForeignKey("lots.id"), nullable=True),
            sa.Column("facebook_post_id", sa.String(36), nullable=True),
            sa.Column("calendar_key", sa.String(120), nullable=False, server_default=""),
            sa.Column("channel", sa.String(20), nullable=False, server_default="page_post"),
            sa.Column("destination_kind", sa.String(16), nullable=False, server_default="lot"),
            sa.Column("destination_url", sa.String(600), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_tracked_links_code", "tracked_links", ["code"], unique=True)
        op.create_index("ix_tracked_links_account_id", "tracked_links", ["account_id"])
        op.create_index("ix_tracked_links_sale_id", "tracked_links", ["sale_id"])
        op.create_index("ix_tracked_links_lot_id", "tracked_links", ["lot_id"])
        op.create_index("ix_tracked_links_facebook_post_id", "tracked_links", ["facebook_post_id"])
    if "tracked_link_clicks" not in tables:
        op.create_table(
            "tracked_link_clicks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("link_id", sa.String(36), sa.ForeignKey("tracked_links.id"), nullable=False),
            sa.Column("account_id", sa.String(36), nullable=False),
            sa.Column("code", sa.String(16), nullable=False),
            sa.Column("sale_id", sa.String(36), nullable=True),
            sa.Column("lot_id", sa.String(36), nullable=True),
            sa.Column("facebook_post_id", sa.String(36), nullable=True),
            sa.Column("calendar_key", sa.String(120), nullable=False, server_default=""),
            sa.Column("channel", sa.String(20), nullable=False, server_default="page_post"),
            sa.Column("destination_kind", sa.String(16), nullable=False, server_default="lot"),
            sa.Column("destination_url", sa.String(600), nullable=False),
            sa.Column("referrer", sa.String(400), nullable=False, server_default=""),
            sa.Column("clicked_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_tracked_link_clicks_link_id", "tracked_link_clicks", ["link_id"])
        op.create_index("ix_tracked_link_clicks_account_id", "tracked_link_clicks", ["account_id"])
        op.create_index("ix_tracked_link_clicks_code", "tracked_link_clicks", ["code"])
        op.create_index("ix_tracked_link_clicks_sale_id", "tracked_link_clicks", ["sale_id"])
        op.create_index("ix_tracked_link_clicks_lot_id", "tracked_link_clicks", ["lot_id"])
        op.create_index("ix_tracked_link_clicks_facebook_post_id", "tracked_link_clicks", ["facebook_post_id"])
        op.create_index("ix_tracked_link_clicks_clicked_at", "tracked_link_clicks", ["clicked_at"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "tracked_link_clicks" in tables:
        op.drop_table("tracked_link_clicks")
    if "tracked_links" in tables:
        op.drop_table("tracked_links")
