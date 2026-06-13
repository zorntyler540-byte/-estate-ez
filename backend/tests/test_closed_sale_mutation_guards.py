"""Closed sales must be immutable through catalog mutation endpoints."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import delete_lot, purge_sale_catalog, update_lot
from app.models import Account, JobStatus, Lot, ProcessingJob, Sale, User
from app.schemas import LotUpdate


class ClosedSaleMutationGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
        self.current = SimpleNamespace(id="owner-1", account_id="acct-1", email="owner@example.com", role="owner")
        with self.Session() as db:
            db.add(Account(id="acct-1", name="Test Account"))
            db.add(
                User(
                    id="owner-1",
                    account_id="acct-1",
                    email="owner@example.com",
                    password_hash="unused",
                    role="owner",
                )
            )
            db.add(
                Sale(
                    id="sale-closed",
                    account_id="acct-1",
                    user_id="owner-1",
                    name="Closed sale",
                    closed_at=datetime.now(timezone.utc),
                )
            )
            db.add(
                Lot(
                    id="lot-closed",
                    account_id="acct-1",
                    user_id="owner-1",
                    sale_id="sale-closed",
                    auction_title="Original title",
                    professional_description="Original description",
                    specification_lines_json="[]",
                    brands_json="[]",
                    labels_and_tags_json="[]",
                    condition_text="",
                    photo_count=0,
                )
            )
            db.add(
                ProcessingJob(
                    id="job-closed",
                    account_id="acct-1",
                    user_id="owner-1",
                    sale_id="sale-closed",
                    status=JobStatus.queued,
                    input_folder="/tmp/import",
                )
            )
            db.commit()

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_patch_lot_rejects_closed_sale_without_mutating(self) -> None:
        with self.Session() as db:
            with self.assertRaises(HTTPException) as raised:
                update_lot(
                    "lot-closed",
                    LotUpdate(auction_title="Changed title", is_completed=True),
                    current=self.current,
                    db=db,
                )

            self.assertEqual(raised.exception.status_code, 409)
            row = db.query(Lot).filter(Lot.id == "lot-closed").one()
            self.assertEqual(row.auction_title, "Original title")
            self.assertFalse(row.is_completed)

    def test_delete_lot_rejects_closed_sale_without_deleting(self) -> None:
        with self.Session() as db:
            with self.assertRaises(HTTPException) as raised:
                delete_lot("lot-closed", current=self.current, db=db)

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(db.query(Lot).filter(Lot.id == "lot-closed").count(), 1)

    def test_purge_sale_catalog_rejects_closed_sale_without_deleting_catalog(self) -> None:
        with self.Session() as db:
            with self.assertRaises(HTTPException) as raised:
                purge_sale_catalog("sale-closed", current=self.current, db=db)

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(db.query(Lot).filter(Lot.sale_id == "sale-closed").count(), 1)
            self.assertEqual(db.query(ProcessingJob).filter(ProcessingJob.sale_id == "sale-closed").count(), 1)


if __name__ == "__main__":
    unittest.main()
