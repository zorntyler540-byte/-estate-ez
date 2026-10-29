"""Persist the phone lot-draft UUID on catalog processing jobs.

Revision ID: 0047_job_client_draft_id
Revises: 0046_ops_job_auction_sale_day
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0047_job_client_draft_id"
down_revision = "0046_ops_job_auction_sale_day"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "processing_jobs" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("processing_jobs")}
    if "client_draft_id" not in cols:
        op.add_column(
            "processing_jobs",
            sa.Column("client_draft_id", sa.String(length=36), nullable=True),
        )
    indexes = {idx["name"] for idx in insp.get_indexes("processing_jobs")}
    if "ix_processing_jobs_client_draft_id" not in indexes:
        op.create_index(
            "ix_processing_jobs_client_draft_id",
            "processing_jobs",
            ["client_draft_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "processing_jobs" not in set(insp.get_table_names()):
        return
    indexes = {idx["name"] for idx in insp.get_indexes("processing_jobs")}
    if "ix_processing_jobs_client_draft_id" in indexes:
        op.drop_index("ix_processing_jobs_client_draft_id", table_name="processing_jobs")
    cols = {c["name"] for c in insp.get_columns("processing_jobs")}
    if "client_draft_id" in cols:
        op.drop_column("processing_jobs", "client_draft_id")
