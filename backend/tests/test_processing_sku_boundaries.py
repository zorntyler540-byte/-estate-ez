"""Regression tests for batch SKU boundary handling in process_job."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Account, JobStatus, Lot, Photo, ProcessingJob, Sale, User
from app.services import processing


def _payload(sku: str | None, title: str, *, brand: str = "Columbia") -> dict:
    return {
        "SKU": sku or "",
        "SkuConfidence": "high" if sku else "",
        "item_name": title,
        "ProfessionalDescription": f"{title} photographed for inventory.",
        "brand": brand,
        "specification_lines": [
            "Type: Softshell jacket",
            "Material: Polyester blend",
        ],
    }


class TestProcessingSkuBoundaries(unittest.TestCase):
    def _run_job(self, folder: Path, payloads: list[dict]) -> tuple[list[Lot], list[Photo]]:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
        db = Session()
        self.addCleanup(db.close)
        self.addCleanup(engine.dispose)

        account = Account(name="Test Account")
        db.add(account)
        db.flush()
        user = User(account_id=account.id, email="owner@example.com", password_hash="x")
        db.add(user)
        db.flush()
        sale = Sale(account_id=account.id, user_id=user.id, name="Estate Sale")
        db.add(sale)
        db.flush()
        job = ProcessingJob(
            account_id=account.id,
            user_id=user.id,
            sale_id=sale.id,
            status=JobStatus.queued,
            input_folder=str(folder),
        )
        db.add(job)
        db.commit()

        async def fake_analyze(paths, *, manifest_tag, limit, on_each):
            _ = manifest_tag, limit
            for idx, path in enumerate(paths):
                if on_each:
                    on_each(idx, len(paths), path, payloads[idx])
            return payloads

        with (
            patch.object(processing.settings, "google_api_key", "test-key"),
            patch.object(processing.settings, "skip_tape_verify", False),
            patch.object(processing.settings, "last_known_sku_fallback", False),
            patch.object(processing, "normalize_job_folder_image", side_effect=lambda _folder, p: p),
            patch.object(processing, "analyze_photos_parallel_async", side_effect=fake_analyze),
            patch.object(processing, "verify_tape_sku", return_value=True),
            patch.object(processing.job_events, "publish"),
        ):
            processing.process_job(db, job)

        lots = db.query(Lot).order_by(Lot.sku.asc()).all()
        photos = db.query(Photo).order_by(Photo.original_name.asc()).all()
        return lots, photos

    def test_batch_adopts_verified_tape_sku_when_detail_precedes_hero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "01_detail.jpg").write_bytes(b"detail")
            (folder / "02_hero.jpg").write_bytes(b"hero")

            lots, photos = self._run_job(
                folder,
                [
                    _payload(None, "Columbia black softshell jacket detail"),
                    _payload("SA015", "Columbia black softshell jacket"),
                ],
            )

        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0].sku, "SA015")
        self.assertEqual(lots[0].photo_count, 2)
        self.assertEqual({p.lot_id for p in photos}, {lots[0].id})

    def test_batch_splits_different_verified_tape_even_when_titles_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "01_first.jpg").write_bytes(b"first")
            (folder / "02_second.jpg").write_bytes(b"second")

            lots, photos = self._run_job(
                folder,
                [
                    _payload("SA015", "Columbia black softshell jacket"),
                    _payload("SA020", "Columbia black softshell jacket"),
                ],
            )

        self.assertEqual([lot.sku for lot in lots], ["SA015", "SA020"])
        self.assertEqual([lot.photo_count for lot in lots], [1, 1])
        self.assertEqual(len({p.lot_id for p in photos}), 2)


if __name__ == "__main__":
    unittest.main()
