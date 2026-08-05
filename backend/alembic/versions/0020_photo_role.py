"""Add nullable photo_role for triage labels (e.g. tape exclusion from CT Seller)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0020_photo_role"
down_revision = "0019_ctseller_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("photos")}
    if "photo_role" not in cols:
        op.add_column("photos", sa.Column("photo_role", sa.String(length=32), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("photos")}
    if "photo_role" in cols:
        op.drop_column("photos", "photo_role")
