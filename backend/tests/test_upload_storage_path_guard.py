"""Tests for account-scoped upload job folder resolution."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.upload_storage import resolve_account_upload_folder


class UploadStoragePathGuardTests(unittest.TestCase):
    def test_resolve_account_upload_folder_accepts_own_existing_batch(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch("app.catalog_storage._BACKEND_DIR", Path(td)):
            batch = Path(td) / "data" / "uploads" / "acct-1" / "batch-1"
            batch.mkdir(parents=True)

            self.assertEqual(resolve_account_upload_folder("acct-1", str(batch)), batch.resolve())

    def test_resolve_account_upload_folder_rejects_other_account_batch(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch("app.catalog_storage._BACKEND_DIR", Path(td)):
            batch = Path(td) / "data" / "uploads" / "acct-2" / "batch-1"
            batch.mkdir(parents=True)

            with self.assertRaises(HTTPException) as ctx:
                resolve_account_upload_folder("acct-1", str(batch))

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(ctx.exception.detail, "Invalid job upload path")

    def test_resolve_account_upload_folder_rejects_missing_own_batch(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch("app.catalog_storage._BACKEND_DIR", Path(td)):
            batch = Path(td) / "data" / "uploads" / "acct-1" / "missing"

            with self.assertRaises(HTTPException) as ctx:
                resolve_account_upload_folder("acct-1", str(batch))

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(ctx.exception.detail, "Job upload folder is missing")


if __name__ == "__main__":
    unittest.main()
