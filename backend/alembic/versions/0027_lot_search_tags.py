"""CT Seller search tags on lots (item.tag keywords)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0027_lot_search_tags"
down_revision = "0026_audience_hunt_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "lots" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("lots")}
    if "search_tags_json" in cols:
        return
    op.add_column(
        "lots",
        sa.Column("search_tags_json", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "lots" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("lots")}
    if "search_tags_json" not in cols:
        return
    op.drop_column("lots", "search_tags_json")
