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
