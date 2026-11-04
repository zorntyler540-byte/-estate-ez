"""Move API-startup DDL (``create_all`` + ``_ensure_*`` ALTERs) into Alembic.

Every step inspects the live schema first. On production Postgres, where API startup
already created these objects, it changes nothing. On databases that only ever ran
Alembic it fills the gaps that startup used to paper over: the ``engagement_snapshots``
table, ``lots.engagement_json``, and ``users.assigned_sale_id``.

Revision ID: 0050_startup_ddl_to_alembic
Revises: 0049_client_invites
"""

from __future__ import annotations

from collections.abc import Callable

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "0050_startup_ddl_to_alembic"
down_revision = "0049_client_invites"
branch_labels = None
depends_on = None


def _tz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _float_zero(name: str) -> sa.Column:
    return sa.Column(name, sa.Float(), nullable=False, server_default=text("0"))


_OPS_JOB_AUCTION_COLUMNS = (
    "space_plan_areas",
    "space_plan_items",
    "projected_lots",
    "auction_questions",
    "pickup_days",
    "pickup_staff",
    "pickup_hours_per_day",
    "sale_days",
    "sale_staff",
    "sale_hours_per_day",
)

# Exactly the columns the removed ``app.main._ensure_*`` helpers added when missing.
# Factories, because a Column object can only be attached to one table.
_COLUMNS: dict[str, list[Callable[[], sa.Column]]] = {
    "sale_photo_zip_exports": [
        lambda: sa.Column("kind", sa.String(20), nullable=False, server_default="all"),
        lambda: sa.Column("filter_ctseller_sale_id", sa.Integer(), nullable=True),
        lambda: sa.Column("across_sales", sa.Boolean(), nullable=False, server_default=sa.false()),
    ],
    "lots": [
        lambda: sa.Column("audience_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        lambda: sa.Column("audience_hunting_at", _tz(), nullable=True),
        lambda: sa.Column("audience_hunt_error", sa.Text(), nullable=True),
        lambda: sa.Column("audience_json", sa.Text(), nullable=False, server_default="{}"),
        lambda: sa.Column("search_tags_json", sa.Text(), nullable=False, server_default="[]"),
        lambda: sa.Column("reserve_price", sa.Float(), nullable=True),
        lambda: sa.Column("ctseller_sale_id", sa.Integer(), nullable=True),
        lambda: sa.Column("ctseller_item_status", sa.String(40), nullable=True),
        lambda: sa.Column("ctseller_sold_price", sa.Float(), nullable=True),
        lambda: sa.Column("engagement_json", sa.Text(), nullable=False, server_default="{}"),
    ],
    "photos": [
        lambda: sa.Column("distinct_item_id", sa.String(64), nullable=True),
        lambda: sa.Column("is_primary_front", sa.Boolean(), nullable=True),
        lambda: sa.Column("shows_whole_lot", sa.Boolean(), nullable=True),
        lambda: sa.Column("shows_measuring_scale", sa.Boolean(), nullable=True),
        lambda: sa.Column("label_legibility", sa.String(16), nullable=True),
        lambda: sa.Column("plain_bg_saved", sa.Boolean(), nullable=False, server_default=sa.false()),
        lambda: sa.Column("captured_at", _tz(), nullable=True),
    ],
    "sales": [
        lambda: sa.Column("ctseller_cover_photo_ids", sa.Text(), nullable=True),
        lambda: sa.Column("ctseller_reserve_price", sa.Float(), nullable=True),
    ],
    "users": [
        lambda: sa.Column("assigned_sale_id", sa.String(36), nullable=True),
        lambda: sa.Column("token_version", sa.Integer(), nullable=False, server_default=text("0")),
        lambda: sa.Column("access_expires_at", _tz(), nullable=True),
        lambda: sa.Column("revoked_at", _tz(), nullable=True),
    ],
    "accounts": [
        lambda: sa.Column("facebook_user_id", sa.String(64), nullable=True),
        lambda: sa.Column("facebook_user_token_enc", sa.Text(), nullable=True),
        lambda: sa.Column("facebook_user_token_expires_at", _tz(), nullable=True),
        lambda: sa.Column("facebook_page_id", sa.String(64), nullable=True),
        lambda: sa.Column("facebook_page_name", sa.String(255), nullable=True),
        lambda: sa.Column("facebook_page_token_enc", sa.Text(), nullable=True),
        lambda: sa.Column("facebook_connected_at", _tz(), nullable=True),
        lambda: sa.Column("facebook_token_error", sa.Text(), nullable=True),
        lambda: sa.Column(
            "facebook_calendar_auto_approve", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        lambda: sa.Column("feature_analytics", sa.Boolean(), nullable=False, server_default=sa.true()),
        lambda: sa.Column("feature_facebook", sa.Boolean(), nullable=False, server_default=sa.true()),
    ],
    "facebook_page_posts": [
        lambda: sa.Column("calendar_key", sa.String(120), nullable=True),
        lambda: sa.Column("calendar_sale_id", sa.Integer(), nullable=True),
    ],
    "processing_jobs": [
        lambda: sa.Column("client_draft_id", sa.String(36), nullable=True),
    ],
    "ops_jobs": [lambda name=name: _float_zero(name) for name in _OPS_JOB_AUCTION_COLUMNS],
}


def _create_engagement_snapshots() -> None:
    """Frozen copy of ``models.EngagementSnapshot`` as ``create_all`` built it."""
    op.create_table(
        "engagement_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("ctseller_sale_id", sa.Integer(), nullable=False),
        sa.Column("ctseller_item_id", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("item_url", sa.String(600), nullable=False),
        sa.Column("item_status", sa.String(40), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False, server_default=text("0")),
        sa.Column("watchers", sa.Integer(), nullable=False, server_default=text("0")),
        sa.Column("bids", sa.Integer(), nullable=False, server_default=text("0")),
        sa.Column("price", sa.Float(), nullable=False, server_default=text("0")),
        sa.Column("reserve", sa.Float(), nullable=False, server_default=text("0")),
        sa.Column("bucket", sa.String(20), nullable=False),
        sa.Column("engagement_score", sa.String(20), nullable=False),
        sa.Column("momentum", sa.String(40), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("late_surge", sa.String(20), nullable=False),
        sa.Column("trajectory", sa.String(40), nullable=False),
        sa.Column("rescue_candidate", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("push_rank", sa.Integer(), nullable=True),
        sa.Column("lot_id", sa.String(36), nullable=True),
        sa.Column("sale_title", sa.String(255), nullable=False),
        sa.Column("auction_url", sa.String(600), nullable=False),
        sa.Column("sale_ends_at", _tz(), nullable=True),
        sa.Column("captured_at", _tz(), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "account_id", "ctseller_sale_id", "ctseller_item_id", name="uq_engagement_snapshot_item"
        ),
    )
    for col in ("account_id", "ctseller_sale_id", "ctseller_item_id", "lot_id"):
        op.create_index(f"ix_engagement_snapshots_{col}", "engagement_snapshots", [col])


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    is_pg = bind.dialect.name.startswith("postgres")

    if "engagement_snapshots" not in tables and "accounts" in tables:
        _create_engagement_snapshots()
        tables.add("engagement_snapshots")

    if "ops_jobs" in tables:
        ops_cols = {c["name"] for c in insp.get_columns("ops_jobs")}
        if "sqft" in ops_cols and "cuft" not in ops_cols:
            op.execute(text("ALTER TABLE ops_jobs RENAME COLUMN sqft TO cuft"))

    for table, factories in _COLUMNS.items():
        if table not in tables:
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for factory in factories:
            column = factory()
            if column.name not in existing:
                op.add_column(table, column)

    if "processing_jobs" in tables:
        indexes = {ix["name"] for ix in insp.get_indexes("processing_jobs")}
        if "ix_processing_jobs_client_draft_id" not in indexes:
            op.create_index("ix_processing_jobs_client_draft_id", "processing_jobs", ["client_draft_id"])

    if "facebook_page_posts" in tables:
        if is_pg:
            for col in insp.get_columns("facebook_page_posts"):
                length = getattr(col["type"], "length", None)
                if col["name"] == "calendar_key" and length is not None and length < 120:
                    op.execute(
                        text("ALTER TABLE facebook_page_posts ALTER COLUMN calendar_key TYPE VARCHAR(120)")
                    )
        from app.services.facebook_calendar import rewrite_legacy_calendar_keys

        rewrite_legacy_calendar_keys(bind)

    if "engagement_snapshots" in tables:
        from app.services.engagement_store import rewrite_stored_rescue_buckets

        rewrite_stored_rescue_buckets(bind)


def downgrade() -> None:
    # Production had all of these before this revision existed (API startup created them),
    # so dropping them here would destroy live data. Downgrade only moves the version pointer.
    pass
