"""Lots that received photos after they were already cataloged."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import get_current_user
from app.db import Base, get_db
from app.main import app
from app.models import Account, Lot, Photo, Sale, User
from app.services.photo_adds import LATE_PHOTO_ADD_GAP, added_later_photo_ids, as_utc


def test_added_later_ignores_the_original_burst() -> None:
    start = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
    photos = [
        _photo("a", start),
        _photo("b", start + timedelta(minutes=20)),
        _photo("c", start + LATE_PHOTO_ADD_GAP),
        _photo("d", start + timedelta(days=2)),
    ]
    assert added_later_photo_ids(photos) == {"c", "d"}
    assert added_later_photo_ids(photos[:1]) == set()


def test_as_utc_accepts_naive_and_sqlite_strings() -> None:
    naive = datetime(2026, 10, 1, 12, 0, 0)
    assert as_utc(naive).tzinfo is not None
    assert as_utc("2026-10-01 12:00:00") == as_utc(naive)


def test_photo_adds_lists_only_late_uploads_in_the_window(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'adds.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = Session()
    now = datetime(2026, 10, 3, 18, 0, tzinfo=timezone.utc)
    account = Account(id="acct-add", name="CT")
    owner = User(id="owner", account_id=account.id, email="tyler@example.com", password_hash="x", role="owner")
    aunt = User(id="aunt", account_id=account.id, email="aunt@example.com", password_hash="x", role="catalogger")
    sale = Sale(id="sale-1", account_id=account.id, user_id=owner.id, name="Justina")
    other = Sale(id="sale-2", account_id=account.id, user_id=owner.id, name="Other")
    db.add_all([account, owner, aunt, sale, other])

    burst = now - timedelta(days=3)
    late = now - timedelta(hours=5)
    old_late = now - timedelta(days=20)
    db.add_all(
        [
            _lot("lot-late", account.id, owner.id, sale.id, "JL102", "Oak dresser"),
            _photo_row("p1", account.id, owner.id, "lot-late", burst),
            _photo_row("p2", account.id, owner.id, "lot-late", burst + timedelta(minutes=8)),
            _photo_row("p3", account.id, aunt.id, "lot-late", late),
            _photo_row("p4", account.id, aunt.id, "lot-late", late + timedelta(minutes=4)),
            _lot("lot-burst", account.id, owner.id, sale.id, "MS10", "Mixer"),
            _photo_row("b1", account.id, owner.id, "lot-burst", now - timedelta(hours=1)),
            _photo_row("b2", account.id, owner.id, "lot-burst", now - timedelta(minutes=40)),
            _lot("lot-old", account.id, owner.id, sale.id, "DK4", "Clock"),
            _photo_row("o1", account.id, owner.id, "lot-old", old_late - timedelta(days=1)),
            _photo_row("o2", account.id, aunt.id, "lot-old", old_late),
            _lot("lot-other", account.id, owner.id, other.id, "ZZ1", "Vase"),
            _photo_row("z1", account.id, owner.id, "lot-other", burst),
            _photo_row("z2", account.id, aunt.id, "lot-other", late),
        ]
    )
    db.commit()

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    client = TestClient(app)
    try:
        res = client.get("/sales/sale-1/photo-adds")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["window_days"] == 14
        assert [row["lot_id"] for row in body["lots"]] == ["lot-late"]
        row = body["lots"][0]
        assert row["sku"] == "JL102"
        assert row["auction_title"] == "Oak dresser"
        assert row["added_count"] == 2
        assert row["added_by"] == ["aunt@example.com"]

        photos = client.get("/lots/lot-late/photos")
        assert photos.status_code == 200, photos.text
        flags = {p["id"]: p["added_later"] for p in photos.json()}
        assert flags == {"p1": False, "p2": False, "p3": True, "p4": True}

        missing = client.get("/sales/missing-sale/photo-adds")
        assert missing.status_code == 404
    finally:
        app.dependency_overrides.clear()
        db.close()


def _photo(photo_id: str, created_at: datetime):
    from types import SimpleNamespace

    return SimpleNamespace(id=photo_id, created_at=created_at)


def _lot(lot_id: str, account_id: str, user_id: str, sale_id: str, sku: str, title: str) -> Lot:
    return Lot(
        id=lot_id,
        account_id=account_id,
        user_id=user_id,
        sale_id=sale_id,
        sku=sku,
        auction_title=title,
        photo_count=2,
    )


def _photo_row(photo_id: str, account_id: str, user_id: str, lot_id: str, created_at: datetime) -> Photo:
    return Photo(
        id=photo_id,
        account_id=account_id,
        user_id=user_id,
        lot_id=lot_id,
        original_name=f"{photo_id}.jpg",
        storage_path=f"catalog/{photo_id}.jpg",
        created_at=created_at,
    )
