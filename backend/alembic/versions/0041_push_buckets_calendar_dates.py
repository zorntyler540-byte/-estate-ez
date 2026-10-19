"""Split Rescue buckets and date-stamp Push-calendar keys.

Revision ID: 0041_push_buckets_calendar_dates
Revises: 0040_facebook_calendar_slot_keys
"""

from alembic import op
from sqlalchemy import inspect, text

revision = "0041_push_buckets_calendar_dates"
down_revision = "0040_facebook_calendar_slot_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "engagement_snapshots" in insp.get_table_names():
        from app.services.engagement_store import rewrite_stored_rescue_buckets

        rewrite_stored_rescue_buckets(bind)
    if "facebook_page_posts" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("facebook_page_posts")}
    if "calendar_key" not in cols:
        return
    if bind.dialect.name.startswith("postgres"):
        bind.execute(text("ALTER TABLE facebook_page_posts ALTER COLUMN calendar_key TYPE VARCHAR(120)"))
    from app.services.facebook_calendar import rewrite_legacy_calendar_keys

    rewrite_legacy_calendar_keys(bind)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "engagement_snapshots" in insp.get_table_names():
        bind.execute(
            text(
                "UPDATE engagement_snapshots SET bucket = 'Rescue' "
                "WHERE bucket IN ('No interest', 'Needs eyes')"
            )
        )
    if "facebook_page_posts" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("facebook_page_posts")}
    if "calendar_key" not in cols:
        return
    rows = bind.execute(
        text(
            "SELECT id, calendar_key FROM facebook_page_posts "
            "WHERE calendar_key IS NOT NULL AND calendar_key LIKE 'pushcal:%'"
        )
    ).fetchall()
    for row_id, key in rows:
        raw = str(key or "").strip()
        parts = raw.split(":")
        if len(parts) == 5 and parts[0] == "pushcal" and len(parts[3]) == 8:
            bind.execute(
                text("UPDATE facebook_page_posts SET calendar_key = :new WHERE id = :id"),
                {"new": f"pushcal:{parts[1]}:{parts[2]}:{parts[4]}", "id": row_id},
            )
