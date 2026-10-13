"""Unit tests for CT Seller helpers (no live network)."""

from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from app.services.ctseller import (
    CTSellerClient,
    CTSellerError,
    CTSellerTokens,
    access_token_should_refresh,
    build_item_payload,
    jwt_expiry_unix,
    persist_ctseller_tokens,
    seal_secret,
    unseal_secret,
)


def _unsigned_jwt(*, exp: float) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode("ascii").rstrip("=")
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode("ascii")).decode("ascii").rstrip("=")
    )
    return f"{header}.{payload}.sig"


def test_seal_roundtrip() -> None:
    sealed = seal_secret("seller-secret")
    assert sealed
    assert sealed != "seller-secret"
    assert unseal_secret(sealed) == "seller-secret"


def test_build_item_payload_strips_category_group() -> None:
    item = build_item_payload(
        sale_id=42,
        location_id=7,
        sku="034",
        title="Vintage lamp",
        description="Nice lamp.",
        category="Lighting#*#Lamps",
        condition="Good",
        starting_price=5.0,
        item_receipt_method='{"pickup": true, "delivery": false, "shipping": false}',
    )
    assert item["saleid"] == 42
    assert item["locationid"] == 7
    assert item["SKU"] == "034"
    assert item["id"] is None
    assert item["itemtypeid"] == 2
    assert item["area"] == "Living Room"  # Lighting → living-space default
    # Leaf "Lamps" is not a /category/list name; fall back to group "Lighting".
    assert item["category"] == "Lighting"
    assert item["startingprice"] == 5.0
    assert item["itemstatus"] == "Pending"
    assert isinstance(item["itemreceiptmethod"], str)
    assert "pickup" in item["itemreceiptmethod"]


def test_build_item_payload_kitchen_area() -> None:
    item = build_item_payload(
        sale_id=1,
        location_id=2,
        sku="K1",
        title="Mugs",
        description="Set",
        category="Kitchen & Home",
        condition="Good",
        starting_price=1.0,
        item_receipt_method=None,
    )
    assert item["area"] == "Kitchen & Dining"


def test_build_item_payload_canonicalizes_luxury_alias() -> None:
    item = build_item_payload(
        sale_id=1,
        location_id=2,
        sku="A1",
        title="Bag",
        description="Designer bag",
        category="Luxury & Designer Items",
        condition="Good",
        starting_price=5.0,
        item_receipt_method=None,
    )
    assert item["category"] == "Luxury and Designer Items"


def test_build_item_payload_copies_sale_tax_and_premium() -> None:
    """SPA Settings tab copies sale tax / buyerspremium onto new items."""
    item = build_item_payload(
        sale_id=1,
        location_id=2,
        sku="T1",
        title="Test",
        description="d",
        category="Household Goods Other",
        condition="Good",
        starting_price=1.0,
        item_receipt_method={"pickup": True, "delivery": False, "shipping": True},
        tax=8.875,
        buyers_premium=15,
        material="Plastic",
    )
    assert item["tax"] == "8.875"
    assert item["buyerspremium"] == "15.000"
    assert item["istaxable"] is True
    assert item["isshippable"] is True
    assert item["material"] == "Plastic"
    assert item["width"] == ""
    assert item["fragilityscore"] is None


def test_build_item_payload_includes_reserve_price() -> None:
    item = build_item_payload(
        sale_id=1,
        location_id=2,
        sku="R1",
        title="Lamp",
        description="d",
        category="Household Goods Other",
        condition="Good",
        starting_price=1.0,
        item_receipt_method=None,
        reserve_price=25,
    )
    assert item["startingprice"] == 1.0
    assert item["reserveprice"] == 25.0


def test_build_item_payload_blank_reserve_when_missing() -> None:
    item = build_item_payload(
        sale_id=1,
        location_id=2,
        sku="R2",
        title="Lamp",
        description="d",
        category="Household Goods Other",
        condition="Good",
        starting_price=1.0,
        item_receipt_method=None,
    )
    assert item["reserveprice"] == ""


