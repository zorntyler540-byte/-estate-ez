"""Sale-wide ZIP export kind: all catalog photos vs one hero per lot."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0024_sale_zip_export_kind"
down_revision = "0023_job_sse_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("sale_photo_zip_exports")}
    if "kind" not in cols:
        op.add_column(
            "sale_photo_zip_exports",
            sa.Column("kind", sa.String(length=20), nullable=False, server_default="all"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("sale_photo_zip_exports")}
    if "kind" in cols:
        op.drop_column("sale_photo_zip_exports", "kind")
