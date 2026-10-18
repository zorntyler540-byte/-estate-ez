"""Facebook Page connection on accounts + post history table."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0038_facebook_page"
down_revision = "0037_zip_export_across_sales"
branch_labels = None
depends_on = None


_ACCOUNT_COLS = (
    ("facebook_user_id", sa.Column("facebook_user_id", sa.String(64), nullable=True)),
    ("facebook_user_token_enc", sa.Column("facebook_user_token_enc", sa.Text(), nullable=True)),
    (
        "facebook_user_token_expires_at",
        sa.Column("facebook_user_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    ),
    ("facebook_page_id", sa.Column("facebook_page_id", sa.String(64), nullable=True)),
    ("facebook_page_name", sa.Column("facebook_page_name", sa.String(255), nullable=True)),
    ("facebook_page_token_enc", sa.Column("facebook_page_token_enc", sa.Text(), nullable=True)),
    ("facebook_connected_at", sa.Column("facebook_connected_at", sa.DateTime(timezone=True), nullable=True)),
    ("facebook_token_error", sa.Column("facebook_token_error", sa.Text(), nullable=True)),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "accounts" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("accounts")}
        for name, column in _ACCOUNT_COLS:
            if name not in cols:
                op.add_column("accounts", column)

    if "facebook_page_posts" in insp.get_table_names():
        return
    op.create_table(
        "facebook_page_posts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False, index=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("lot_id", sa.String(36), sa.ForeignKey("lots.id"), nullable=True, index=True),
        sa.Column("lot_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("photo_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("facebook_page_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("facebook_post_id", sa.String(80), nullable=True),
        sa.Column("caption", sa.Text(), nullable=False, server_default=""),
        sa.Column("include_follow_hook", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(32), nullable=False, server_default="lot"),
        sa.Column("status", sa.String(20), nullable=False, server_default="failed"),
        sa.Column("scheduled_publish_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "facebook_page_posts" in insp.get_table_names():
        op.drop_table("facebook_page_posts")
    if "accounts" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("accounts")}
    for name, _column in reversed(_ACCOUNT_COLS):
        if name in cols:
            op.drop_column("accounts", name)
