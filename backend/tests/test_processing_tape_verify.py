"""Processing job SKU boundary behavior when tape verification is unavailable."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db as app_db
from app.models import Account, Lot, Photo, ProcessingJob, Sale, User
from app.services import processing


class TestProcessingTapeVerify(unittest.TestCase):
    def test_indeterminate_tape_verify_keeps_lot_boundary_for_review(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        app_db.Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            photo_paths = [folder / "001.jpg", folder / "002.jpg"]
            for path in photo_paths:
                path.write_bytes(b"fake jpeg bytes")

            async def fake_analyze_photos_parallel_async(
                photos, *, manifest_tag: str, limit: int, on_each
            ):
                payloads = [
                    {
                        "SKU": "001",
                        "AuctionTitle": "Blue Tape First Lot",
                        "ProfessionalDescription": "First estate lot.",
                        "SpecificationLines": [],
                        "Brands": [],
                        "Condition": "GOOD",
                    },
                    {
                        "SKU": "002",
                        "AuctionTitle": "Blue Tape Second Lot",
                        "ProfessionalDescription": "Second estate lot.",
                        "SpecificationLines": [],
                        "Brands": [],
                        "Condition": "GOOD",
                    },
                ]
                for i, (photo, payload) in enumerate(zip(photos, payloads)):
                    on_each(i, len(photos), photo, payload)
                return payloads

            photo_ids = iter(["photo-001", "photo-002"])

            def fake_persist_catalog_photo(account_id: str, source: Path):
                return next(photo_ids), f"catalog/{account_id}/{source.name}"

            with Session() as session:
                session.add(Account(id="acct", name="Test Account"))
                session.add(
                    User(
                        id="user",
                        account_id="acct",
                        email="user@example.com",
                        password_hash="x",
                    )
                )
                session.add(Sale(id="sale", account_id="acct", user_id="user", name="Sale"))
                job = ProcessingJob(
                    id="job",
                    account_id="acct",
                    user_id="user",
                    sale_id="sale",
                    input_folder=str(folder),
                )
                session.add(job)
                session.commit()

                with (
                    patch.object(processing.settings, "google_api_key", "test-key"),
                    patch.object(processing.settings, "skip_tape_verify", False),
                    patch.object(
                        processing,
                        "analyze_photos_parallel_async",
                        fake_analyze_photos_parallel_async,
                    ),
                    patch.object(processing, "verify_tape_sku", return_value=None),
                    patch.object(processing, "persist_catalog_photo", fake_persist_catalog_photo),
                    patch.object(processing.job_events, "publish"),
                ):
                    processing.process_job(session, job)

                lots = session.query(Lot).order_by(Lot.sku).all()
                self.assertEqual([lot.sku for lot in lots], ["001", "002"])
                self.assertEqual([lot.photo_count for lot in lots], [1, 1])
                self.assertTrue(all(lot.sku_needs_review for lot in lots))

                photos = session.query(Photo).order_by(Photo.original_name).all()
                self.assertEqual(len(photos), 2)
                self.assertEqual({photo.lot_id for photo in photos}, {lot.id for lot in lots})


if __name__ == "__main__":
    unittest.main()
