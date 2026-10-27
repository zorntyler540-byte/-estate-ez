"""Auction Watcher switches on accounts.

Revision ID: 0047_account_feature_flags
Revises: 0046_ops_job_auction_sale_day
"""

from alembic import op
from sqlalchemy import inspect, text

revision = "0047_account_feature_flags"
down_revision = "0046_ops_job_auction_sale_day"
branch_labels = None
depends_on = None

COLUMNS = ("feature_analytics", "feature_facebook")


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "accounts" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("accounts")}
    for name in COLUMNS:
        if name not in cols:
            op.execute(text(f"ALTER TABLE accounts ADD COLUMN {name} BOOLEAN NOT NULL DEFAULT TRUE"))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "accounts" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("accounts")}
    for name in COLUMNS:
        if name in cols:
            op.execute(text(f"ALTER TABLE accounts DROP COLUMN {name}"))
