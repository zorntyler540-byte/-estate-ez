"""Account default and per-sale override for the CT Seller description note.

Revision ID: 0057_standard_description
Revises: 0056_photo_ctseller_sent_token

``accounts.standard_description`` is the franchise default. ``sales.standard_description``
NULL means inherit that default; a string (including empty) is the sale override.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0057_standard_description"
down_revision = "0056_photo_ctseller_sent_token"
branch_labels = None
depends_on = None


def _add(table: str, column: str) -> None:
    insp = inspect(op.get_bind())
    if table not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns(table)}
    if column not in cols:
        op.add_column(table, sa.Column(column, sa.Text(), nullable=True))


def _drop(table: str, column: str) -> None:
    insp = inspect(op.get_bind())
    if table not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns(table)}
    if column not in cols:
        return
    with op.batch_alter_table(table) as batch:
        batch.drop_column(column)


def upgrade() -> None:
    _add("accounts", "standard_description")
    _add("sales", "standard_description")


def downgrade() -> None:
    _drop("sales", "standard_description")
    _drop("accounts", "standard_description")
