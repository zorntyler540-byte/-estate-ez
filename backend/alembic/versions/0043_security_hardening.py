"""Token versioning, Facebook OAuth state, and click daily counts.

Revision ID: 0043_security_hardening
Revises: 0042_tracked_links
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "0043_security_hardening"
down_revision = "0042_tracked_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "users" in tables:
        cols = {c["name"] for c in insp.get_columns("users")}
        if "token_version" not in cols:
            op.add_column(
                "users",
                sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
            )

    if "facebook_oauth_states" not in tables:
        op.create_table(
            "facebook_oauth_states",
            sa.Column("jti", sa.String(64), primary_key=True),
            sa.Column("account_id", sa.String(36), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_facebook_oauth_states_account_id", "facebook_oauth_states", ["account_id"])
        op.create_index("ix_facebook_oauth_states_user_id", "facebook_oauth_states", ["user_id"])

    if "tracked_link_daily_counts" not in tables:
        op.create_table(
            "tracked_link_daily_counts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("link_id", sa.String(36), sa.ForeignKey("tracked_links.id"), nullable=False),
            sa.Column("account_id", sa.String(36), nullable=False),
            sa.Column("sale_id", sa.String(36), nullable=True),
            sa.Column("lot_id", sa.String(36), nullable=True),
            sa.Column("facebook_post_id", sa.String(36), nullable=True),
            sa.Column("calendar_key", sa.String(120), nullable=False, server_default=""),
            sa.Column("channel", sa.String(20), nullable=False, server_default="page_post"),
            sa.Column("destination_kind", sa.String(16), nullable=False, server_default="lot"),
            sa.Column("day_pacific", sa.String(10), nullable=False),
            sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
            sa.UniqueConstraint("link_id", "day_pacific", name="uq_tracked_link_daily"),
        )
        op.create_index("ix_tracked_link_daily_counts_link_id", "tracked_link_daily_counts", ["link_id"])
        op.create_index("ix_tracked_link_daily_counts_account_id", "tracked_link_daily_counts", ["account_id"])
        op.create_index("ix_tracked_link_daily_counts_sale_id", "tracked_link_daily_counts", ["sale_id"])
        op.create_index("ix_tracked_link_daily_counts_lot_id", "tracked_link_daily_counts", ["lot_id"])
        op.create_index("ix_tracked_link_daily_counts_day_pacific", "tracked_link_daily_counts", ["day_pacific"])

    if "tracked_link_clicks" in tables:
        # Best-effort backfill so reports after deploy still include existing clicks.
        # Date token is UTC YYYY-MM-DD (good enough for a one-time import).
        try:
            bind.execute(
                text(
                    """
                    INSERT INTO tracked_link_daily_counts (
                        id, link_id, account_id, sale_id, lot_id, facebook_post_id,
                        calendar_key, channel, destination_kind, day_pacific, clicks
                    )
                    SELECT
                        MIN(id),
                        link_id,
                        MIN(account_id),
                        MIN(sale_id),
                        MIN(lot_id),
                        MIN(facebook_post_id),
                        MIN(calendar_key),
                        MIN(channel),
                        MIN(destination_kind),
                        substr(CAST(clicked_at AS TEXT), 1, 10),
                        COUNT(*)
                    FROM tracked_link_clicks
                    GROUP BY
                        link_id,
                        substr(CAST(clicked_at AS TEXT), 1, 10)
                    """
                )
            )
        except Exception:
            pass


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "tracked_link_daily_counts" in tables:
        op.drop_table("tracked_link_daily_counts")
    if "facebook_oauth_states" in tables:
        op.drop_table("facebook_oauth_states")
    if "users" in tables:
        cols = {c["name"] for c in insp.get_columns("users")}
        if "token_version" in cols:
            op.drop_column("users", "token_version")
