from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main, regeneration_tasks
from app.db import Base
from app.models import Account, Lot, Sale, User


class LotRegenerationClaimTests(unittest.TestCase):
    def _session_with_lot(self, *, started_at: datetime | None = None):
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
        db = session_factory()
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)

        account = Account(id="acct-1", name="Account")
        user = User(id="user-1", account_id=account.id, email="user@example.com", password_hash="hash")
        sale = Sale(id="sale-1", account_id=account.id, user_id=user.id, name="Sale")
        lot = Lot(
            id="lot-1",
            account_id=account.id,
            user_id=user.id,
            sale_id=sale.id,
            sku=None,
            auction_title="Original title",
            professional_description="Original description",
            specification_lines_json="[]",
            brands_json="[]",
            labels_and_tags_json="[]",
            condition_text="",
            photo_count=0,
            ai_regenerate_started_at=started_at,
            ai_regenerate_error="previous error",
        )
        db.add_all([account, user, sale, lot])
        db.commit()
        return db, lot

    def test_mark_lot_regenerate_started_claims_only_idle_lot(self) -> None:
        db, lot = self._session_with_lot()

        regeneration_tasks.mark_lot_regenerate_started(db, lot_id=lot.id, account_id=lot.account_id)
        db.refresh(lot)
        started_at = lot.ai_regenerate_started_at

        self.assertIsNotNone(started_at)
        self.assertIsNone(lot.ai_regenerate_error)
        with self.assertRaisesRegex(RuntimeError, "regenerate_already_running"):
            regeneration_tasks.mark_lot_regenerate_started(db, lot_id=lot.id, account_id=lot.account_id)
        db.refresh(lot)
        self.assertEqual(lot.ai_regenerate_started_at, started_at)

    def test_mark_lot_regenerate_started_reclaims_stale_lot(self) -> None:
        stale_started = datetime.now(timezone.utc) - regeneration_tasks.STALE_REGENERATE - timedelta(minutes=1)
        db, lot = self._session_with_lot(started_at=stale_started)

        regeneration_tasks.mark_lot_regenerate_started(db, lot_id=lot.id, account_id=lot.account_id)
        db.refresh(lot)

        self.assertIsNotNone(lot.ai_regenerate_started_at)
        claimed_at = lot.ai_regenerate_started_at
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=timezone.utc)
        self.assertGreater(claimed_at, stale_started)
        self.assertIsNone(lot.ai_regenerate_error)


class RegenerateEndpointTests(unittest.TestCase):
    def test_duplicate_background_start_does_not_clear_active_marker(self) -> None:
        row = SimpleNamespace(sale_id="sale-1")
        query = MagicMock()
        query.filter.return_value.first.return_value = row
        db = MagicMock()
        db.query.return_value = query
        current = SimpleNamespace(id="user-1", account_id="acct-1")
        body = SimpleNamespace(photo_ids=None, preserve_sku=False)

        with (
            patch.object(main, "_assert_sale_editable_for_lot_moves"),
            patch.object(main.regeneration_tasks, "is_regenerating", return_value=False),
            patch.object(main.regeneration_tasks, "mark_lot_regenerate_started"),
            patch.object(
                main.regeneration_tasks,
                "start_lot_regeneration_in_background",
                side_effect=RuntimeError("regenerate_already_running"),
            ),
            patch.object(main.regeneration_tasks, "finish_lot_regenerate") as finish,
        ):
            with self.assertRaises(HTTPException) as ctx:
                main.regenerate_lot_with_ai("lot-1", body, current=current, db=db)

        self.assertEqual(ctx.exception.status_code, 409)
        finish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
