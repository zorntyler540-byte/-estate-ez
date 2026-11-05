"""Dedicated catalog worker heartbeats (API in-process fallback only when stale).

Revision ID: 0052_worker_heartbeats
Revises: 0051_push_calendar_slot_safety
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0052_worker_heartbeats"
down_revision = "0051_push_calendar_slot_safety"
branch_labels = None
depends_on = None

_TABLE = "worker_heartbeats"
_INDEX = "ix_worker_heartbeats_last_seen_at"


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if _TABLE not in set(insp.get_table_names()):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("slots", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("slots_in_use", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
        insp = inspect(op.get_bind())
    indexes = {idx["name"] for idx in insp.get_indexes(_TABLE)}
    if _INDEX not in indexes:
        op.create_index(_INDEX, _TABLE, ["last_seen_at"])


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if _TABLE not in set(insp.get_table_names()):
        return
    indexes = {idx["name"] for idx in insp.get_indexes(_TABLE)}
    if _INDEX in indexes:
        op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
