from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.ctseller import CTSellerError
from app.services.ctseller_posted import (
    apply_item_detail_to_lot,
    normalize_ctseller_outcome,
    parse_item_sale_id,
    parse_item_status,
    parse_sold_price,
    posted_auction_counts,
    posted_outcome_counts,
    refresh_lot_ctseller_sale_ids,
)


def test_parse_item_sale_id() -> None:
    assert parse_item_sale_id({"saleid": 47921}) == 47921
    assert parse_item_sale_id({"saleId": "88"}) == 88
    assert parse_item_sale_id({"saleid": 0}) is None
    assert parse_item_sale_id({}) is None


def test_parse_item_status_and_sold_price() -> None:
    assert parse_item_status({"itemstatus": "Sold"}) == "Sold"
    assert parse_sold_price({"itemstatus": "Sold", "soldprice": "18.50"}) == 18.5
    assert parse_sold_price({"itemstatus": "Pending", "currentbid": "12"}) is None
    assert parse_sold_price({"itemstatus": "Unsold", "currentbid": "4"}) is None


def test_normalize_ctseller_outcome() -> None:
    assert normalize_ctseller_outcome("Sold") == "sold"
    assert normalize_ctseller_outcome("sold online") == "sold"
    assert normalize_ctseller_outcome("Unsold") == "unsold"
    assert normalize_ctseller_outcome("Pending") == "live"
    assert normalize_ctseller_outcome("Ready") == "live"
    assert normalize_ctseller_outcome("Pulled") == "pulled"
    assert normalize_ctseller_outcome(None) == "unknown"


def test_posted_auction_counts_groups_mixed_sales() -> None:
    lots = [
        SimpleNamespace(ctseller_item_id=1, ctseller_posted_at=None, ctseller_sale_id=100),
        SimpleNamespace(ctseller_item_id=2, ctseller_posted_at=None, ctseller_sale_id=100),
        SimpleNamespace(ctseller_item_id=3, ctseller_posted_at=None, ctseller_sale_id=200),
        SimpleNamespace(ctseller_item_id=4, ctseller_posted_at=None, ctseller_sale_id=None),
        SimpleNamespace(ctseller_item_id=None, ctseller_posted_at=None, ctseller_sale_id=None),
    ]
    ranked, unknown = posted_auction_counts(lots)
    assert ranked == [(100, 2), (200, 1)]
    assert unknown == 1


def test_posted_outcome_counts() -> None:
    lots = [
        SimpleNamespace(
            ctseller_item_id=1,
            ctseller_posted_at=None,
            ctseller_item_status="Sold",
            ctseller_sold_price=20,
        ),
        SimpleNamespace(
            ctseller_item_id=2,
            ctseller_posted_at=None,
            ctseller_item_status="Unsold",
            ctseller_sold_price=None,
        ),
        SimpleNamespace(
            ctseller_item_id=3,
            ctseller_posted_at=None,
            ctseller_item_status="Pending",
            ctseller_sold_price=None,
        ),
    ]
    assert posted_outcome_counts(lots) == {
        "sold": 1,
        "unsold": 1,
        "live": 1,
        "pulled": 0,
        "unknown": 0,
    }


def test_apply_item_detail_to_lot() -> None:
    lot = SimpleNamespace(ctseller_sale_id=None, ctseller_item_status=None, ctseller_sold_price=None)
    assert apply_item_detail_to_lot(
        lot,  # type: ignore[arg-type]
        {"saleid": 50, "itemstatus": "Sold", "soldprice": 9},
    )
    assert lot.ctseller_sale_id == 50
    assert lot.ctseller_item_status == "Sold"
    assert lot.ctseller_sold_price == 9


def test_refresh_lot_ctseller_sale_ids_fills_missing() -> None:
    lot = SimpleNamespace(
        ctseller_item_id=55,
        ctseller_sale_id=None,
        ctseller_item_status=None,
        ctseller_sold_price=None,
    )
    client = MagicMock()
    client.item_detail.return_value = {"id": 55, "saleid": 47921, "itemstatus": "Pending"}
    db = MagicMock()
    looked, failed = refresh_lot_ctseller_sale_ids(db, [lot], client)  # type: ignore[arg-type]
    assert looked == 1
    assert failed == 0
    assert lot.ctseller_sale_id == 47921
    assert lot.ctseller_item_status == "Pending"
    db.commit.assert_called_once()
    client.list_sale_items.assert_not_called()


def test_refresh_uses_item_list_then_detail_fallback() -> None:
    known = SimpleNamespace(
        ctseller_item_id=1,
        ctseller_sale_id=100,
        ctseller_item_status=None,
        ctseller_sold_price=None,
    )
    missing = SimpleNamespace(
        ctseller_item_id=2,
        ctseller_sale_id=100,
        ctseller_item_status=None,
        ctseller_sold_price=None,
    )
    client = MagicMock()
    client.list_sale_items.return_value = [
        {"id": 1, "saleid": 100, "itemstatus": "Sold", "soldprice": 12}
    ]
    client.item_detail.side_effect = CTSellerError("gone", status_code=404)
    db = MagicMock()
    looked, failed = refresh_lot_ctseller_sale_ids(db, [known, missing], client)  # type: ignore[arg-type]
    assert looked == 1
    assert failed == 1
    assert known.ctseller_item_status == "Sold"
    assert known.ctseller_sold_price == 12
    client.item_detail.assert_called_once_with(2)
    db.commit.assert_called_once()
