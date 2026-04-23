"""Add closed_at to sales

Revision ID: 0007_sale_closed_at
Revises: 0006_lot_completion
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0007_sale_closed_at"
down_revision = "0006_lot_completion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("sales")}
    if "closed_at" not in cols:
        op.add_column("sales", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("sales", "closed_at")

