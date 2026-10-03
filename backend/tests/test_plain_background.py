"""Janet JL furniture plain-background gate, composite, and restore."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.services.plain_background import (
    composite_cutout,
    jpeg_bytes_rgb,
    lot_is_janet_furniture,
    plain_bg_sidecar_path,
    sku_prefix,
)


def test_sku_prefix_and_janet_furniture_gate() -> None:
    assert sku_prefix("JL91500") == "JL"
    assert sku_prefix("jl101add") == "JL"
    assert sku_prefix("MS530") == "MS"
    assert lot_is_janet_furniture("JL12", "Furniture", "Lot")
    assert lot_is_janet_furniture("JL12", "Bedroom Furniture", "")
    assert lot_is_janet_furniture("JL12", "Household Goods Other", "Oak dresser")
    assert not lot_is_janet_furniture("JL12", "Glassware", "Crystal bowl")
    assert not lot_is_janet_furniture("MS12", "Furniture", "Oak dresser")


def test_composite_paints_color_around_the_piece() -> None:
    cut = Image.new("RGBA", (24, 24), (0, 0, 0, 0))
    for x in range(8, 16):
        for y in range(8, 16):
            cut.putpixel((x, y), (180, 30, 30, 255))
    out = composite_cutout(cut, (243, 240, 234))
    corner = out.getpixel((1, 1))
    assert corner[0] > 220 and corner[1] > 210
    center = out.getpixel((12, 12))
    assert center[0] > 140 and center[1] < 90


def test_composite_rejects_a_mask_that_kept_the_whole_frame() -> None:
    solid = Image.new("RGBA", (12, 12), (20, 20, 20, 255))
    try:
        composite_cutout(solid, (255, 255, 255))
    except RuntimeError as exc:
        assert "separate" in str(exc)
    else:
        raise AssertionError("expected a full-frame mask to fail")


def test_plain_background_endpoint_is_jl_furniture_only(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.auth import get_current_user
    from app.catalog_storage import persist_catalog_photo, resolve_catalog_storage_path
    from app.db import Base, get_db
    from app.main import app
    from app.models import Account, Lot, Photo, Sale, User
    from app.services import plain_background as plain_bg

    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)

    def fake_render(sources: dict[str, bytes], color_name: str):
        color = plain_bg.PLAIN_BG_COLORS[color_name]
        ok = {pid: jpeg_bytes_rgb(Image.new("RGB", (30, 16), color)) for pid in sources}
        return ok, {}

    monkeypatch.setattr(plain_bg, "render_plain_backgrounds", fake_render)

    engine = create_engine(f"sqlite:///{tmp_path / 'plain.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = Session()
    account = Account(id="acct-bg", name="CT")
    user = User(
        id="user-bg",
        account_id=account.id,
        email="bg@example.com",
        password_hash="x",
        role="owner",
    )
    sale = Sale(id="sale-bg", account_id=account.id, user_id=user.id, name="Janet")
    lot = Lot(
        id="lot-bg",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="JL220",
        auction_title="Walnut dresser",
        ct_category="Furniture",
        photo_count=2,
    )
    other = Lot(
        id="lot-ms",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="MS10",
        auction_title="Walnut dresser",
        ct_category="Furniture",
        photo_count=1,
    )
    src_a = tmp_path / "a.jpg"
    src_b = tmp_path / "b.jpg"
    src_c = tmp_path / "c.jpg"
    src_d = tmp_path / "d.jpg"
    src_e = tmp_path / "e.jpg"
    Image.new("RGB", (40, 24), (200, 20, 20)).save(src_a, format="JPEG")
    Image.new("RGB", (40, 24), (20, 20, 200)).save(src_b, format="JPEG")
    Image.new("RGB", (40, 24), (20, 180, 20)).save(src_c, format="JPEG")
    Image.new("RGB", (40, 24), (10, 200, 10)).save(src_d, format="JPEG")
    Image.new("RGB", (40, 24), (220, 180, 20)).save(src_e, format="JPEG")
    _, key_a = persist_catalog_photo(account.id, src_a, photo_id="photo-a")
    _, key_b = persist_catalog_photo(account.id, src_b, photo_id="photo-b")
    _, key_c = persist_catalog_photo(account.id, src_c, photo_id="photo-c")
    _, key_d = persist_catalog_photo(account.id, src_d, photo_id="photo-d")
    _, key_e = persist_catalog_photo(account.id, src_e, photo_id="photo-e")
    db.add_all(
        [
            account,
            user,
            sale,
            lot,
            other,
            Photo(
                id="photo-a",
                account_id=account.id,
                user_id=user.id,
                lot_id=lot.id,
                original_name="a.jpg",
                storage_path=key_a,
                sort_order=0,
            ),
            Photo(
                id="photo-b",
                account_id=account.id,
                user_id=user.id,
                lot_id=lot.id,
                original_name="b.jpg",
                storage_path=key_b,
                sort_order=1,
                photo_role="tape",
            ),
            Photo(
                id="photo-d",
                account_id=account.id,
                user_id=user.id,
                lot_id=lot.id,
                original_name="d.jpg",
                storage_path=key_d,
                sort_order=2,
            ),
            Photo(
                id="photo-e",
                account_id=account.id,
                user_id=user.id,
                lot_id=lot.id,
                original_name="e.jpg",
                storage_path=key_e,
                sort_order=3,
            ),
            Photo(
                id="photo-c",
                account_id=account.id,
                user_id=user.id,
                lot_id=other.id,
                original_name="c.jpg",
                storage_path=key_c,
                sort_order=0,
            ),
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
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    try:
        denied = client.post("/lots/lot-ms/photos/plain-background", json={"color": "studio"})
        assert denied.status_code == 403, denied.text

        tape = client.post(
            "/lots/lot-bg/photos/plain-background",
            json={"color": "studio", "photo_ids": ["photo-b"]},
        )
        assert tape.status_code == 400, tape.text

        over_cap = client.post(
            "/lots/lot-bg/photos/plain-background",
            json={"color": "studio", "photo_ids": [f"missing-{i}" for i in range(8)]},
        )
        assert over_cap.status_code == 400, over_cap.text
        assert "not on this lot" in over_cap.text

        applied = client.post("/lots/lot-bg/photos/plain-background", json={"color": "gray"})
        assert applied.status_code == 200, applied.text
        body = applied.json()
        assert body["updated"] == 1
        assert body["failed"] == 0
        assert body["photos"][0]["plain_background"] is True
        with Image.open(resolve_catalog_storage_path(key_a)) as im:
            pixel = im.getpixel((2, 2))
            assert pixel[0] > 200 and abs(pixel[0] - pixel[1]) < 20
        with Image.open(resolve_catalog_storage_path(key_b)) as im:
            assert im.getpixel((2, 2))[2] > 150
        with Image.open(resolve_catalog_storage_path(key_d)) as im:
            assert im.getpixel((2, 2))[1] > 150
        side = plain_bg_sidecar_path(key_a)
        assert side is not None and side.is_file()
        with Image.open(side) as im:
            assert im.getpixel((2, 2))[0] > 150

        again = client.post("/lots/lot-bg/photos/plain-background", json={"color": "white"})
        assert again.status_code == 200, again.text
        with Image.open(side) as im:
            assert im.getpixel((2, 2))[0] > 150

        restored = client.post(
            "/lots/lot-bg/photos/plain-background/restore",
            json={"photo_ids": ["photo-a"]},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["updated"] == 1
        assert restored.json()["photos"][0]["plain_background"] is False
        with Image.open(resolve_catalog_storage_path(key_a)) as im:
            pixel = im.getpixel((2, 2))
            assert pixel[0] > 150 and pixel[1] < 80

        selected = client.post(
            "/lots/lot-bg/photos/plain-background",
            json={
                "color": "gray",
                "photo_ids": ["photo-a", "photo-b", "photo-d", "photo-e"],
            },
        )
        assert selected.status_code == 200, selected.text
        selected_body = selected.json()
        assert selected_body["updated"] == 3
        assert selected_body["failed"] == 0
        assert any("tape" in note.lower() for note in selected_body["notes"])
        for key in (key_a, key_d, key_e):
            with Image.open(resolve_catalog_storage_path(key)) as im:
                pixel = im.getpixel((2, 2))
                assert abs(pixel[0] - pixel[1]) < 20 and pixel[0] > 200
        with Image.open(resolve_catalog_storage_path(key_b)) as im:
            assert im.getpixel((2, 2))[2] > 150
    finally:
        app.dependency_overrides.clear()
        db.close()
