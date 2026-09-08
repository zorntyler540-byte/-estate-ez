"""Move lots between catalog sales."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import get_current_user
from app.db import Base, get_db
from app.main import app
from app.models import Account, Lot, Photo, Sale, User
from app.services.lot_moves import drop_lot_photos_from_sale_covers, move_lots_to_sale
from app.services.sale_covers import parse_cover_photo_ids


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'moves.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _seed(db):
    account = Account(id="acct-m", name="CT")
    user = User(
        id="user-m",
        account_id=account.id,
        email="moves@example.com",
        password_hash="x",
        role="owner",
    )
    sale_a = Sale(id="sale-a", account_id=account.id, user_id=user.id, name="Alpha")
    sale_b = Sale(id="sale-b", account_id=account.id, user_id=user.id, name="Bravo")
    sale_closed = Sale(
        id="sale-c",
        account_id=account.id,
        user_id=user.id,
        name="Closed",
        closed_at=datetime.now(timezone.utc),
    )
    lot = Lot(
        id="lot-1",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale_a.id,
        sku="042",
        auction_title="Lamp",
    )
    photo = Photo(
        id="photo-1",
        account_id=account.id,
        user_id=user.id,
        lot_id=lot.id,
        original_name="lamp.jpg",
        storage_path="catalog/a/lamp.jpg",
        photo_role="hero",
        sort_order=0,
    )
    sale_a.ctseller_cover_photo_ids = '["photo-1","other"]'
    db.add_all([account, user, sale_a, sale_b, sale_closed, lot, photo])
    db.commit()
    return account, user, sale_a, sale_b, sale_closed, lot


def test_drop_cover_photos_for_moved_lot(tmp_path) -> None:
    Session = _session(tmp_path)
    db = Session()
    _account, _user, sale_a, _b, _c, lot = _seed(db)
    changed = drop_lot_photos_from_sale_covers(db, sale_a, lot.id, lot.account_id)
    db.commit()
    db.refresh(sale_a)
    assert changed is True
    assert parse_cover_photo_ids(sale_a.ctseller_cover_photo_ids) == ["other"]


def test_move_lots_helper_skips_same_sale(tmp_path) -> None:
    Session = _session(tmp_path)
    db = Session()
    _account, _user, sale_a, sale_b, _c, lot = _seed(db)
    moved, skipped = move_lots_to_sale(
        db, account_id=lot.account_id, lot_ids=[lot.id], destination=sale_a
    )
    assert moved == []
    assert skipped == [lot.id]
    moved, skipped = move_lots_to_sale(
        db, account_id=lot.account_id, lot_ids=[lot.id], destination=sale_b
    )
    assert [row.id for row in moved] == [lot.id]
    assert skipped == []
    db.refresh(lot)
    assert lot.sale_id == sale_b.id


def test_move_lots_api_and_closed_destination(tmp_path) -> None:
    Session = _session(tmp_path)
    db = Session()
    _account, user, sale_a, sale_b, sale_closed, lot = _seed(db)

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    try:
        closed = client.post(f"/lots/{lot.id}/move", json={"sale_id": sale_closed.id})
        assert closed.status_code == 409, closed.text

        same = client.post(f"/lots/{lot.id}/move", json={"sale_id": sale_a.id})
        assert same.status_code == 400, same.text

        ok = client.post("/lots/move-to-sale", json={"lot_ids": [lot.id], "sale_id": sale_b.id})
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["moved_count"] == 1
        assert body["destination_sale_id"] == sale_b.id
        assert body["moved"][0]["sale_id"] == sale_b.id
        assert body["moved"][0]["sku"] == "042"

        shown = client.get(f"/lots/{lot.id}")
        assert shown.status_code == 200
        assert shown.json()["sale_id"] == sale_b.id
    finally:
        app.dependency_overrides.clear()
        db.close()
