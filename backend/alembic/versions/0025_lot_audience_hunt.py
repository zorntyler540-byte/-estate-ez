"""Lot audience hunt fields (collector groups + hold off CT Bids)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0025_lot_audience_hunt"
down_revision = "0024_sale_zip_export_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("lots")}
    if "audience_hold" not in cols:
        op.add_column(
            "lots",
            sa.Column("audience_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "audience_hunting_at" not in cols:
        op.add_column("lots", sa.Column("audience_hunting_at", sa.DateTime(timezone=True), nullable=True))
    if "audience_hunt_error" not in cols:
        op.add_column("lots", sa.Column("audience_hunt_error", sa.Text(), nullable=True))
    if "audience_json" not in cols:
        op.add_column(
            "lots",
            sa.Column("audience_json", sa.Text(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("lots")}
    for name in ("audience_json", "audience_hunt_error", "audience_hunting_at", "audience_hold"):
        if name in cols:
            op.drop_column("lots", name)
