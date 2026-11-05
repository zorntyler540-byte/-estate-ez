"""processing_jobs.parent_job_id (website batch split into per-lot jobs).

Revision ID: 0055_job_parent_id
Revises: 0054_worker_heartbeats
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0055_job_parent_id"
down_revision = "0054_worker_heartbeats"
branch_labels = None
depends_on = None

_TABLE = "processing_jobs"
_COLUMN = "parent_job_id"
_INDEX = "ix_processing_jobs_parent_job_id"


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if _COLUMN not in {c["name"] for c in insp.get_columns(_TABLE)}:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=36), nullable=True))
        insp = inspect(op.get_bind())
    if _INDEX not in {idx["name"] for idx in insp.get_indexes(_TABLE)}:
        op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if _INDEX in {idx["name"] for idx in insp.get_indexes(_TABLE)}:
        op.drop_index(_INDEX, table_name=_TABLE)
    if _COLUMN in {c["name"] for c in insp.get_columns(_TABLE)}:
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
