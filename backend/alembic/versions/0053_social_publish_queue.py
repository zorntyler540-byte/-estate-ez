"""Durable social publish queue + Instagram / Stories targets.

* ``social_publish_batches`` / ``social_publish_jobs``: what used to live in API memory.
  A job is one post on one target. ``active_key`` is set while a job is queued / running /
  waiting for its slot time and cleared when it finishes, so the unique
  ``(account_id, active_key)`` index allows one live job per calendar slot.
  ``(account_id, idempotency_key)`` on batches makes a repeated POST return the same batch.
* ``facebook_page_posts.publish_target`` (``facebook_page`` / ``instagram_feed`` /
  ``stories``) and ``instagram_media_id``.
* ``accounts.instagram_user_id`` / ``instagram_username``: the IG professional account
  linked to the connected Page.

Revision ID: 0053_social_publish_queue
Revises: 0052_account_timezone
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0053_social_publish_queue"
down_revision = "0052_account_timezone"
branch_labels = None
depends_on = None


def _cols(insp, table: str) -> set[str]:
    return {c["name"] for c in insp.get_columns(table)}


def _indexes(insp, table: str) -> set[str]:
    return {ix["name"] for ix in insp.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "accounts" in tables:
        cols = _cols(insp, "accounts")
        if "instagram_user_id" not in cols:
            op.add_column("accounts", sa.Column("instagram_user_id", sa.String(length=64), nullable=True))
        if "instagram_username" not in cols:
            op.add_column("accounts", sa.Column("instagram_username", sa.String(length=255), nullable=True))

    if "facebook_page_posts" in tables:
        cols = _cols(insp, "facebook_page_posts")
        if "publish_target" not in cols:
            op.add_column(
                "facebook_page_posts",
                sa.Column(
                    "publish_target",
                    sa.String(length=32),
                    nullable=False,
                    server_default="facebook_page",
                ),
            )
        if "instagram_media_id" not in cols:
            op.add_column("facebook_page_posts", sa.Column("instagram_media_id", sa.String(length=80), nullable=True))

    if "social_publish_batches" not in tables:
        op.create_table(
            "social_publish_batches",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="lot"),
            sa.Column("include_follow_hook", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("force", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("idempotency_key", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
    insp = inspect(bind)
    batch_ix = _indexes(insp, "social_publish_batches")
    if "ix_social_publish_batches_account_id" not in batch_ix:
        op.create_index("ix_social_publish_batches_account_id", "social_publish_batches", ["account_id"])
    if "uq_social_publish_batches_account_idem" not in batch_ix:
        op.create_index(
            "uq_social_publish_batches_account_idem",
            "social_publish_batches",
            ["account_id", "idempotency_key"],
            unique=True,
        )

    if "social_publish_jobs" not in tables:
        op.create_table(
            "social_publish_jobs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "batch_id",
                sa.String(length=36),
                sa.ForeignKey("social_publish_batches.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lot_id", sa.String(length=36), nullable=True),
            sa.Column("calendar_key", sa.String(length=120), nullable=True),
            sa.Column("publish_target", sa.String(length=32), nullable=False, server_default="facebook_page"),
            sa.Column("item_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("active_key", sa.String(length=160), nullable=True),
            sa.Column("post_record_id", sa.String(length=36), nullable=True),
            sa.Column("state_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("result_json", sa.Text(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    insp = inspect(bind)
    job_ix = _indexes(insp, "social_publish_jobs")
    for name, cols, unique in (
        ("ix_social_publish_jobs_batch_id", ["batch_id"], False),
        ("ix_social_publish_jobs_account_status", ["account_id", "status"], False),
        ("ix_social_publish_jobs_status_run_at", ["status", "run_at"], False),
        ("uq_social_publish_jobs_account_active_key", ["account_id", "active_key"], True),
    ):
        if name not in job_ix:
            op.create_index(name, "social_publish_jobs", cols, unique=unique)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "social_publish_jobs" in tables:
        op.drop_table("social_publish_jobs")
    if "social_publish_batches" in tables:
        op.drop_table("social_publish_batches")
    if "facebook_page_posts" in tables:
        cols = _cols(insp, "facebook_page_posts")
        with op.batch_alter_table("facebook_page_posts") as batch:
            if "instagram_media_id" in cols:
                batch.drop_column("instagram_media_id")
            if "publish_target" in cols:
                batch.drop_column("publish_target")
    if "accounts" in tables:
        cols = _cols(insp, "accounts")
        with op.batch_alter_table("accounts") as batch:
            if "instagram_username" in cols:
                batch.drop_column("instagram_username")
            if "instagram_user_id" in cols:
                batch.drop_column("instagram_user_id")