def test_format_and_resolve_reserve_price() -> None:
    from app.services.ctseller import format_ctseller_reserve_price, resolve_lot_reserve_price

    assert format_ctseller_reserve_price(None) == ""
    assert format_ctseller_reserve_price(0) == ""
    assert format_ctseller_reserve_price(-1) == ""
    assert format_ctseller_reserve_price(5) == 5.0
    lot = SimpleNamespace(reserve_price=None)
    sale = SimpleNamespace(ctseller_reserve_price=10)
    assert resolve_lot_reserve_price(lot, sale) == 10.0
    lot.reserve_price = 3
    assert resolve_lot_reserve_price(lot, sale) == 3.0


def test_update_item_reserve_puts_merged_detail() -> None:
    client = CTSellerClient(access_token="tok")

    def fake_detail(item_id: int) -> dict:
        assert item_id == 88
        return {"id": 88, "SKU": "001", "title": "Lamp", "reserveprice": ""}

    putted: list[tuple[str, dict]] = []

    def fake_put(path: str, payload: dict) -> dict:
        putted.append((path, payload))
        return {"status": "success"}

    with patch.object(client, "item_detail", fake_detail), patch.object(client, "_put_raw", fake_put):
        client.update_item_reserve(item_id=88, reserve_price=12)
    assert putted[0][0] == "/item/update"
    row = putted[0][1]["data"][0]
    assert row["id"] == 88
    assert row["SKU"] == "001"
    assert row["reserveprice"] == 12.0


def test_build_item_payload_blank_tax_when_sale_has_none() -> None:
    item = build_item_payload(
        sale_id=1,
        location_id=2,
        sku="T2",
        title="Test",
        description="d",
        category="Household Goods Other",
        condition="Good",
        starting_price=1.0,
        item_receipt_method=None,
    )
    assert item["tax"] == ""
    assert item["buyerspremium"] == ""
    assert item["istaxable"] is False
    assert item["tag"] == ""


def test_build_item_payload_joins_search_tags() -> None:
    item = build_item_payload(
        sale_id=1,
        location_id=2,
        sku="T3",
        title="Herman Miller desk",
        description="d",
        category="Desks",
        condition="Good",
        starting_price=1.0,
        item_receipt_method=None,
        tag=["Herman Miller", "MCM", "Rolex"],
    )
    assert "Herman Miller" in item["tag"]
    assert "MCM" in item["tag"]
    assert "Rolex" not in item["tag"]


def test_build_item_payload_keeps_evidenced_luxury_tag() -> None:
    item = build_item_payload(
        sale_id=1,
        location_id=2,
        sku="W1",
        title="Rolex Oyster Datejust",
        description="Steel Rolex watch",
        category="Jewelry",
        condition="Good",
        starting_price=1.0,
        item_receipt_method=None,
        tag=["Rolex", "Oyster"],
    )
    assert "Rolex" in item["tag"]


def test_jwt_expiry_and_refresh_decision() -> None:
    now = 1_700_000_000.0
    fresh = _unsigned_jwt(exp=now + 3600)
    soon = _unsigned_jwt(exp=now + 60)
    dead = _unsigned_jwt(exp=now - 10)
    assert jwt_expiry_unix(fresh) == now + 3600
    assert access_token_should_refresh(None) is True
    assert access_token_should_refresh("", now=now) is True
    assert access_token_should_refresh(fresh, now=now) is False
    assert access_token_should_refresh(soon, now=now, skew_seconds=180) is True
    assert access_token_should_refresh(dead, now=now) is True
    assert access_token_should_refresh("not-a-jwt", now=now) is False


def test_persist_tokens_keeps_old_refresh_when_blank() -> None:
    account = SimpleNamespace(
        ctseller_access_token_enc="old-a",
        ctseller_refresh_token_enc="old-r",
    )
    persist_ctseller_tokens(account, CTSellerTokens(access_token="new-access", refresh_token=""))
    assert unseal_secret(account.ctseller_access_token_enc) == "new-access"
    assert account.ctseller_refresh_token_enc == "old-r"
    persist_ctseller_tokens(account, CTSellerTokens(access_token="newer", refresh_token="new-refresh"))
    assert unseal_secret(account.ctseller_refresh_token_enc) == "new-refresh"


def test_refresh_tokens_reads_rotated_refresh() -> None:
    client = CTSellerClient()

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"newToken": "access-2", "refreshToken": "refresh-2"}

    with patch.object(client.session, "get", return_value=_Resp()):
        tokens = client.refresh_tokens("refresh-1")
    assert tokens.access_token == "access-2"
    assert tokens.refresh_token == "refresh-2"
    assert client.access_token == "access-2"


