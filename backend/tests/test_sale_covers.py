"""Sale cover picker helpers — no live CT Seller."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.ctseller import parse_sale_image_secondary_urls, pick_profile_sale_image
from app.services.sale_covers import (
    cover_candidate_photos,
    parse_cover_photo_ids,
    serialize_cover_photo_ids,
    suggest_cover_photos,
    validate_cover_photo_ids,
)


def _lot(lot_id: str, sku: str) -> SimpleNamespace:
    return SimpleNamespace(id=lot_id, sku=sku, created_at=None, auction_title=f"Lot {sku}")


def _photo(photo_id: str, lot_id: str, role: str | None, sort_order: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        id=photo_id,
        lot_id=lot_id,
        photo_role=role,
        sort_order=sort_order,
        created_at=None,
        original_name=f"{photo_id}.jpg",
        storage_path=f"catalog/a/{photo_id}.jpg",
        is_primary_front=False,
        shows_whole_lot=role == "hero",
        shows_measuring_scale=False,
        distinct_item_id=None,
        label_legibility=None,
    )


def test_parse_and_serialize_cover_ids() -> None:
    assert parse_cover_photo_ids(None) == []
    assert parse_cover_photo_ids("not-json") == []
    assert parse_cover_photo_ids('["a","a","b","c","d","e"]') == ["a", "b", "c", "d"]
    assert serialize_cover_photo_ids(["x", "x", "y"]) == '["x", "y"]'


def test_suggest_skips_tape_and_uses_sku_order() -> None:
    lots = [_lot("l2", "020"), _lot("l1", "010"), _lot("l3", "030"), _lot("l4", "040")]
    photos = [
        _photo("tape", "l1", "tape"),
        _photo("h1", "l1", "hero"),
        _photo("h2", "l2", "hero"),
        _photo("h3", "l3", "item_front"),
        _photo("h4", "l4", "hero"),
        _photo("extra", "l1", "item_front", sort_order=2),
    ]
    picked = suggest_cover_photos(lots, photos)  # type: ignore[arg-type]
    assert [p.id for p in picked] == ["h1", "h2", "h3", "h4"]


def test_suggest_fills_from_extras_when_fewer_lots() -> None:
    lots = [_lot("l1", "001"), _lot("l2", "002")]
    photos = [
        _photo("h1", "l1", "hero"),
        _photo("a1", "l1", "item_front", sort_order=2),
        _photo("h2", "l2", "hero"),
        _photo("a2", "l2", "angle_only", sort_order=2),
        _photo("tape", "l2", "tape", sort_order=3),
    ]
    picked = suggest_cover_photos(lots, photos)  # type: ignore[arg-type]
    assert [p.id for p in picked] == ["h1", "h2", "a1", "a2"]
    assert "tape" not in {p.id for p in picked}


def test_candidates_include_hero_and_cap_extras() -> None:
    lots = [_lot("l1", "001")]
    photos = [_photo("h1", "l1", "hero")] + [
        _photo(f"e{i}", "l1", "item_front", sort_order=i + 1) for i in range(8)
    ]
    cards = cover_candidate_photos(lots, photos, extras_per_lot=3)  # type: ignore[arg-type]
    assert cards[0].id == "h1"
    assert len(cards) == 4  # hero + 3 extras


def test_validate_rejects_tape_and_requires_four() -> None:
    photos = [
        _photo("h1", "l1", "hero"),
        _photo("h2", "l1", "item_front"),
        _photo("h3", "l1", "angle_only"),
        _photo("tape", "l1", "tape"),
    ]
    assert validate_cover_photo_ids(photo_ids=["h1", "h2"], sale_photos=photos, require_four=False) == [
        "h1",
        "h2",
    ]
    try:
        validate_cover_photo_ids(photo_ids=["h1", "h2", "h3"], sale_photos=photos, require_four=True)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "exactly 4" in str(exc)
    try:
        validate_cover_photo_ids(
            photo_ids=["h1", "h2", "h3", "tape"], sale_photos=photos, require_four=True
        )
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "tape" in str(exc).lower()


def test_cover_photos_api_save_and_push(tmp_path) -> None:
    from unittest.mock import MagicMock, patch

    from fastapi.testclient import TestClient
    from PIL import Image
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.auth import get_current_user
    from app.db import Base, get_db
    from app.main import app
    from app.models import Account, Lot, Photo, Sale, User

    engine = create_engine(f"sqlite:///{tmp_path / 'covers.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = Session()
    account = Account(id="acct-c", name="CT")
    user = User(
        id="user-c",
        account_id=account.id,
        email="covers@example.com",
        password_hash="x",
        role="owner",
    )
    sale = Sale(id="sale-c", account_id=account.id, user_id=user.id, name="Yard", ctseller_sale_id=99)
    db.add_all([account, user, sale])
    photo_ids: list[str] = []
    for i in range(4):
        lot = Lot(
            id=f"lot-{i}",
            account_id=account.id,
            user_id=user.id,
            sale_id=sale.id,
            sku=f"00{i + 1}",
            auction_title=f"Item {i + 1}",
        )
        img_path = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (80, 60), color=(i * 40, 80, 120)).save(img_path, format="JPEG")
        photo = Photo(
            id=f"photo-{i}",
            account_id=account.id,
            user_id=user.id,
            lot_id=lot.id,
            original_name=img_path.name,
            storage_path=str(img_path),
            photo_role="hero",
            sort_order=0,
        )
        db.add_all([lot, photo])
        photo_ids.append(photo.id)
    db.commit()

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
        listed = client.get("/sales/sale-c/cover-photos")
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["suggested_photo_ids"] == photo_ids
        assert len(body["slots"]) == 4
        assert body["slots"][0]["sku"] == "001"

        cands = client.get("/sales/sale-c/cover-candidates")
        assert cands.status_code == 200, cands.text
        assert len(cands.json()["candidates"]) == 4

        saved = client.put("/sales/sale-c/cover-photos", json={"photo_ids": photo_ids})
        assert saved.status_code == 200, saved.text
        assert saved.json()["selected_photo_ids"] == photo_ids

        fake = MagicMock()
        fake.list_sale_images.return_value = []
        fake.upsert_sale_cover_photos.return_value = {"status": "success", "data": [{"id": 55}]}
        with patch("app.main._ctseller_client_for_account", return_value=fake):
            pushed = client.post(
                "/sales/sale-c/cover-photos/push",
                json={"photo_ids": photo_ids, "ctseller_sale_id": 99},
            )
        assert pushed.status_code == 200, pushed.text
        assert pushed.json()["remote_id"] == 55
        assert pushed.json()["updated"] is False
        fake.upsert_sale_cover_photos.assert_called_once()
        kwargs = fake.upsert_sale_cover_photos.call_args.kwargs
        assert kwargs["sale_id"] == 99
        assert len(kwargs["secondary_jpegs"]) == 3
        assert kwargs["existing_id"] is None
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_sale_reserve_price_applies_to_all_lots(tmp_path) -> None:
    from unittest.mock import MagicMock, patch

    from fastapi.testclient import TestClient

    from app.auth import get_current_user
    from app.db import Base, get_db
    from app.main import app
    from app.models import Account, Lot, Sale, User
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'reserve.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = Session()
    account = Account(id="acct-r", name="CT")
    user = User(id="user-r", account_id=account.id, email="r@example.com", password_hash="x", role="owner")
    sale = Sale(id="sale-r", account_id=account.id, user_id=user.id, name="Reserve sale")
    lot_a = Lot(
        id="lot-a",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="001",
        auction_title="A",
        ctseller_item_id=501,
    )
    lot_b = Lot(
        id="lot-b",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="002",
        auction_title="B",
    )
    db.add_all([account, user, sale, lot_a, lot_b])
    db.commit()

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
        fake = MagicMock()
        fake.update_item_reserve.return_value = {"status": "success"}
        with patch("app.main._ctseller_client_for_account", return_value=fake):
            res = client.post(
                "/sales/sale-r/reserve-price",
                json={"reserve_price": 15, "apply_to_lots": True, "push_posted": True},
            )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["reserve_price"] == 15
        assert body["lots_updated"] == 2
        assert body["posted_updated"] == 1
        assert body["posted_skipped"] == 1
        fake.update_item_reserve.assert_called_once()
        assert fake.update_item_reserve.call_args.kwargs["item_id"] == 501
        assert fake.update_item_reserve.call_args.kwargs["reserve_price"] == 15
        inner = Session()
        try:
            saved_sale = inner.query(Sale).filter(Sale.id == "sale-r").one()
            assert saved_sale.ctseller_reserve_price == 15
            assert {row.reserve_price for row in inner.query(Lot).all()} == {15}
        finally:
            inner.close()
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_parse_sale_image_secondary_urls() -> None:
    assert parse_sale_image_secondary_urls(["a", "b"]) == ["a", "b"]
    assert parse_sale_image_secondary_urls('["https://cdn/a.jpg","https://cdn/b.jpg"]') == [
        "https://cdn/a.jpg",
        "https://cdn/b.jpg",
    ]
    assert parse_sale_image_secondary_urls("['https://cdn/a.jpg', 'https://cdn/b.jpg']") == [
        "https://cdn/a.jpg",
        "https://cdn/b.jpg",
    ]
    profile = pick_profile_sale_image(
        [{"name": "banner", "id": 1}, {"name": "profile", "id": 9, "url": "https://cdn/p.jpg"}]
    )
    assert profile is not None
    assert profile["id"] == 9
