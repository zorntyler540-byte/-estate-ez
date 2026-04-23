"""Per-user tenancy: user_id on jobs, lots, photos

Revision ID: 0003_user_id_tenancy
Revises: 0002_job_cancel_requested
Create Date: 2026-04-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "0003_user_id_tenancy"
down_revision = "0002_job_cancel_requested"
branch_labels = None
depends_on = None


def _backfill_user_id(bind, table: str) -> None:
    if bind.dialect.name == "postgresql":
        op.execute(
            text(
                f"""
                UPDATE {table} AS t
                SET user_id = sub.id
                FROM (
                    SELECT DISTINCT ON (account_id) id, account_id
                    FROM users
                    ORDER BY account_id, created_at ASC
                ) AS sub
                WHERE t.account_id = sub.account_id AND t.user_id IS NULL
                """
            )
        )
    else:
        op.execute(
            text(
                f"""
                UPDATE {table}
                SET user_id = (
                    SELECT id FROM users
                    WHERE users.account_id = {table}.account_id
                    ORDER BY users.created_at ASC
                    LIMIT 1
                )
                WHERE user_id IS NULL
                """
            )
        )


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    pj_cols = {c["name"] for c in insp.get_columns("processing_jobs")}
    lots_cols = {c["name"] for c in insp.get_columns("lots")}
    photos_cols = {c["name"] for c in insp.get_columns("photos")}

    if "user_id" not in pj_cols:
        op.add_column("processing_jobs", sa.Column("user_id", sa.String(length=36), nullable=True))
    if "user_id" not in lots_cols:
        op.add_column("lots", sa.Column("user_id", sa.String(length=36), nullable=True))
    if "user_id" not in photos_cols:
        op.add_column("photos", sa.Column("user_id", sa.String(length=36), nullable=True))

    _backfill_user_id(bind, "processing_jobs")
    _backfill_user_id(bind, "lots")
    _backfill_user_id(bind, "photos")

    insp = inspect(bind)
    if bind.dialect.name == "postgresql":
        fk_pj = "fk_processing_jobs_user_id_users"
        fk_lots = "fk_lots_user_id_users"
        fk_ph = "fk_photos_user_id_users"
        fks_pj = {fk.get("name") for fk in insp.get_foreign_keys("processing_jobs")}
        fks_lots = {fk.get("name") for fk in insp.get_foreign_keys("lots")}
        fks_ph = {fk.get("name") for fk in insp.get_foreign_keys("photos")}
        if fk_pj not in fks_pj:
            op.create_foreign_key(fk_pj, "processing_jobs", "users", ["user_id"], ["id"])
        if fk_lots not in fks_lots:
            op.create_foreign_key(fk_lots, "lots", "users", ["user_id"], ["id"])
        if fk_ph not in fks_ph:
            op.create_foreign_key(fk_ph, "photos", "users", ["user_id"], ["id"])

    ix_pj = "ix_processing_jobs_user_id"
    ix_lots = "ix_lots_user_id"
    ix_ph = "ix_photos_user_id"
    idx_pj = {i["name"] for i in insp.get_indexes("processing_jobs")}
    idx_lots = {i["name"] for i in insp.get_indexes("lots")}
    idx_ph = {i["name"] for i in insp.get_indexes("photos")}
    if ix_pj not in idx_pj:
        op.create_index(ix_pj, "processing_jobs", ["user_id"])
    if ix_lots not in idx_lots:
        op.create_index(ix_lots, "lots", ["user_id"])
    if ix_ph not in idx_ph:
        op.create_index(ix_ph, "photos", ["user_id"])

    if bind.dialect.name == "postgresql":
        op.alter_column("processing_jobs", "user_id", existing_type=sa.String(length=36), nullable=False)
        op.alter_column("lots", "user_id", existing_type=sa.String(length=36), nullable=False)
        op.alter_column("photos", "user_id", existing_type=sa.String(length=36), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("ix_photos_user_id", table_name="photos")
    op.drop_index("ix_lots_user_id", table_name="lots")
    op.drop_index("ix_processing_jobs_user_id", table_name="processing_jobs")

    if bind.dialect.name == "postgresql":
        op.drop_constraint("fk_photos_user_id_users", "photos", type_="foreignkey")
        op.drop_constraint("fk_lots_user_id_users", "lots", type_="foreignkey")
        op.drop_constraint("fk_processing_jobs_user_id_users", "processing_jobs", type_="foreignkey")

    op.drop_column("photos", "user_id")
    op.drop_column("lots", "user_id")
    op.drop_column("processing_jobs", "user_id")