def test_client_uses_refresh_when_jwt_expired(tmp_path) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.main import _ctseller_client_for_account
    from app.models import Account

    engine = create_engine(f"sqlite:///{tmp_path / 'ctseller-session.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    account = Account(
        id="acct-ct",
        name="CT",
        ctseller_username="seller@example.com",
        ctseller_password_enc=seal_secret("pw"),
        ctseller_access_token_enc=seal_secret(_unsigned_jwt(exp=time.time() - 30)),
        ctseller_refresh_token_enc=seal_secret("refresh-old"),
    )
    db.add(account)
    db.commit()

    def fake_refresh_tokens(self, refresh_token: str) -> CTSellerTokens:
        assert refresh_token == "refresh-old"
        self.set_token("access-fresh")
        return CTSellerTokens(access_token="access-fresh", refresh_token="refresh-new")

    with (
        patch.object(CTSellerClient, "refresh_tokens", fake_refresh_tokens),
        patch.object(CTSellerClient, "login", side_effect=AssertionError("should not login")),
        patch.object(CTSellerClient, "list_open_sales", side_effect=AssertionError("should not probe")),
    ):
        client = _ctseller_client_for_account(account, db, probe=False)
    assert client.access_token == "access-fresh"
    db.refresh(account)
    assert unseal_secret(account.ctseller_access_token_enc) == "access-fresh"
    assert unseal_secret(account.ctseller_refresh_token_enc) == "refresh-new"


def _cover_jpeg() -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (80, 60), color=(12, 80, 140)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _assert_raw_jpeg_b64(value: str) -> None:
    import base64

    assert isinstance(value, str)
    assert not value.startswith("data:")
    raw = base64.b64decode(value, validate=True)
    assert raw[:2] == b"\xff\xd8"


def test_upsert_sale_cover_photos_posts_raw_base64_and_updates_existing() -> None:
    from app.services.ctseller import sale_cover_base64

    client = CTSellerClient(access_token="tok")
    posted: list[tuple[str, dict]] = []

    def fake_post(path: str, payload: dict) -> dict:
        posted.append((path, payload))
        return {"status": "success", "data": [{"id": 77}]}

    putted: list[tuple[str, dict]] = []

    def fake_put(path: str, payload: dict) -> dict:
        putted.append((path, payload))
        return {"status": "success", "data": [{"id": 11}]}

    tiny = _cover_jpeg()
    with patch.object(client, "_post_raw", fake_post), patch.object(client, "_put_raw", fake_put):
        client.upsert_sale_cover_photos(
            sale_id=321,
            primary_jpeg=tiny,
            secondary_jpegs=[tiny, tiny, tiny],
        )
        client.upsert_sale_cover_photos(
            sale_id=321,
            primary_jpeg=tiny,
            secondary_jpegs=[tiny, tiny, tiny],
            existing_id=11,
        )
    assert posted[0][0] == "/sale/image/add"
    row = posted[0][1]["data"][0]
    assert row["saleid"] == 321
    assert row["name"] == "profile"
    _assert_raw_jpeg_b64(row["image"])
    assert len(row["itemimageurl"]) == 3
    for url in row["itemimageurl"]:
        _assert_raw_jpeg_b64(url)
    assert putted[0][0] == "/sale/image/update"
    assert putted[0][1]["data"][0]["id"] == 11
    encoded = sale_cover_base64(tiny)
    _assert_raw_jpeg_b64(encoded)


def test_upsert_sale_cover_photos_retries_seller_server_problem() -> None:
    from app.services.ctseller import CTSellerError

    client = CTSellerClient(access_token="tok")
    calls = {"n": 0}

    def flaky_post(path: str, payload: dict) -> dict:
        calls["n"] += 1
        if path == "/sale/image/list":
            return {"data": []}
        if calls["n"] == 1:
            raise CTSellerError("Uh oh, looks like we encountered a server problem")
        return {"status": "success", "data": [{"id": 88}]}

    tiny = _cover_jpeg()
    with patch.object(client, "_post_raw", flaky_post), patch("app.services.ctseller.time.sleep", lambda *_a, **_k: None):
        result = client.upsert_sale_cover_photos(
            sale_id=321,
            primary_jpeg=tiny,
            secondary_jpegs=[tiny, tiny, tiny],
        )
    assert calls["n"] >= 2
    assert result["data"][0]["id"] == 88


