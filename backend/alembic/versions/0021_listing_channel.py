"""Add listing_channel to lots and processing jobs (ctseller vs facebook)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0021_listing_channel"
down_revision = "0020_photo_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    lot_cols = {c["name"] for c in insp.get_columns("lots")}
    if "listing_channel" not in lot_cols:
        op.add_column(
            "lots",
            sa.Column(
                "listing_channel",
                sa.String(length=32),
                nullable=False,
                server_default="ctseller",
            ),
        )

    job_cols = {c["name"] for c in insp.get_columns("processing_jobs")}
    if "listing_channel" not in job_cols:
        op.add_column(
            "processing_jobs",
            sa.Column(
                "listing_channel",
                sa.String(length=32),
                nullable=False,
                server_default="ctseller",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    job_cols = {c["name"] for c in insp.get_columns("processing_jobs")}
    if "listing_channel" in job_cols:
        op.drop_column("processing_jobs", "listing_channel")
    lot_cols = {c["name"] for c in insp.get_columns("lots")}
    if "listing_channel" in lot_cols:
        op.drop_column("lots", "listing_channel")
