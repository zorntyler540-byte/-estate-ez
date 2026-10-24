"""Rename ops job volume from square footage to cubic footage.

Revision ID: 0045_ops_job_cuft
Revises: 0044_ops_franchise_jobs
"""

from alembic import op
from sqlalchemy import inspect

revision = "0045_ops_job_cuft"
down_revision = "0044_ops_franchise_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "ops_jobs" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("ops_jobs")}
    if "cuft" in cols or "sqft" not in cols:
        return
    op.execute("ALTER TABLE ops_jobs RENAME COLUMN sqft TO cuft")


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "ops_jobs" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("ops_jobs")}
    if "sqft" in cols or "cuft" not in cols:
        return
    op.execute("ALTER TABLE ops_jobs RENAME COLUMN cuft TO sqft")
