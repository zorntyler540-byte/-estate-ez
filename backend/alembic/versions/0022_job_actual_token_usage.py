"""Add actual (metered) Gemini token usage columns to processing_jobs, split by model tier."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0022_job_actual_token_usage"
down_revision = "0021_listing_channel"
branch_labels = None
depends_on = None

_COLUMNS = (
    "actual_flash_input_tokens",
    "actual_flash_output_tokens",
    "actual_pro_input_tokens",
    "actual_pro_output_tokens",
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    existing = {c["name"] for c in insp.get_columns("processing_jobs")}
    for name in _COLUMNS:
        if name not in existing:
            op.add_column(
                "processing_jobs",
                sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    existing = {c["name"] for c in insp.get_columns("processing_jobs")}
    for name in _COLUMNS:
        if name in existing:
            op.drop_column("processing_jobs", name)
