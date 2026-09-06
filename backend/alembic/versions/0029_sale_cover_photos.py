"""Persist the four CT Seller sale cover photo ids on a catalog sale."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0029_sale_cover_photos"
down_revision = "0028_lot_search_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "sales" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("sales")}
    if "ctseller_cover_photo_ids" in cols:
        return
    op.add_column("sales", sa.Column("ctseller_cover_photo_ids", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "sales" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("sales")}
    if "ctseller_cover_photo_ids" not in cols:
        return
    op.drop_column("sales", "ctseller_cover_photo_ids")
