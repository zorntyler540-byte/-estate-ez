"""CT Seller condition tier helpers."""

from __future__ import annotations

from app.services.ctseller_conditions import (
    CT_SELLER_CONDITION_DEFAULT,
    canonicalize_ct_condition,
    ct_condition_rows,
)


def test_canonicalize_empty_defaults_to_good() -> None:
    assert canonicalize_ct_condition("") == CT_SELLER_CONDITION_DEFAULT
    assert canonicalize_ct_condition(None) == "GOOD"


def test_canonicalize_exact_and_aliases() -> None:
    assert canonicalize_ct_condition("VERY GOOD") == "VERY GOOD"
    assert canonicalize_ct_condition("like new") == "LIKE NEW"
    assert canonicalize_ct_condition("Good") == "GOOD"
    assert canonicalize_ct_condition("fair — heavy wear") == "FAIR"


def test_ct_condition_rows_include_five_tiers() -> None:
    rows = ct_condition_rows()
    names = [r["condition"] for r in rows]
    assert names == ["NEW", "LIKE NEW", "VERY GOOD", "GOOD", "FAIR"]
    assert all(r.get("description") for r in rows)
