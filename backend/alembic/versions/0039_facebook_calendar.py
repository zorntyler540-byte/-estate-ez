"""Push-calendar Facebook slot keys and per-account auto-approve."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0039_facebook_calendar"
down_revision = "0038_facebook_page"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "accounts" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("accounts")}
        if "facebook_calendar_auto_approve" not in cols:
            op.add_column(
                "accounts",
                sa.Column(
                    "facebook_calendar_auto_approve",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
            )
    if "facebook_page_posts" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("facebook_page_posts")}
        if "calendar_key" not in cols:
            op.add_column(
                "facebook_page_posts",
                sa.Column("calendar_key", sa.String(80), nullable=True),
            )
        if "calendar_sale_id" not in cols:
            op.add_column(
                "facebook_page_posts",
                sa.Column("calendar_sale_id", sa.Integer(), nullable=True),
            )
        indexes = {i["name"] for i in insp.get_indexes("facebook_page_posts")}
        if "ix_facebook_page_posts_calendar_key" not in indexes:
            op.create_index(
                "ix_facebook_page_posts_calendar_key",
                "facebook_page_posts",
                ["calendar_key"],
            )
        if "ix_facebook_page_posts_calendar_sale_id" not in indexes:
            op.create_index(
                "ix_facebook_page_posts_calendar_sale_id",
                "facebook_page_posts",
                ["calendar_sale_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "facebook_page_posts" in insp.get_table_names():
        indexes = {i["name"] for i in insp.get_indexes("facebook_page_posts")}
        if "ix_facebook_page_posts_calendar_sale_id" in indexes:
            op.drop_index("ix_facebook_page_posts_calendar_sale_id", table_name="facebook_page_posts")
        if "ix_facebook_page_posts_calendar_key" in indexes:
            op.drop_index("ix_facebook_page_posts_calendar_key", table_name="facebook_page_posts")
        cols = {c["name"] for c in insp.get_columns("facebook_page_posts")}
        if "calendar_sale_id" in cols:
            op.drop_column("facebook_page_posts", "calendar_sale_id")
        if "calendar_key" in cols:
            op.drop_column("facebook_page_posts", "calendar_key")
    if "accounts" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("accounts")}
        if "facebook_calendar_auto_approve" in cols:
            op.drop_column("accounts", "facebook_calendar_auto_approve")
