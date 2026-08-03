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
    assert item["category"] == "Lamps"
    assert item["startingprice"] == 5.0
    assert item["itemstatus"] == "Pending"
    assert item["itemreceiptmethod"]["pickup"] is True
