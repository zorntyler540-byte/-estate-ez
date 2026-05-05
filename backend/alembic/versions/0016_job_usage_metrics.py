"""Add per-job usage metrics for master-admin analytics."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0016_job_usage_metrics"
down_revision = "0015_account_admin_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("processing_jobs")}
    if "analyzed_photos" not in cols:
        op.add_column(
            "processing_jobs",
            sa.Column("analyzed_photos", sa.Integer(), nullable=False, server_default="0"),
        )
    if "failed_photos" not in cols:
        op.add_column(
            "processing_jobs",
            sa.Column("failed_photos", sa.Integer(), nullable=False, server_default="0"),
        )
    if "est_input_tokens" not in cols:
        op.add_column(
            "processing_jobs",
            sa.Column("est_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        )
    if "est_output_tokens" not in cols:
        op.add_column(
            "processing_jobs",
            sa.Column("est_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("processing_jobs")}
    if "est_output_tokens" in cols:
        op.drop_column("processing_jobs", "est_output_tokens")
    if "est_input_tokens" in cols:
        op.drop_column("processing_jobs", "est_input_tokens")
    if "failed_photos" in cols:
        op.drop_column("processing_jobs", "failed_photos")
    if "analyzed_photos" in cols:
        op.drop_column("processing_jobs", "analyzed_photos")
