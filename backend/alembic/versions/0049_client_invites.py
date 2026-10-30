"""Client upload invites: hashed link tokens, expiry, and revocation.

Revision ID: 0049_client_invites
Revises: 0048_account_feature_flags
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0049_client_invites"
down_revision = "0048_account_feature_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "users" in tables:
        cols = {c["name"] for c in insp.get_columns("users")}
        if "access_expires_at" not in cols:
            op.add_column("users", sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True))
        if "revoked_at" not in cols:
            op.add_column("users", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    if "client_invites" not in tables:
        op.create_table(
            "client_invites",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("sale_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_client_invites_account_id", "client_invites", ["account_id"])
        op.create_index("ix_client_invites_sale_id", "client_invites", ["sale_id"])
        op.create_index("ix_client_invites_user_id", "client_invites", ["user_id"])
        op.create_index("ix_client_invites_token_hash", "client_invites", ["token_hash"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "client_invites" in tables:
        op.drop_index("ix_client_invites_token_hash", table_name="client_invites")
        op.drop_index("ix_client_invites_user_id", table_name="client_invites")
        op.drop_index("ix_client_invites_sale_id", table_name="client_invites")
        op.drop_index("ix_client_invites_account_id", table_name="client_invites")
        op.drop_table("client_invites")
    if "users" in tables:
        cols = {c["name"] for c in insp.get_columns("users")}
        if "revoked_at" in cols:
            op.drop_column("users", "revoked_at")
        if "access_expires_at" in cols:
            op.drop_column("users", "access_expires_at")
