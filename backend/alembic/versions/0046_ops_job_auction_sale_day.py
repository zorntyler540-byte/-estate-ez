"""Add auction lot hours and sale-day staffing columns to ops jobs.

Revision ID: 0046_ops_job_auction_sale_day
Revises: 0045_ops_job_cuft
"""

from alembic import op
from sqlalchemy import inspect, text

revision = "0046_ops_job_auction_sale_day"
down_revision = "0045_ops_job_cuft"
branch_labels = None
depends_on = None

COLUMNS = (
    "space_plan_areas",
    "space_plan_items",
    "projected_lots",
    "auction_questions",
    "pickup_days",
    "pickup_staff",
    "pickup_hours_per_day",
    "sale_days",
    "sale_staff",
    "sale_hours_per_day",
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "ops_jobs" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("ops_jobs")}
    for name in COLUMNS:
        if name not in cols:
            op.execute(text(f"ALTER TABLE ops_jobs ADD COLUMN {name} FLOAT DEFAULT 0 NOT NULL"))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "ops_jobs" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("ops_jobs")}
    for name in reversed(COLUMNS):
        if name in cols:
            op.execute(text(f"ALTER TABLE ops_jobs DROP COLUMN {name}"))
