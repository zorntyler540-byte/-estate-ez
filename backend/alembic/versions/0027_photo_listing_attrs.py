"""Persist triage fields used for shopper-friendly listing photo order."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0027_photo_listing_attrs"
down_revision = "0026_audience_hunt_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "photos" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("photos")}
    if "distinct_item_id" not in cols:
        op.add_column("photos", sa.Column("distinct_item_id", sa.String(length=64), nullable=True))
    if "is_primary_front" not in cols:
        op.add_column("photos", sa.Column("is_primary_front", sa.Boolean(), nullable=True))
    if "shows_whole_lot" not in cols:
        op.add_column("photos", sa.Column("shows_whole_lot", sa.Boolean(), nullable=True))
    if "shows_measuring_scale" not in cols:
        op.add_column("photos", sa.Column("shows_measuring_scale", sa.Boolean(), nullable=True))
    if "label_legibility" not in cols:
        op.add_column("photos", sa.Column("label_legibility", sa.String(length=16), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "photos" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("photos")}
    for name in (
        "label_legibility",
        "shows_measuring_scale",
        "shows_whole_lot",
        "is_primary_front",
        "distinct_item_id",
    ):
        if name in cols:
            op.drop_column("photos", name)
