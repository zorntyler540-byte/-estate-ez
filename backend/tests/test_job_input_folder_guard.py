from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException

from app import main


class JobInputFolderGuardTests(unittest.TestCase):
    def test_job_input_must_stay_under_account_upload_root(self) -> None:
        old_root = main.UPLOAD_ROOT
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            main.UPLOAD_ROOT = root
            try:
                own_batch = root / "acct-1" / "batch-a"
                own_batch.mkdir(parents=True)
                sibling_batch = root / "acct-2" / "batch-b"
                sibling_batch.mkdir(parents=True)
                outside = root.parent / f"{root.name}-outside"
                outside.mkdir(parents=True, exist_ok=True)

                resolved = main._account_upload_folder_for_job_input("acct-1", str(own_batch))
                self.assertEqual(resolved, own_batch)

                with self.assertRaises(HTTPException):
                    main._account_upload_folder_for_job_input("acct-1", str(sibling_batch))
                with self.assertRaises(HTTPException):
                    main._account_upload_folder_for_job_input("acct-1", str(outside))
            finally:
                main.UPLOAD_ROOT = old_root


if __name__ == "__main__":
    unittest.main()
