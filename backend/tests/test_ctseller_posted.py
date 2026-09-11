from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.ctseller import CTSellerError
from app.services.ctseller_posted import (
    parse_item_sale_id,
    posted_auction_counts,
    refresh_lot_ctseller_sale_ids,
)


def test_parse_item_sale_id() -> None:
    assert parse_item_sale_id({"saleid": 47921}) == 47921
    assert parse_item_sale_id({"saleId": "88"}) == 88
    assert parse_item_sale_id({"saleid": 0}) is None
    assert parse_item_sale_id({}) is None


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


def test_refresh_lot_ctseller_sale_ids_fills_missing() -> None:
    lot = SimpleNamespace(ctseller_item_id=55, ctseller_sale_id=None)
    client = MagicMock()
    client.item_detail.return_value = {"id": 55, "saleid": 47921}
    db = MagicMock()
    looked, failed = refresh_lot_ctseller_sale_ids(db, [lot], client)  # type: ignore[arg-type]
    assert looked == 1
    assert failed == 0
    assert lot.ctseller_sale_id == 47921
    db.commit.assert_called_once()


def test_refresh_lot_ctseller_sale_ids_skips_known_and_errors() -> None:
    known = SimpleNamespace(ctseller_item_id=1, ctseller_sale_id=100)
    missing = SimpleNamespace(ctseller_item_id=2, ctseller_sale_id=None)
    client = MagicMock()
    client.item_detail.side_effect = CTSellerError("gone", status_code=404)
    db = MagicMock()
    looked, failed = refresh_lot_ctseller_sale_ids(db, [known, missing], client)  # type: ignore[arg-type]
    assert looked == 0
    assert failed == 1
    client.item_detail.assert_called_once_with(2)
    db.commit.assert_not_called()
