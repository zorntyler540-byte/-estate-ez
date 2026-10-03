"""Remember which catalog photos have a plain-background original saved."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0036_photo_plain_bg_saved"
down_revision = "0035_ctseller_history_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "photos" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("photos")}
    if "plain_bg_saved" in cols:
        return
    op.add_column(
        "photos",
        sa.Column("plain_bg_saved", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "photos" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("photos")}
    if "plain_bg_saved" in cols:
        op.drop_column("photos", "plain_bg_saved")
