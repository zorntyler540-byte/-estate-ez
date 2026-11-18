"""photos.ctseller_sent_token — which gallery bytes were last sent to CT Seller.

Revision ID: 0056_photo_ctseller_sent_token
Revises: 0055_job_parent_id
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0056_photo_ctseller_sent_token"
down_revision = "0055_job_parent_id"
branch_labels = None
depends_on = None

_TABLE = "photos"
_COLUMN = "ctseller_sent_token"


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if _TABLE not in set(insp.get_table_names()):
        return
    if _COLUMN not in {c["name"] for c in insp.get_columns(_TABLE)}:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=64), nullable=True))


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if _TABLE not in set(insp.get_table_names()):
        return
    if _COLUMN in {c["name"] for c in insp.get_columns(_TABLE)}:
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
