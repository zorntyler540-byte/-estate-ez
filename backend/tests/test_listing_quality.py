"""Listing quality gate: flag placeholder / garbled titles + descriptions for human review."""

from __future__ import annotations

from app.core.lotting import listing_quality_issue


def test_flags_garbled_tag_ocr_title() -> None:
    # Real-world failure: a handwritten estate tag's field labels got dumped into the title.
    reason = listing_quality_issue(
        "No. Handcrafted STYLE Plates SIZE 3 pcs PRICE No. STYLE SIZE PRICE 84 JAPAN Apparel",
        "A genuine human-written description about the plates.",
    )
    assert reason == "garbled_title"


def test_flags_placeholder_grouping_description() -> None:
    assert listing_quality_issue("Some Real Title", "Estate grouping (2 items)") == "placeholder_description"
    assert listing_quality_issue("Some Real Title", "") == "placeholder_description"


def test_flags_placeholder_titles() -> None:
    assert listing_quality_issue("Estate grouping (2 items)", "x") == "placeholder_title"
    assert listing_quality_issue("Estate item (ITEM_2)", "x") == "placeholder_title"
    assert listing_quality_issue("", "x") == "placeholder_title"


def test_good_listings_are_not_flagged() -> None:
    assert listing_quality_issue("Pyrex Cobalt Blue Glass Mug", "A classic cobalt mug for the kitchen.") is None
    assert listing_quality_issue("Lodge 12-Inch Cast Iron Skillet", "Well-seasoned skillet ready to cook.") is None
    # Legitimate use of one or two label-like words should not trip the garbled detector.
    assert listing_quality_issue("Men's Size Large Nike Style Shirt", "Comfortable large Nike tee.") is None
