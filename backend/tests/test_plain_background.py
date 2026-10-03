"""Janet JL furniture plain-background gate, composite, and restore."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.services.plain_background import (
    composite_cutout,
    fit_cover,
    image_bytes_from_response,
    image_is_blank,
    jpeg_bytes_rgb,
    lot_is_janet_furniture,
    nearest_aspect,
    plain_bg_sidecar_path,
    render_plain_backgrounds,
    sku_prefix,
)


class _Inline:
    def __init__(self, data: bytes) -> None:
        self.data = data


class _Part:
    def __init__(self, data: bytes | None) -> None:
        self.inline_data = _Inline(data) if data is not None else None


class _Content:
    def __init__(self, parts: list[_Part]) -> None:
        self.parts = parts


class _Cand:
    def __init__(self, parts: list[_Part]) -> None:
        self.content = _Content(parts)


class _Response:
    def __init__(self, parts: list[_Part]) -> None:
        self.candidates = [_Cand(parts)]


def test_image_response_and_fit() -> None:
    assert nearest_aspect(400, 300) == "4:3"
    assert nearest_aspect(300, 400) == "3:4"
    blank = Image.new("RGB", (20, 20), (243, 240, 234))
    assert image_is_blank(blank)
    piece = Image.new("RGB", (40, 30), (243, 240, 234))
    for x in range(8, 32):
        for y in range(6, 24):
            piece.putpixel((x, y), (90, 40, 20))
    assert not image_is_blank(piece)
    fitted = fit_cover(piece, (20, 10))
    assert fitted.size == (20, 10)
    raw = jpeg_bytes_rgb(piece)
    assert image_bytes_from_response(_Response([_Part(raw)])) == raw
    assert image_bytes_from_response(_Response([_Part(None)])) is None


def test_render_uses_the_ai_edit(monkeypatch) -> None:
    def fake_edit(jpeg: bytes, color: tuple[int, int, int]) -> bytes:
        assert color == (255, 255, 255)
        with Image.open(BytesIO(jpeg)) as im:
            assert im.size[0] > 0
        return jpeg_bytes_rgb(Image.new("RGB", (12, 8), color))

    monkeypatch.setattr("app.services.plain_background.edit_plain_background", fake_edit)
    src = jpeg_bytes_rgb(Image.new("RGB", (30, 20), (20, 80, 140)))
    ok, errors = render_plain_backgrounds({"p1": src}, "white")
    assert errors == {}
    assert "p1" in ok
    with Image.open(BytesIO(ok["p1"])) as im:
        pixel = im.getpixel((1, 1))
        assert pixel[0] > 240 and pixel[2] > 240


def test_render_reports_a_failed_edit(monkeypatch) -> None:
    def fake_edit(jpeg: bytes, color: tuple[int, int, int]) -> bytes:
        del jpeg, color
        raise RuntimeError("AI did not return a photo.")

    monkeypatch.setattr("app.services.plain_background.edit_plain_background", fake_edit)
    src = jpeg_bytes_rgb(Image.new("RGB", (30, 20), (20, 80, 140)))
    ok, errors = render_plain_backgrounds({"p1": src}, "studio")
    assert ok == {}
    assert "photo" in errors["p1"]


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
    cut = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(15, 85):
        for y in range(15, 85):
            cut.putpixel((x, y), (180, 30, 30, 255))
    out = composite_cutout(cut, (243, 240, 234))
    corner = out.getpixel((1, 1))
    assert corner[0] > 220 and corner[1] > 210
    center = out.getpixel((50, 50))
    assert center[0] > 140 and center[1] < 90


def test_composite_rejects_a_mask_that_erases_the_piece() -> None:
    # A leftover chunk, like a leg or a corner, is enough to pass the old
    # "found something" check and still not enough to keep.
    scrap = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
    for x in range(8, 40):
        for y in range(8, 48):
            scrap.putpixel((x, y), (90, 50, 30, 255))
    try:
        composite_cutout(scrap, (243, 240, 234))
    except RuntimeError as exc:
        assert "too much" in str(exc)
    else:
        raise AssertionError("expected a scrap mask to fail")

    hollow = Image.new("RGBA", (80, 80), (90, 50, 30, 255))
    for x in range(12, 68):
        for y in range(12, 68):
            hollow.putpixel((x, y), (0, 0, 0, 0))
    try:
        composite_cutout(hollow, (243, 240, 234))
    except RuntimeError as exc:
        assert "too much" in str(exc)
    else:
        raise AssertionError("expected a hollow mask to fail")


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
