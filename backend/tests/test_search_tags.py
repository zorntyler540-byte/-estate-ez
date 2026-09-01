"""CT Bids search-tag sanitizer and listing fallback."""

from __future__ import annotations

from types import SimpleNamespace

from app.core.lotting import LotOutput
from app.main import _search_tags_for_lot_row
from app.services.search_tags import (
    format_ctseller_tag_field,
    merge_search_tags,
    sanitize_search_tags,
    suggest_search_tags,
)
from pipeline.gemini_schema import pydantic_model_to_gemini_schema
from pipeline.schemas import FullyAuditedManifest


def test_sanitize_drops_unaffiliated_luxury_bait() -> None:
    tags = sanitize_search_tags(
        ["Rococo", "Rolex", "Porsche", "brass"],
        evidence="antique french rococo clock brass gilt",
    )
    assert "Rococo" in tags
    assert "brass" in tags
    assert "Rolex" not in tags
    assert "Porsche" not in tags


def test_sanitize_keeps_luxury_when_listing_names_it() -> None:
    tags = sanitize_search_tags(
        ["Rolex", "Oyster", "steel"],
        evidence="Rolex Oyster Datejust steel watch",
    )
    assert "Rolex" in tags
    assert "Oyster" in tags


def test_suggest_includes_brand_material_and_mcm() -> None:
    tags = suggest_search_tags(
        title="Herman Miller George Nelson Action Office Desk",
        description="Mid-century roll top desk in teak.",
        brands=["Herman Miller"],
        specification_lines=["Material: Teak", "Era: 1960s"],
        category="Desks",
    )
    blob = " ".join(tags).lower()
    assert "herman miller" in blob
    assert "teak" in blob
    assert any(t.lower() in {"mcm", "mid century"} for t in tags)


def test_merge_fills_when_model_returns_too_few() -> None:
    tags = merge_search_tags(
        ["desk"],
        title="Herman Miller teak desk",
        brands=["Herman Miller"],
        specification_lines=["Material: Teak"],
        category="Desks",
    )
    assert len(tags) >= 3
    assert any("herman" in t.lower() for t in tags)


def test_format_ctseller_tag_field_is_comma_joined() -> None:
    assert format_ctseller_tag_field(["Herman Miller", "MCM"]) == "Herman Miller,MCM"


def test_lot_output_finalize_builds_search_tags() -> None:
    lot = LotOutput(
        sku="12",
        auction_title="Pyrex mixing bowls",
        professional_description="Primary-color Pyrex nesting bowls.",
        specification_lines=["Material: Glass", "Brand: Pyrex"],
        brands=["Pyrex"],
        ct_category="Kitchen & Home",
    )
    lot._finalize_listing_quality()
    assert lot.search_tags
    assert any("pyrex" in t.lower() for t in lot.search_tags)


def test_fully_audited_manifest_includes_search_tags() -> None:
    schema = pydantic_model_to_gemini_schema(FullyAuditedManifest)
    assert "search_tags" in schema["properties"]


def test_search_tags_for_lot_row_fills_empty_store() -> None:
    lot = SimpleNamespace(
        auction_title="Herman Miller teak desk",
        professional_description="Mid-century Action Office desk.",
        specification_lines_json='["Material: Teak"]',
        brands_json='["Herman Miller"]',
        labels_and_tags_json="[]",
        search_tags_json="[]",
        ct_category="Desks",
    )
    tags = _search_tags_for_lot_row(lot)
    assert any("herman" in t.lower() for t in tags)
    assert any("teak" in t.lower() for t in tags)


def test_search_tags_for_lot_row_keeps_saved_tags() -> None:
    lot = SimpleNamespace(
        auction_title="Herman Miller teak desk",
        professional_description="Desk",
        specification_lines_json="[]",
        brands_json='["Herman Miller"]',
        labels_and_tags_json="[]",
        search_tags_json='["Action Office", "MCM"]',
        ct_category="Desks",
    )
    tags = _search_tags_for_lot_row(lot)
    assert tags == ["Action Office", "MCM"]