def test_upsert_sale_cover_photos_refreshes_stale_profile_id() -> None:
    from app.services.ctseller import CTSellerError

    client = CTSellerClient(access_token="tok")
    puts: list[int | None] = []

    def fake_put(path: str, payload: dict) -> dict:
        row = payload["data"][0]
        puts.append(row.get("id"))
        if row.get("id") == 11:
            raise CTSellerError("Uh oh, looks like we encountered a server problem")
        return {"status": "success", "data": [{"id": 22}]}

    def fake_post(path: str, payload: dict) -> dict:
        assert path == "/sale/image/list"
        return {"data": [{"id": 22, "name": "profile"}]}

    tiny = _cover_jpeg()
    with (
        patch.object(client, "_put_raw", fake_put),
        patch.object(client, "_post_raw", fake_post),
        patch("app.services.ctseller.time.sleep", lambda *_a, **_k: None),
    ):
        result = client.upsert_sale_cover_photos(
            sale_id=321,
            primary_jpeg=tiny,
            secondary_jpegs=[tiny, tiny, tiny],
            existing_id=11,
        )
    assert puts == [11, 22]
    assert result["data"][0]["id"] == 22


def test_sale_cover_base64_rejects_garbage() -> None:
    from app.services.ctseller import CTSellerError, sale_cover_base64

    try:
        sale_cover_base64(b"not-an-image")
        raise AssertionError("expected CTSellerError")
    except CTSellerError as exc:
        assert "could not be read" in str(exc).lower()


def test_encode_sale_cover_jpeg_center_crops_to_square() -> None:
    from io import BytesIO

    from PIL import Image

    from app.services.ctseller import encode_sale_cover_jpeg

    buf = BytesIO()
    Image.new("RGB", (800, 400), color=(10, 20, 30)).save(buf, format="JPEG", quality=85)
    jpeg = encode_sale_cover_jpeg(buf.getvalue(), max_edge=400)
    out = Image.open(BytesIO(jpeg))
    assert out.size == (400, 400)


def test_list_sale_images_reads_data_list() -> None:
    client = CTSellerClient(access_token="tok")

    def fake_post(path: str, payload: dict) -> dict:
        assert path == "/sale/image/list"
        assert payload["filter"][0]["field"] == "saleid"
        assert payload["filter"][0]["value"] == 44
        return {"data": [{"id": 1, "name": "profile"}]}

    with patch.object(client, "_post_raw", fake_post):
        rows = client.list_sale_images(sale_id=44)
    assert rows[0]["id"] == 1


def test_client_falls_back_to_password_when_refresh_fails(tmp_path) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.main import _ctseller_client_for_account
    from app.models import Account

    engine = create_engine(f"sqlite:///{tmp_path / 'ctseller-login.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    account = Account(
        id="acct-ct2",
        name="CT",
        ctseller_username="seller@example.com",
        ctseller_password_enc=seal_secret("pw"),
        ctseller_access_token_enc=seal_secret(_unsigned_jwt(exp=time.time() - 30)),
        ctseller_refresh_token_enc=seal_secret("refresh-dead"),
    )
    db.add(account)
    db.commit()

    def fake_refresh_tokens(self, refresh_token: str) -> CTSellerTokens:
        raise CTSellerError("expired refresh", status_code=401)

    def fake_login(self, username: str, password: str, *, keep_logged_in: bool = True) -> CTSellerTokens:
        assert username == "seller@example.com"
        assert password == "pw"
        assert keep_logged_in is True
        self.set_token("access-login")
        return CTSellerTokens(access_token="access-login", refresh_token="refresh-login")

    with (
        patch.object(CTSellerClient, "refresh_tokens", fake_refresh_tokens),
        patch.object(CTSellerClient, "login", fake_login),
    ):
        client = _ctseller_client_for_account(account, db, probe=False)
    assert client.access_token == "access-login"
    db.refresh(account)
    assert unseal_secret(account.ctseller_access_token_enc) == "access-login"


