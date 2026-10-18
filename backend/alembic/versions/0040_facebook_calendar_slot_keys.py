"""Rewrite Push-calendar keys to include the slot (legacy 09:00 rows)."""

from alembic import op
from sqlalchemy import inspect, text

revision = "0040_facebook_calendar_slot_keys"
down_revision = "0039_facebook_calendar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
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
        if len(parts) == 3 and parts[0] == "pushcal":
            bind.execute(
                text("UPDATE facebook_page_posts SET calendar_key = :new WHERE id = :id"),
                {"new": f"{raw}:0900", "id": row_id},
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
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
        if raw.endswith(":0900") and raw.count(":") == 3:
            bind.execute(
                text("UPDATE facebook_page_posts SET calendar_key = :new WHERE id = :id"),
                {"new": raw[: -len(":0900")], "id": row_id},
            )
