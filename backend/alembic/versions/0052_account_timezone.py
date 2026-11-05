"""Franchise timezone on ``accounts`` (IANA name; NULL keeps Pacific).

Revision ID: 0052_account_timezone
Revises: 0051_push_calendar_slot_safety
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0052_account_timezone"
down_revision = "0051_push_calendar_slot_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if "accounts" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("accounts")}
    if "timezone" not in cols:
        op.add_column("accounts", sa.Column("timezone", sa.String(length=64), nullable=True))


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if "accounts" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("accounts")}
    if "timezone" in cols:
        op.drop_column("accounts", "timezone")
