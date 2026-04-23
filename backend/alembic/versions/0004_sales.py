"""Sales: table + sale_id on lots and processing_jobs

Revision ID: 0004_sales
Revises: 0003_user_id_tenancy
Create Date: 2026-04-20
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "0004_sales"
down_revision = "0003_user_id_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "sales" not in tables:
        op.create_table(
            "sales",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_sales_account_id", "sales", ["account_id"])
        op.create_index("ix_sales_user_id", "sales", ["user_id"])
        if bind.dialect.name == "postgresql":
            op.create_foreign_key("fk_sales_user_id_users", "sales", "users", ["user_id"], ["id"])

    insp = inspect(bind)
    lots_cols = {c["name"] for c in insp.get_columns("lots")}
    pj_cols = {c["name"] for c in insp.get_columns("processing_jobs")}

    if "sale_id" not in lots_cols:
        op.add_column("lots", sa.Column("sale_id", sa.String(length=36), nullable=True))
    if "sale_id" not in pj_cols:
        op.add_column("processing_jobs", sa.Column("sale_id", sa.String(length=36), nullable=True))

    conn = op.get_bind()

    users_missing_sale = conn.execute(
        text(
            """
            SELECT u.id, u.account_id
            FROM users u
            WHERE NOT EXISTS (SELECT 1 FROM sales s WHERE s.user_id = u.id)
            """
        )
    ).fetchall()
    for user_id, account_id in users_missing_sale:
        sid = str(uuid.uuid4())
        conn.execute(
            text(
                """
                INSERT INTO sales (id, account_id, user_id, name)
                VALUES (:id, :aid, :uid, :name)
                """
            ),
            {"id": sid, "aid": account_id, "uid": user_id, "name": "General"},
        )

    conn.execute(
        text(
            """
            UPDATE lots
            SET sale_id = (
                SELECT s.id FROM sales s
                WHERE s.user_id = lots.user_id
                ORDER BY s.created_at ASC
                LIMIT 1
            )
            WHERE sale_id IS NULL
            """
        )
    )
    conn.execute(
        text(
            """
            UPDATE processing_jobs
            SET sale_id = (
                SELECT s.id FROM sales s
                WHERE s.user_id = processing_jobs.user_id
                ORDER BY s.created_at ASC
                LIMIT 1
            )
            WHERE sale_id IS NULL
            """
        )
    )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("lots") as batch_op:
            batch_op.alter_column("sale_id", existing_type=sa.String(length=36), nullable=False)
        with op.batch_alter_table("processing_jobs") as batch_op:
            batch_op.alter_column("sale_id", existing_type=sa.String(length=36), nullable=False)
    else:
        op.alter_column("lots", "sale_id", existing_type=sa.String(length=36), nullable=False)
        op.alter_column("processing_jobs", "sale_id", existing_type=sa.String(length=36), nullable=False)

    idx_lots_sale = "ix_lots_sale_id"
    idx_pj_sale = "ix_processing_jobs_sale_id"
    insp = inspect(bind)
    idx_lots = {i["name"] for i in insp.get_indexes("lots")}
    idx_pj = {i["name"] for i in insp.get_indexes("processing_jobs")}
    if idx_lots_sale not in idx_lots:
        op.create_index(idx_lots_sale, "lots", ["sale_id"])
    if idx_pj_sale not in idx_pj:
        op.create_index(idx_pj_sale, "processing_jobs", ["sale_id"])

    if bind.dialect.name == "postgresql":
        insp = inspect(bind)
        fks_lots = {fk.get("name") for fk in insp.get_foreign_keys("lots")}
        fks_pj = {fk.get("name") for fk in insp.get_foreign_keys("processing_jobs")}
        if "fk_lots_sale_id_sales" not in fks_lots:
            op.create_foreign_key("fk_lots_sale_id_sales", "lots", "sales", ["sale_id"], ["id"])
        if "fk_processing_jobs_sale_id_sales" not in fks_pj:
            op.create_foreign_key(
                "fk_processing_jobs_sale_id_sales", "processing_jobs", "sales", ["sale_id"], ["id"]
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("fk_processing_jobs_sale_id_sales", "processing_jobs", type_="foreignkey")
        op.drop_constraint("fk_lots_sale_id_sales", "lots", type_="foreignkey")
        op.drop_constraint("fk_sales_user_id_users", "sales", type_="foreignkey")

    for ix, table in (("ix_processing_jobs_sale_id", "processing_jobs"), ("ix_lots_sale_id", "lots")):
        try:
            op.drop_index(ix, table_name=table)
        except Exception:
            pass

    op.drop_column("processing_jobs", "sale_id")
    op.drop_column("lots", "sale_id")
    op.drop_index("ix_sales_user_id", table_name="sales")
    op.drop_index("ix_sales_account_id", table_name="sales")
    op.drop_table("sales")
