"""Add lot completion tracking fields

Revision ID: 0006_lot_completion
Revises: 0005_ct_categories
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0006_lot_completion"
down_revision = "0005_ct_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("lots")}
    if "is_completed" not in cols:
        default = sa.text("false") if bind.dialect.name == "postgresql" else sa.text("0")
        op.add_column("lots", sa.Column("is_completed", sa.Boolean(), nullable=False, server_default=default))
    if "completed_at" not in cols:
        op.add_column("lots", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("lots", "completed_at")
    op.drop_column("lots", "is_completed")

