"""Sale-wide AI background targets the first website photo of each lot."""

from __future__ import annotations

from PIL import Image

from app.services.plain_background import PLAIN_BG_COLORS, jpeg_bytes_rgb


def test_sale_pass_changes_only_the_first_listing_photo(tmp_path, monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.catalog_storage import persist_catalog_photo, resolve_catalog_storage_path
    from app.db import Base
    from app.hero_background_tasks import process_sale_hero_backgrounds
    from app.models import Account, Lot, Photo, Sale, User

    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)

    def fake_ai(raw: bytes, color_name: str) -> bytes:
        return jpeg_bytes_rgb(Image.new("RGB", (24, 16), PLAIN_BG_COLORS[color_name]))

    monkeypatch.setattr("app.hero_background_tasks.render_hero_ai_background", fake_ai)

    engine = create_engine(f"sqlite:///{tmp_path / 'sale-hero.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = Session()
    account = Account(id="acct-sale", name="CT")
    user = User(id="user-sale", account_id=account.id, email="sale@example.com", password_hash="x", role="owner")
    sale = Sale(id="sale-1", account_id=account.id, user_id=user.id, name="Oak Street")
    lot_a = Lot(
        id="lot-a",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="MS1",
        auction_title="Chair",
        photo_count=3,
    )
    lot_b = Lot(
        id="lot-b",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="MS2",
        auction_title="Tape only",
        photo_count=1,
    )

    def store(name: str, photo_id: str, color: tuple[int, int, int]) -> str:
        path = tmp_path / name
        Image.new("RGB", (20, 12), color).save(path, format="JPEG")
        _, key = persist_catalog_photo(account.id, path, photo_id=photo_id)
        return key

    key_tape = store("tape.jpg", "photo-tape", (20, 20, 200))
    key_first = store("first.jpg", "photo-first", (200, 20, 20))
    key_later = store("later.jpg", "photo-later", (20, 180, 20))
    key_only_tape = store("only-tape.jpg", "photo-only-tape", (20, 20, 180))
    db.add_all(
        [
            account,
            user,
            sale,
            lot_a,
            lot_b,
            Photo(
                id="photo-tape",
                account_id=account.id,
                user_id=user.id,
                lot_id=lot_a.id,
                original_name="tape.jpg",
                storage_path=key_tape,
                sort_order=0,
                photo_role="tape",
            ),
            Photo(
                id="photo-first",
                account_id=account.id,
                user_id=user.id,
                lot_id=lot_a.id,
                original_name="first.jpg",
                storage_path=key_first,
                sort_order=1,
                photo_role="item_front",
            ),
            Photo(
                id="photo-later",
                account_id=account.id,
                user_id=user.id,
                lot_id=lot_a.id,
                original_name="later.jpg",
                storage_path=key_later,
                sort_order=2,
                photo_role="hero",
            ),
            Photo(
                id="photo-only-tape",
                account_id=account.id,
                user_id=user.id,
                lot_id=lot_b.id,
                original_name="only-tape.jpg",
                storage_path=key_only_tape,
                sort_order=0,
                photo_role="tape",
            ),
        ]
    )
    db.commit()

    summary = process_sale_hero_backgrounds(
        db,
        account_id=account.id,
        sale_id=sale.id,
        lot_ids=[lot_a.id, lot_b.id],
        color="gray",
    )
    assert summary["updated"] == 1
    assert summary["skipped"] == 1
    assert summary["failed"] == 0
    with Image.open(resolve_catalog_storage_path(key_first)) as im:
        pixel = im.getpixel((2, 2))
        assert abs(pixel[0] - pixel[1]) < 20 and pixel[0] > 200
    with Image.open(resolve_catalog_storage_path(key_tape)) as im:
        assert im.getpixel((2, 2))[2] > 150
    with Image.open(resolve_catalog_storage_path(key_later)) as im:
        assert im.getpixel((2, 2))[1] > 150
    with Image.open(resolve_catalog_storage_path(key_only_tape)) as im:
        assert im.getpixel((2, 2))[2] > 150
    first = db.get(Photo, "photo-first")
    assert first is not None and first.plain_bg_saved is True


def test_sale_endpoint_queues_without_calling_gemini(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.auth import get_current_user
    from app.db import Base, get_db
    from app.main import app
    from app.models import Account, Lot, Sale, User

    engine = create_engine(f"sqlite:///{tmp_path / 'queue.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = Session()
    account = Account(id="acct-q", name="CT")
    user = User(id="user-q", account_id=account.id, email="q@example.com", password_hash="x", role="owner")
    sale = Sale(id="sale-q", account_id=account.id, user_id=user.id, name="Queue")
    lot = Lot(
        id="lot-q",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="MS9",
        auction_title="Table",
        photo_count=0,
    )
    db.add_all([account, user, sale, lot])
    db.commit()

    calls: list[dict] = []

    def fake_start(**kwargs):
        calls.append(kwargs)
        if len(calls) > 1:
            raise RuntimeError("hero_background_already_running")
        return {
            "running": True,
            "color": kwargs["color"],
            "total": len(kwargs["lot_ids"]),
            "done": 0,
            "updated": 0,
            "failed": 0,
            "skipped": 0,
            "current_sku": None,
            "last_error": None,
        }

    monkeypatch.setattr("app.hero_background_tasks.start_sale_hero_backgrounds", fake_start)

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    try:
        started = client.post("/sales/sale-q/hero-background", json={"color": "white"})
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["running"] is True
        assert body["color"] == "white"
        assert body["total"] == 1
        assert calls[0]["lot_ids"] == ["lot-q"]
        assert calls[0]["color"] == "white"
        again = client.post("/sales/sale-q/hero-background", json={"color": "gray"})
        assert again.status_code == 409, again.text
    finally:
        app.dependency_overrides.clear()
        db.close()
