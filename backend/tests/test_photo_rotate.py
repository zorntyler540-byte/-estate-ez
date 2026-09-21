from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from app.catalog_storage import persist_catalog_photo, purge_catalog_photo_thumbs, resolve_catalog_storage_path
from app.services.image_preprocess import jpeg_exif_orientation
from app.services.photo_rotate import rotate_image_bytes


def _jpeg(width: int, height: int, color: tuple[int, int, int] = (200, 40, 40), orientation: int | None = None) -> bytes:
    im = Image.new("RGB", (width, height), color)
    buf = BytesIO()
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        im.save(buf, format="JPEG", quality=90, exif=exif)
    else:
        im.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_rotate_90_clockwise_swaps_dimensions() -> None:
    data = _jpeg(40, 20)
    out = rotate_image_bytes(data, 90)
    with Image.open(BytesIO(out)) as im:
        assert im.size == (20, 40)
        assert jpeg_exif_orientation(im) in (0, 1)


def test_rotate_minus_90_swaps_dimensions() -> None:
    data = _jpeg(40, 20)
    out = rotate_image_bytes(data, -90)
    with Image.open(BytesIO(out)) as im:
        assert im.size == (20, 40)


def test_rotate_180_keeps_dimensions() -> None:
    data = _jpeg(40, 20)
    out = rotate_image_bytes(data, 180)
    with Image.open(BytesIO(out)) as im:
        assert im.size == (40, 20)


def test_rotate_bakes_exif_then_turns() -> None:
    """Orientation 6 is already a 90° CW display; after bake, +90 CW is landscape again."""
    data = _jpeg(40, 20, orientation=6)
    out = rotate_image_bytes(data, 90)
    with Image.open(BytesIO(out)) as im:
        assert jpeg_exif_orientation(im) in (0, 1)
        assert im.size == (40, 20)


def test_purge_catalog_photo_thumbs_removes_local_siblings(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)
    src = tmp_path / "shot.jpg"
    src.write_bytes(_jpeg(40, 20))
    photo_id, key = persist_catalog_photo("acct-1", src, photo_id="pid-rot")
    dest = resolve_catalog_storage_path(key)
    thumb = dest.with_name(f"{Path(dest.name).stem}.thumb.220.jpg")
    thumb.write_bytes(b"stale")
    assert thumb.is_file()
    purge_catalog_photo_thumbs(key)
    assert not thumb.is_file()
    assert dest.is_file()
    assert photo_id == "pid-rot"


def test_rotate_lot_photos_endpoint_rotates_all_and_selected(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.auth import get_current_user
    from app.db import Base, get_db
    from app.main import app
    from app.models import Account, Lot, Photo, Sale, User

    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)

    engine = create_engine(f"sqlite:///{tmp_path / 'rotate.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = Session()
    account = Account(id="acct-rot", name="CT")
    user = User(
        id="user-rot",
        account_id=account.id,
        email="rotate@example.com",
        password_hash="x",
        role="owner",
    )
    sale = Sale(id="sale-rot", account_id=account.id, user_id=user.id, name="Yard")
    lot = Lot(
        id="lot-rot",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="T0331RG",
        auction_title="Cups",
        photo_count=2,
    )
    src_a = tmp_path / "a.jpg"
    src_b = tmp_path / "b.jpg"
    Image.new("RGB", (40, 20), (200, 10, 10)).save(src_a, format="JPEG")
    Image.new("RGB", (40, 20), (10, 200, 10)).save(src_b, format="JPEG")
    _, key_a = persist_catalog_photo(account.id, src_a, photo_id="photo-a")
    _, key_b = persist_catalog_photo(account.id, src_b, photo_id="photo-b")
    photo_a = Photo(
        id="photo-a",
        account_id=account.id,
        user_id=user.id,
        lot_id=lot.id,
        original_name="a.jpg",
        storage_path=key_a,
        sort_order=0,
    )
    photo_b = Photo(
        id="photo-b",
        account_id=account.id,
        user_id=user.id,
        lot_id=lot.id,
        original_name="b.jpg",
        storage_path=key_b,
        sort_order=1,
    )
    db.add_all([account, user, sale, lot, photo_a, photo_b])
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
        selected = client.post(
            "/lots/lot-rot/photos/rotate",
            json={"degrees": 90, "photo_ids": ["photo-a"]},
        )
        assert selected.status_code == 200, selected.text
        body = selected.json()
        assert body["rotated"] == 1
        assert body["failed"] == 0
        with Image.open(resolve_catalog_storage_path(key_a)) as im:
            assert im.size == (20, 40)
        with Image.open(resolve_catalog_storage_path(key_b)) as im:
            assert im.size == (40, 20)

        all_photos = client.post("/lots/lot-rot/photos/rotate", json={"degrees": -90})
        assert all_photos.status_code == 200, all_photos.text
        assert all_photos.json()["rotated"] == 2
        with Image.open(resolve_catalog_storage_path(key_a)) as im:
            assert im.size == (40, 20)
        with Image.open(resolve_catalog_storage_path(key_b)) as im:
            assert im.size == (20, 40)

        missing = client.post(
            "/lots/lot-rot/photos/rotate",
            json={"degrees": 90, "photo_ids": ["nope"]},
        )
        assert missing.status_code == 400

        bad = client.post("/lots/lot-rot/photos/rotate", json={"degrees": 45})
        assert bad.status_code == 422
    finally:
        app.dependency_overrides.clear()
        db.close()
