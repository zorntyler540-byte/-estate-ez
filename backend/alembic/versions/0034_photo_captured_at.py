"""Camera shutter time on catalog photos (not server ingest)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0034_photo_captured_at"
down_revision = "0033_lot_ctseller_sold_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "photos" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("photos")}
    if "captured_at" not in cols:
        op.add_column("photos", sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "photos" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("photos")}
    if "captured_at" in cols:
        op.drop_column("photos", "captured_at")
