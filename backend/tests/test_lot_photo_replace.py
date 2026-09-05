"""Regression tests for catalog photo replacement safety."""

from __future__ import annotations

import asyncio
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import UploadFile
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import catalog_storage
from app.catalog_storage import persist_catalog_photo, upload_root
from app.db import Base
from app.main import replace_lot_photo
from app.models import Account, Lot, Photo, Sale, User


def _jpeg_bytes(color: tuple[int, int, int]) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (16, 16), color).save(buf, format="JPEG")
    return buf.getvalue()


class TestLotPhotoReplace(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._old_backend_dir = catalog_storage._BACKEND_DIR
        catalog_storage._BACKEND_DIR = Path(self._tmp.name)
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self) -> None:
        self.engine.dispose()
        catalog_storage._BACKEND_DIR = self._old_backend_dir
        self._tmp.cleanup()

    def _seed_photo(self, old_bytes: bytes):
        db = self.Session()
        src = Path(self._tmp.name) / "seed" / "old.jpg"
        src.parent.mkdir(parents=True)
        src.write_bytes(old_bytes)
        _, old_storage_path = persist_catalog_photo("acct-1", src, photo_id="photo-1")
        db.add_all(
            [
                Account(id="acct-1", name="Account"),
                User(
                    id="user-1",
                    account_id="acct-1",
                    email="owner@example.com",
                    password_hash="hash",
                    role="owner",
                ),
                Sale(id="sale-1", account_id="acct-1", user_id="user-1", name="Sale"),
                Lot(
                    id="lot-1",
                    account_id="acct-1",
                    user_id="user-1",
                    sale_id="sale-1",
                    sku="1",
                    auction_title="Lot",
                    professional_description="",
                    specification_lines_json="[]",
                    brands_json="[]",
                    labels_and_tags_json="[]",
                    condition_text="",
                    photo_count=1,
                ),
                Photo(
                    id="photo-1",
                    account_id="acct-1",
                    user_id="user-1",
                    lot_id="lot-1",
                    original_name="old.jpg",
                    storage_path=old_storage_path,
                    sort_order=0,
                ),
            ]
        )
        db.commit()
        return db, db.get(User, "user-1"), old_storage_path

    def test_failed_replace_commit_keeps_original_catalog_bytes(self) -> None:
        old_bytes = _jpeg_bytes((255, 0, 0))
        db, user, old_storage_path = self._seed_photo(old_bytes)
        old_path = upload_root() / old_storage_path
        original_commit = db.commit

        def fail_commit() -> None:
            raise RuntimeError("database commit failed")

        try:
            db.commit = fail_commit  # type: ignore[method-assign]
            upload = UploadFile(file=BytesIO(_jpeg_bytes((0, 0, 255))), filename="edited.jpg")
            with self.assertRaisesRegex(RuntimeError, "database commit failed"):
                asyncio.run(replace_lot_photo("lot-1", "photo-1", user, db, upload))
            self.assertEqual(old_path.read_bytes(), old_bytes)
            catalog_files = sorted((upload_root() / "catalog" / "acct-1").iterdir())
            self.assertEqual(catalog_files, [old_path])
        finally:
            db.commit = original_commit  # type: ignore[method-assign]
            db.close()

    def test_successful_replace_moves_row_to_new_catalog_file_and_removes_old_file(self) -> None:
        old_bytes = _jpeg_bytes((255, 0, 0))
        db, user, old_storage_path = self._seed_photo(old_bytes)
        old_path = upload_root() / old_storage_path
        try:
            upload = UploadFile(file=BytesIO(_jpeg_bytes((0, 255, 0))), filename="edited.jpg")
            result = asyncio.run(replace_lot_photo("lot-1", "photo-1", user, db, upload))
            updated = db.get(Photo, "photo-1")
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertTrue(result.file_on_disk)
            self.assertNotEqual(updated.storage_path, old_storage_path)
            self.assertFalse(old_path.exists())
            self.assertTrue((upload_root() / updated.storage_path).is_file())
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
