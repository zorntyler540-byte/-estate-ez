"""Audience hunt events for admin analytics (counts + metered Pro tokens)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0026_audience_hunt_events"
down_revision = "0025_lot_audience_hunt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "audience_hunt_events" in insp.get_table_names():
        return
    op.create_table(
        "audience_hunt_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("lot_id", sa.String(length=36), nullable=True),
        sa.Column("sale_id", sa.String(length=36), nullable=True),
        sa.Column("skipped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("skip_reason", sa.String(length=400), nullable=True),
        sa.Column("worthy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recommend_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("group_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("search_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actual_pro_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actual_pro_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_audience_hunt_events_account_id", "audience_hunt_events", ["account_id"])
    op.create_index("ix_audience_hunt_events_lot_id", "audience_hunt_events", ["lot_id"])
    op.create_index("ix_audience_hunt_events_sale_id", "audience_hunt_events", ["sale_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "audience_hunt_events" in insp.get_table_names():
        op.drop_table("audience_hunt_events")
