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


def test_upsert_sale_cover_photos_posts_data_urls_and_updates_existing() -> None:
    from app.services.ctseller import sale_cover_data_url

    client = CTSellerClient(access_token="tok")
    posted: list[tuple[str, dict]] = []

    def fake_post(path: str, payload: dict) -> dict:
        posted.append((path, payload))
        return {"status": "success", "data": [{"id": 77}]}

    putted: list[tuple[str, dict]] = []

    def fake_put(path: str, payload: dict) -> dict:
        putted.append((path, payload))
        return {"status": "success", "data": [{"id": 11}]}

    tiny = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00" + bytes([8] * 64) + b"\xff\xd9"
    )
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
    assert row["image"].startswith("data:image/jpeg;base64,")
    assert len(row["itemimageurl"]) == 3
    assert all(u.startswith("data:image/jpeg;base64,") for u in row["itemimageurl"])
    assert putted[0][0] == "/sale/image/update"
    assert putted[0][1]["data"][0]["id"] == 11
    encoded = sale_cover_data_url(tiny)
    assert encoded.startswith("data:image/jpeg;base64,")


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
