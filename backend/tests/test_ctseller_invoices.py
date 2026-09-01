"""Unit tests for CT Seller invoice parsing helpers (shipping suite spike)."""

from app.services.ctseller import parse_invoice_list_row, receipt_method_wants_shipping


def test_parse_invoice_list_row() -> None:
    row = parse_invoice_list_row(
        {
            "id": "42",
            "invoicenumber": "INV-100",
            "saletitle": "April Sale",
            "saleid": 9,
            "locationtitle": "Tucson",
            "invoiceamount": "185.50",
            "invoicestatus": "Paid",
            "type": "Purchase",
            "buyerid": 7,
            "createddate": "2026-08-01T12:00:00",
            "view": 1,
        }
    )
    assert row is not None
    assert row.id == 42
    assert row.invoicenumber == "INV-100"
    assert row.saleid == 9
    assert row.invoiceamount == 185.5
    assert row.invoicestatus == "Paid"


def test_parse_invoice_list_row_rejects_missing_id() -> None:
    assert parse_invoice_list_row({"invoicenumber": "x"}) is None


def test_receipt_method_wants_shipping() -> None:
    assert receipt_method_wants_shipping('{"pickup": false, "delivery": false, "shipping": true}') is True
    assert receipt_method_wants_shipping({"pickup": True, "shipping": False}) is False
    assert receipt_method_wants_shipping(None) is None
    assert receipt_method_wants_shipping("") is None
