"""Add appraisal_checks table for job-site spot appraisals."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0017_appraisal_checks"
down_revision = "0016_job_usage_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "appraisal_checks" in insp.get_table_names():
        return
    op.create_table(
        "appraisal_checks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("sale_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("scene_note", sa.Text(), nullable=True),
        sa.Column("photo_paths_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("original_names_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("est_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("est_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_appraisal_checks_account_id", "appraisal_checks", ["account_id"])
    op.create_index("ix_appraisal_checks_user_id", "appraisal_checks", ["user_id"])
    op.create_index("ix_appraisal_checks_sale_id", "appraisal_checks", ["sale_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "appraisal_checks" not in insp.get_table_names():
        return
    op.drop_index("ix_appraisal_checks_sale_id", table_name="appraisal_checks")
    op.drop_index("ix_appraisal_checks_user_id", table_name="appraisal_checks")
    op.drop_index("ix_appraisal_checks_account_id", table_name="appraisal_checks")
    op.drop_table("appraisal_checks")
