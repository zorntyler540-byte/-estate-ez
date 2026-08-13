"""Persist catalog-job SSE events + live progress snapshot (API/worker split)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0023_job_sse_progress"
down_revision = "0022_job_actual_token_usage"
branch_labels = None
depends_on = None

_JOB_COLS = (
    ("progress_phase", sa.Column("progress_phase", sa.String(length=64), nullable=True)),
    ("progress_done", sa.Column("progress_done", sa.Integer(), nullable=False, server_default="0")),
    ("progress_total", sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0")),
    (
        "progress_file_name",
        sa.Column("progress_file_name", sa.String(length=255), nullable=True),
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    existing = {c["name"] for c in insp.get_columns("processing_jobs")}
    for name, col in _JOB_COLS:
        if name not in existing:
            op.add_column("processing_jobs", col)

    tables = set(insp.get_table_names())
    if "job_sse_events" not in tables:
        op.create_table(
            "job_sse_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("account_id", sa.String(length=36), nullable=False),
            sa.Column("job_id", sa.String(length=36), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_job_sse_events_account_id", "job_sse_events", ["account_id"])
        op.create_index("ix_job_sse_events_job_id", "job_sse_events", ["job_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "job_sse_events" in tables:
        op.drop_index("ix_job_sse_events_job_id", table_name="job_sse_events")
        op.drop_index("ix_job_sse_events_account_id", table_name="job_sse_events")
        op.drop_table("job_sse_events")
    existing = {c["name"] for c in insp.get_columns("processing_jobs")}
    for name, _col in reversed(_JOB_COLS):
        if name in existing:
            op.drop_column("processing_jobs", name)
