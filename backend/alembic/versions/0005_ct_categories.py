"""Add CT category fields to lots

Revision ID: 0005_ct_categories
Revises: 0004_sales
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0005_ct_categories"
down_revision = "0004_sales"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("lots")}
    if "ct_category" not in cols:
        op.add_column("lots", sa.Column("ct_category", sa.String(length=120), nullable=True))
    if "ct_subcategory" not in cols:
        op.add_column("lots", sa.Column("ct_subcategory", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("lots", "ct_subcategory")
    op.drop_column("lots", "ct_category")

