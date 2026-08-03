"""Unit tests for CT Seller helpers (no live network)."""

from __future__ import annotations

from app.services.ctseller import build_item_payload, seal_secret, unseal_secret


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
    # Leaf "Lamps" is not a /category/list name; fall back to group "Lighting".
    assert item["category"] == "Lighting"
    assert item["startingprice"] == 5.0
    assert item["itemstatus"] == "Pending"
    assert item["itemreceiptmethod"]["pickup"] is True


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
