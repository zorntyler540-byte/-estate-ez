from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
import app.main as main
from app.models import Account, ProcessingJob, Sale, User
from app.schemas import CreateJobRequest


class TestJobInputFolderSecurity(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.upload_root = Path(self.tmp.name) / "uploads"
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
        self.db: Session = self.SessionLocal()
        self.user = User(
            id="user-a",
            account_id="acct-a",
            email="a@example.com",
            password_hash="x",
            role="owner",
        )
        self.sale = Sale(id="sale-a", account_id="acct-a", user_id="user-a", name="Sale A")
        self.db.add_all(
            [
                Account(id="acct-a", name="Account A"),
                Account(id="acct-b", name="Account B"),
                self.user,
                self.sale,
            ]
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_create_job_rejects_folder_outside_current_account_upload_root(self) -> None:
        other_account_folder = self.upload_root / "acct-b" / "batch-1"
        other_account_folder.mkdir(parents=True)

        with patch.object(main, "UPLOAD_ROOT", self.upload_root):
            with self.assertRaises(HTTPException) as ctx:
                main.create_job(
                    CreateJobRequest(input_folder=str(other_account_folder), sale_id=self.sale.id),
                    current=self.user,
                    db=self.db,
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "Invalid job upload path")
        self.assertEqual(self.db.query(ProcessingJob).count(), 0)

    def test_create_job_accepts_folder_inside_current_account_upload_root(self) -> None:
        own_folder = self.upload_root / "acct-a" / "batch-1"
        own_folder.mkdir(parents=True)

        with patch.object(main, "UPLOAD_ROOT", self.upload_root):
            out = main.create_job(
                CreateJobRequest(input_folder=str(own_folder), sale_id=self.sale.id),
                current=self.user,
                db=self.db,
            )

        stored = self.db.query(ProcessingJob).filter(ProcessingJob.id == out.id).one()
        self.assertEqual(stored.input_folder, str(own_folder.resolve()))
        self.assertEqual(stored.account_id, "acct-a")


if __name__ == "__main__":
    unittest.main()
