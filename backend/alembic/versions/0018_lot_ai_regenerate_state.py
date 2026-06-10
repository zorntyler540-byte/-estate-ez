"""Persist AI regenerate in-progress state on lots (multi-replica safe)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0018_lot_ai_regenerate_state"
down_revision = "0017_appraisal_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("lots")}
    if "ai_regenerate_started_at" not in cols:
        op.add_column("lots", sa.Column("ai_regenerate_started_at", sa.DateTime(timezone=True), nullable=True))
    if "ai_regenerate_error" not in cols:
        op.add_column("lots", sa.Column("ai_regenerate_error", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("lots")}
    if "ai_regenerate_error" in cols:
        op.drop_column("lots", "ai_regenerate_error")
    if "ai_regenerate_started_at" in cols:
        op.drop_column("lots", "ai_regenerate_started_at")
