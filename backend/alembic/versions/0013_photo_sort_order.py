"""Catalog photo display order (per lot), independent of upload time."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "0013_photo_sort_order"
down_revision = "0012_sale_zip_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = [c["name"] for c in insp.get_columns("photos")]
    if "sort_order" in cols:
        return
    op.add_column(
        "photos",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    connection = op.get_bind()
    lots = connection.execute(text("SELECT DISTINCT lot_id FROM photos WHERE lot_id IS NOT NULL")).fetchall()
    for (lot_id,) in lots:
        rows = connection.execute(
            text("SELECT id FROM photos WHERE lot_id = :lid ORDER BY created_at ASC, id ASC"),
            {"lid": lot_id},
        ).fetchall()
        for i, (pid,) in enumerate(rows):
            connection.execute(
                text("UPDATE photos SET sort_order = :so WHERE id = :id"),
                {"so": i, "id": pid},
            )


def downgrade() -> None:
    op.drop_column("photos", "sort_order")
