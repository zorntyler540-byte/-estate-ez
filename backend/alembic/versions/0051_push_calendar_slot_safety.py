"""Push-calendar slot safety: one row per slot, auto-approve retry cap, publish claim.

* ``attempt_count`` / ``next_attempt_at`` cap and back off auto-approve retries.
* ``claimed_at`` marks a ``publishing`` row so a crash mid-Graph-call is detectable.
* ``review_note`` tells the owner why a pinned slot needs attention (the system never
  deletes or moves a post that Facebook already accepted).
* Unique ``(account_id, calendar_key)`` so two publishers cannot both create a slot row.
  Older duplicates keep the best row (posted > scheduled > failed > cancelled, newest)
  and the rest lose their ``calendar_key``.

Revision ID: 0051_push_calendar_slot_safety
Revises: 0050_startup_ddl_to_alembic
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "0051_push_calendar_slot_safety"
down_revision = "0050_startup_ddl_to_alembic"
branch_labels = None
depends_on = None

_INDEX = "uq_facebook_page_posts_account_calendar_key"
_STATUS_RANK = {"posted": 0, "scheduled": 1, "publishing": 2, "failed": 3, "cancelled": 4}


def _dedupe_calendar_keys(bind) -> int:
    rows = bind.execute(
        text(
            "SELECT id, account_id, calendar_key, status, updated_at, created_at "
            "FROM facebook_page_posts WHERE calendar_key IS NOT NULL"
        )
    ).fetchall()
    groups: dict[tuple[str, str], list] = {}
    for row in rows:
        groups.setdefault((str(row[1]), str(row[2])), []).append(row)
    cleared = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: str(r[4] or r[5] or ""), reverse=True)
        members.sort(key=lambda r: _STATUS_RANK.get(str(r[3] or ""), 9))
        for loser in members[1:]:
            bind.execute(
                text(
                    "UPDATE facebook_page_posts SET calendar_key = NULL, "
                    "review_note = :note WHERE id = :id"
                ),
                {"id": loser[0], "note": f"Duplicate record for slot {loser[2]}; kept for history."},
            )
            cleared += 1
    return cleared


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "facebook_page_posts" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("facebook_page_posts")}
    if "attempt_count" not in cols:
        op.add_column(
            "facebook_page_posts",
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if "next_attempt_at" not in cols:
        op.add_column("facebook_page_posts", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    if "claimed_at" not in cols:
        op.add_column("facebook_page_posts", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    if "review_note" not in cols:
        op.add_column("facebook_page_posts", sa.Column("review_note", sa.Text(), nullable=True))

    from app.services.facebook_calendar import rewrite_legacy_calendar_keys

    rewrite_legacy_calendar_keys(bind)
    indexes = {ix["name"] for ix in inspect(bind).get_indexes("facebook_page_posts")}
    if _INDEX not in indexes:
        _dedupe_calendar_keys(bind)
        op.create_index(_INDEX, "facebook_page_posts", ["account_id", "calendar_key"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "facebook_page_posts" not in set(insp.get_table_names()):
        return
    indexes = {ix["name"] for ix in insp.get_indexes("facebook_page_posts")}
    if _INDEX in indexes:
        op.drop_index(_INDEX, table_name="facebook_page_posts")
    cols = {c["name"] for c in insp.get_columns("facebook_page_posts")}
    for name in ("review_note", "claimed_at", "next_attempt_at", "attempt_count"):
        if name in cols:
            op.drop_column("facebook_page_posts", name)
