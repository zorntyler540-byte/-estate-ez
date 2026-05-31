"""Regression tests for catalog lot AI regenerate writes."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.lotting import LotOutput
from app.db import Base
from app.models import Account, Lot, Photo, Sale, User
from app.services import processing


class TestLotAiRegenerate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
        self.db: Session = self.SessionLocal()

        self.db.add_all(
            [
                Account(id="acct-1", name="Test account"),
                User(
                    id="user-1",
                    account_id="acct-1",
                    email="owner@example.com",
                    password_hash="hash",
                ),
                Sale(id="sale-1", account_id="acct-1", user_id="user-1", name="Estate sale"),
                Lot(
                    id="lot-1",
                    account_id="acct-1",
                    user_id="user-1",
                    sale_id="sale-1",
                    sku="123",
                    auction_title="Existing title",
                    professional_description="Existing description",
                    specification_lines_json="[]",
                    brands_json="[]",
                    labels_and_tags_json="[]",
                    starting_price=42.0,
                    condition_text="GOOD",
                    photo_count=0,
                    sku_needs_review=False,
                ),
            ]
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        self.tmp.cleanup()

    def _photo_file(self, name: str) -> Path:
        path = Path(self.tmp.name) / name
        path.write_bytes(b"placeholder image bytes")
        return path

    def _add_photo(self, photo_id: str, name: str, storage_path: str, sort_order: int = 0) -> None:
        self.db.add(
            Photo(
                id=photo_id,
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-1",
                original_name=name,
                storage_path=storage_path,
                sort_order=sort_order,
            )
        )
        self.db.commit()

    def test_regenerate_rejects_partial_missing_catalog_files(self) -> None:
        existing = self._photo_file("present.jpg")
        missing = Path(self.tmp.name) / "missing.jpg"
        self._add_photo("photo-1", "present.jpg", str(existing), sort_order=0)
        self._add_photo("photo-2", "missing.jpg", str(missing), sort_order=1)

        with (
            mock.patch.object(processing.settings, "google_api_key", "test-key"),
            mock.patch.object(processing, "run_single_lot_pipeline") as pipeline_mock,
            self.assertRaisesRegex(ValueError, "missing on disk.*missing.jpg"),
        ):
            processing.run_lot_ai_regenerate_and_save(
                self.db,
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-1",
                photo_ids=None,
                preserve_sku=True,
            )

        pipeline_mock.assert_not_called()
        lot = self.db.get(Lot, "lot-1")
        self.assertIsNotNone(lot)
        assert lot is not None
        self.assertEqual(lot.auction_title, "Existing title")
        self.assertEqual(lot.condition_text, "GOOD")
        self.assertEqual(lot.starting_price, 42.0)

    def test_regenerate_preserves_condition_and_price_when_pipeline_omits_them(self) -> None:
        existing = self._photo_file("present.jpg")
        self._add_photo("photo-1", "present.jpg", str(existing), sort_order=0)
        pipeline_lot = LotOutput(
            sku="999",
            auction_title="Fresh title",
            professional_description="Fresh description",
            specification_lines=["Material: Walnut"],
            brands=["Maker"],
            labels_and_tags=[],
            condition="",
            starting_price=None,
            sku_needs_review=False,
        )

        with (
            mock.patch.object(processing.settings, "google_api_key", "test-key"),
            mock.patch.object(processing.settings, "pipeline_triage_brand_audit", True),
            mock.patch.object(
                processing,
                "run_single_lot_pipeline",
                return_value=(pipeline_lot, {"triage": []}),
            ),
        ):
            processing.run_lot_ai_regenerate_and_save(
                self.db,
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-1",
                photo_ids=None,
                preserve_sku=True,
            )

        lot = self.db.get(Lot, "lot-1")
        self.assertIsNotNone(lot)
        assert lot is not None
        self.assertEqual(lot.auction_title, "Fresh title")
        self.assertEqual(lot.professional_description, "Fresh description")
        self.assertEqual(lot.condition_text, "GOOD")
        self.assertEqual(lot.starting_price, 42.0)
        self.assertEqual(lot.sku, "123")
        self.assertEqual(lot.photo_count, 1)


if __name__ == "__main__":
    unittest.main()
