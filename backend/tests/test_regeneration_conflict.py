from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Account, Lot, Photo, Sale, User
from app.services import processing


class LotRegenerationConflictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        self.tmp.cleanup()

    def _seed_lot(self):
        db = self.Session()
        account = Account(id="acct-1", name="Estate Account")
        user = User(id="user-1", account_id=account.id, email="owner@example.com", password_hash="hash")
        sale = Sale(id="sale-1", account_id=account.id, user_id=user.id, name="June Sale")
        lot = Lot(
            id="lot-1",
            account_id=account.id,
            user_id=user.id,
            sale_id=sale.id,
            sku="101",
            auction_title="Original lamp title",
            professional_description="Original description.",
            specification_lines_json=json.dumps(["Material: Brass"], ensure_ascii=True),
            brands_json="[]",
            labels_and_tags_json="[]",
            starting_price=10.0,
            condition_text="Original condition",
            photo_count=1,
        )
        photo_path = Path(self.tmp.name) / "lamp.jpg"
        photo_path.write_bytes(b"not a real image; model call is mocked")
        photo = Photo(
            id="photo-1",
            account_id=account.id,
            user_id=user.id,
            lot_id=lot.id,
            original_name="lamp.jpg",
            storage_path=str(photo_path),
            sort_order=0,
        )
        db.add_all([account, user, sale, lot, photo])
        db.commit()
        db.refresh(lot)
        return db, lot

    def test_regenerate_does_not_overwrite_saved_edit_after_start(self) -> None:
        db, lot = self._seed_lot()
        expected = processing.lot_ai_regenerate_fingerprint(lot, preserve_sku=False)
        lot.auction_title = "Human saved title"
        db.commit()

        with (
            patch.object(processing.settings, "google_api_key", "test-key"),
            patch.object(
                processing,
                "_run_photo_analysis_parallel",
                return_value=[
                    {
                        "SKU": "202",
                        "AuctionTitle": "AI generated lamp title",
                        "ProfessionalDescription": "AI generated description.",
                        "SpecificationLines": ["Material: Brass"],
                        "Brands": [],
                        "LabelsAndTags": [],
                        "Condition": "AI condition",
                        "StartingPrice": "25",
                    }
                ],
            ),
        ):
            with self.assertRaisesRegex(ValueError, "Lot changed while AI regenerate was running"):
                processing.run_lot_ai_regenerate_and_save(
                    db,
                    account_id="acct-1",
                    user_id="user-1",
                    lot_id="lot-1",
                    photo_ids=None,
                    preserve_sku=False,
                    expected_listing_fingerprint=expected,
                )

        db.refresh(lot)
        self.assertEqual(lot.auction_title, "Human saved title")
        db.close()


if __name__ == "__main__":
    unittest.main()