def test_jpeg_bytes_for_ctseller_post_caches_small_file(tmp_path) -> None:
    from io import BytesIO

    from PIL import Image

    from app.services.ctseller import IMAGE_MAX_EDGE, jpeg_bytes_for_ctseller_post

    src = tmp_path / "hero.jpg"
    Image.new("RGB", (2400, 1800), (20, 40, 80)).save(src, format="JPEG", quality=90)
    out = jpeg_bytes_for_ctseller_post(src)
    cache = src.with_name(f"hero.ctseller.{IMAGE_MAX_EDGE}.jpg")
    assert cache.is_file()
    with Image.open(BytesIO(out)) as im:
        assert max(im.size) <= IMAGE_MAX_EDGE
    again = jpeg_bytes_for_ctseller_post(src)
    assert again == cache.read_bytes()


def test_image_to_base64_skips_second_decode_for_small_jpeg() -> None:
    from io import BytesIO

    from PIL import Image

    from app.services.ctseller import _image_to_base64

    buf = BytesIO()
    Image.new("RGB", (400, 300), (9, 9, 9)).save(buf, format="JPEG", quality=80)
    raw = buf.getvalue()
    b64 = _image_to_base64(raw)
    import base64

    assert base64.b64decode(b64) == raw


def test_video_locator_matches_seller_btoa() -> None:
    from app.services.ctseller import ctseller_video_locator

    raw = "BIGwalk.mp4$pk-9$1048580$2"
    assert ctseller_video_locator(
        filename="walk.mp4", partkey="pk-9", size=1048580, part_count=2
    ) == base64.b64encode(raw.encode("utf-8")).decode("ascii")


def test_add_item_video_chunks_then_registers_on_the_media_tab() -> None:
    client = CTSellerClient(access_token="tok")
    posted: list[tuple[str, dict]] = []

    def fake_post(path: str, payload: dict) -> dict:
        posted.append((path, payload))
        if path == "/file/upload":
            return {"status": "success", "data": {"partkey": "pk-9"}}
        return {"status": "success"}

    payload = b"a" * (1024 * 1024) + b"tail"
    with patch.object(client, "_post_raw", fake_post):
        client.add_item_video(item_id=9, video_bytes=payload, name="folder/walk.mp4", display_order=4)

    assert [row[0] for row in posted] == [
        "/file/upload",
        "/file/upload/fragment",
        "/file/upload/fragment",
        "/item/image/add",
    ]
    start = posted[0][1]["data"]
    assert start == {"filename": "walk.mp4", "size": len(payload), "partCount": 2}
    assert posted[1][1]["data"]["partid"] == 1
    assert posted[1][1]["data"]["partkey"] == "pk-9"
    assert len(base64.b64decode(posted[1][1]["data"]["content"])) == 1024 * 1024
    assert posted[2][1]["data"]["partid"] == 2
    assert base64.b64decode(posted[2][1]["data"]["content"]) == b"tail"
    media = posted[3][1]["data"]
    assert media["itemid"] == 9
    assert media["name"] == "walk.mp4"
    assert media["type"] == "video"
    assert media["isdisplayimage"] == 0
    assert media["displayorder"] == 4
    assert media["image"] == base64.b64encode(b"BIGwalk.mp4$pk-9$1048580$2").decode("ascii")


def test_add_item_video_rejects_empty_and_oversize_before_upload(monkeypatch) -> None:
    from app.services import ctseller as ctseller_mod

    client = CTSellerClient(access_token="tok")

    def fake_post(path: str, payload: dict) -> dict:
        raise AssertionError(path)

    monkeypatch.setattr(ctseller_mod, "CTSELLER_MAX_VIDEO_BYTES", 2 * 1024 * 1024)
    with patch.object(client, "_post_raw", fake_post):
        try:
            client.add_item_video(item_id=1, video_bytes=b"", name="empty.mp4", display_order=1)
            raise AssertionError("empty")
        except CTSellerError as exc:
            assert "empty" in str(exc)
        try:
            client.add_item_video(
                item_id=1,
                video_bytes=b"x" * (2 * 1024 * 1024 + 1),
                name="big.mp4",
                display_order=1,
            )
            raise AssertionError("oversize")
        except CTSellerError as exc:
            assert "2 MB" in str(exc)
