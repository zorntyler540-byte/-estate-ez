"""Franchise operations quotes, settlements, and monthly figures.

Revision ID: 0044_ops_franchise_jobs
Revises: 0043_security_hardening
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0044_ops_franchise_jobs"
down_revision = "0043_security_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "ops_settings" not in tables:
        op.create_table(
            "ops_settings",
            sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), primary_key=True),
            sa.Column("location_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("owner_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("bill_rate", sa.Float(), nullable=False, server_default="75"),
            sa.Column("labor_target", sa.Float(), nullable=False, server_default="0.33"),
            sa.Column("burden_rate", sa.Float(), nullable=False, server_default="0.3"),
            sa.Column("royalty_rate", sa.Float(), nullable=False, server_default="0.06"),
            sa.Column("deposit_rate", sa.Float(), nullable=False, server_default="0.2"),
            sa.Column("card_fee_rate", sa.Float(), nullable=False, server_default="0.04"),
            sa.Column("commission_rate", sa.Float(), nullable=False, server_default="0.35"),
            sa.Column("minimum_fee", sa.Float(), nullable=False, server_default="2500"),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "ops_factors" not in tables:
        op.create_table(
            "ops_factors",
            sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), primary_key=True),
            sa.Column("service", sa.String(32), primary_key=True),
            sa.Column("density", sa.String(16), primary_key=True),
            sa.Column("factor", sa.Float(), nullable=False),
        )

    if "ops_employees" not in tables:
        op.create_table(
            "ops_employees",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("pay_rate", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_ops_employees_account_id", "ops_employees", ["account_id"])

    if "ops_jobs" not in tables:
        op.create_table(
            "ops_jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("client_name", sa.String(255), nullable=False),
            sa.Column("phone", sa.String(80), nullable=False, server_default=""),
            sa.Column("email", sa.String(255), nullable=False, server_default=""),
            sa.Column("address", sa.String(500), nullable=False, server_default=""),
            sa.Column("zip", sa.String(20), nullable=False, server_default=""),
            sa.Column("sqft", sa.Float(), nullable=False, server_default="0"),
            sa.Column("density", sa.String(16), nullable=False, server_default="medium"),
            sa.Column("liquidation", sa.String(16), nullable=False, server_default="none"),
            sa.Column("include_sorting", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("include_cleanout", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("include_packing", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("include_resettling", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("space_plan_hours", sa.Float(), nullable=False, server_default="0"),
            sa.Column("projected_proceeds", sa.Float(), nullable=False, server_default="0"),
            sa.Column("sale_id", sa.String(36), sa.ForeignKey("sales.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="quoted"),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("quote_json", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_ops_jobs_account_id", "ops_jobs", ["account_id"])
        op.create_index("ix_ops_jobs_user_id", "ops_jobs", ["user_id"])
        op.create_index("ix_ops_jobs_sale_id", "ops_jobs", ["sale_id"])

    if "ops_settlements" not in tables:
        op.create_table(
            "ops_settlements",
            sa.Column("job_id", sa.String(36), sa.ForeignKey("ops_jobs.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("actual_hours", sa.Float(), nullable=False, server_default="0"),
            sa.Column("in_person_proceeds", sa.Float(), nullable=False, server_default="0"),
            sa.Column("online_proceeds", sa.Float(), nullable=False, server_default="0"),
            sa.Column("buyer_premium", sa.Float(), nullable=False, server_default="0"),
            sa.Column("shipping", sa.Float(), nullable=False, server_default="0"),
            sa.Column("misc_revenue", sa.Float(), nullable=False, server_default="0"),
            sa.Column("direct_expenses", sa.Float(), nullable=False, server_default="0"),
            sa.Column("deposit_received", sa.Float(), nullable=False, server_default="0"),
            sa.Column("balance_received", sa.Float(), nullable=False, server_default="0"),
            sa.Column("hourly_labor_cost", sa.Float(), nullable=False, server_default="0"),
            sa.Column("commission_rate", sa.Float(), nullable=False, server_default="0.35"),
            sa.Column("minimum_fee", sa.Float(), nullable=False, server_default="2500"),
            sa.Column("royalty_rate", sa.Float(), nullable=False, server_default="0.06"),
            sa.Column("settled_on", sa.String(10), nullable=False),
        )

    if "ops_month_figures" not in tables:
        op.create_table(
            "ops_month_figures",
            sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), primary_key=True),
            sa.Column("month", sa.String(7), primary_key=True),
            sa.Column("consultations", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("marketing_spend", sa.Float(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    for name in (
        "ops_month_figures",
        "ops_settlements",
        "ops_jobs",
        "ops_employees",
        "ops_factors",
        "ops_settings",
    ):
        if name in tables:
            op.drop_table(name)
