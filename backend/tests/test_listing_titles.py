"""Category-and-style-first auction titles, with searched brands left in front."""

from __future__ import annotations

from app.core.gemini import SYSTEM_INSTRUCTION, _user_prompt
from app.core.lotting import LotOutput, _ensure_branded_auction_title, scrub_padded_auction_title
from app.services.listing_titles import (
    auction_title_order_clause,
    brand_leads_search,
    place_brand_for_search,
)
from pipeline.pipeline import auction_title_order_clause as pipeline_clause
from pipeline.schemas import FullyAuditedManifest

LAMP_BRAND_FIRST = "Gatherings Inc. Primitive Wrought Iron Style Double-Arm Table Lamp"
LAMP_CATEGORY_FIRST = "Primitive Wrought Iron Style Double-Arm Table Lamp, Gatherings Inc."


def test_house_brand_moves_behind_the_category() -> None:
    assert place_brand_for_search(LAMP_BRAND_FIRST, "Gatherings Inc.") == LAMP_CATEGORY_FIRST
    assert (
        place_brand_for_search(
            "Primitive Wrought Iron Double-Arm Table Lamp by Gatherings Inc.",
            "Gatherings Inc.",
        )
        == "Primitive Wrought Iron Double-Arm Table Lamp by Gatherings Inc."
    )
    assert brand_leads_search("Gatherings Inc.") is False


def test_searched_brand_stays_or_moves_to_the_front() -> None:
    assert (
        place_brand_for_search("Louis Vuitton Monogram Shoulder Bag", "Louis Vuitton")
        == "Louis Vuitton Monogram Shoulder Bag"
    )
    assert (
        place_brand_for_search("Monogram Shoulder Bag, Louis Vuitton", "Louis Vuitton")
        == "Louis Vuitton Monogram Shoulder Bag"
    )
    assert (
        place_brand_for_search("Monogram Shoulder Bag by Louis Vuitton", "Louis Vuitton")
        == "Louis Vuitton Monogram Shoulder Bag"
    )
    assert brand_leads_search("St. John's Bay") is True
    assert brand_leads_search("Pyrex") is True
    assert brand_leads_search("Pedersoli") is True


def test_firearm_maker_stays_first() -> None:
    title = "Acme Arms Brown Bess Flintlock Musket"
    assert place_brand_for_search(title, "Acme Arms") == title


def test_finalize_rewrites_a_leading_house_brand() -> None:
    lot = LotOutput.from_model(
        {
            "item_name": LAMP_BRAND_FIRST,
            "brand": "Gatherings Inc.",
            "labels_and_tags": ["GATHERINGS INC."],
            "specification_lines": ["Brand: Gatherings Inc.", "Material: Wrought iron"],
        }
    )
    assert lot.auction_title == LAMP_CATEGORY_FIRST
    assert lot.brands == ["Gatherings Inc."]


def test_searched_apparel_brand_still_leads_a_rebuilt_title() -> None:
    out = _ensure_branded_auction_title(
        "Woven Utility Jacket Detail with Zippered Pocket",
        ["St. John's Bay"],
        [
            "Brand: St. John's Bay",
            "Material: Cotton Blend",
            "Style: Utility / Outerwear",
            "Era: Contemporary",
        ],
    )
    assert out.startswith("St. John's Bay")
    assert "Jacket" in out
    assert "Detail with" not in out


def test_house_brand_rebuild_keeps_the_object_first() -> None:
    out = _ensure_branded_auction_title(
        "Woven lamp detail with a zippered cord",
        ["Gatherings Inc."],
        ["Material: Wrought iron", "Style: Table lamp"],
    )
    assert out.lower().startswith("wrought iron")
    assert out.endswith("Gatherings Inc.")


def test_multi_brand_title_is_left_in_place() -> None:
    title = "Tribal Jacket, Alfred Dunner Cardigan, and White Mountain Wedge Clogs"
    out = _ensure_branded_auction_title(
        title,
        ["Tribal", "Alfred Dunner", "White Mountain"],
        ["Brand: Tribal"],
    )
    assert out == title


def test_including_a_house_brand_stays_at_the_end() -> None:
    assert (
        scrub_padded_auction_title("Wrought Iron Table Lamp, Including Gatherings Inc.")
        == "Wrought Iron Table Lamp, Gatherings Inc"
    )


def test_title_prompts_teach_category_first() -> None:
    clause = auction_title_order_clause()
    assert clause == pipeline_clause()
    assert "Primitive Wrought Iron Double-Arm Table Lamp, Gatherings Inc." in clause
    assert "Louis Vuitton Monogram Shoulder Bag" in clause
    assert clause in SYSTEM_INSTRUCTION
    user = _user_prompt(manifest_tag="")
    assert "Category and style before the object" in user
    assert "brand first when a label is legible" not in user
    assert "brand first when a label is legible" not in SYSTEM_INSTRUCTION
    hint = FullyAuditedManifest.model_fields["final_auction_title"].description or ""
    assert "Category and style before the object" in hint
    assert "lead with confirmed brand" not in hint.lower()
