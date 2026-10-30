"""Flash listing prompt budget, schema trim, and Pro specialty handoff."""

from __future__ import annotations

import json

from app.config import settings
from app.core.gemini import (
    SYSTEM_INSTRUCTION,
    InventoryTableEntry,
    _user_prompt,
    inventory_table_entry_gemini_schema,
)
from app.services.pipeline_integration import EST_PIPELINE_LISTING_INPUT
from pipeline.pipeline import PRO_AUTHOR_SPECIALTY_GUIDANCE


def _approx_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))


def test_listing_schema_drops_lot_boundary_blank_page() -> None:
    schema = inventory_table_entry_gemini_schema()
    props = schema.get("properties") or {}
    assert "lot_boundary_blank_page" not in props
    assert "lotBoundaryBlankPage" not in props
    assert "lot_boundary_blank_page" not in InventoryTableEntry.model_fields


def test_specification_lines_field_teaches_only_allowed_labels() -> None:
    desc = InventoryTableEntry.model_fields["specification_lines"].description or ""
    for label in ("Brand:", "Condition note:", "Size / tags:", "Dimensions:", "Weight:"):
        assert label in desc
    for banned in ("Components:", "Item n:", "Item 1:", "Denomination:"):
        assert banned not in desc


def test_system_prompt_does_not_teach_components_or_item_rows() -> None:
    assert "Item 1:" not in SYSTEM_INSTRUCTION
    assert "Item n:" not in SYSTEM_INSTRUCTION
    assert "Components:" not in SYSTEM_INSTRUCTION


def test_flash_listing_must_keep_rules_present() -> None:
    blob = SYSTEM_INSTRUCTION + _user_prompt(manifest_tag="")
    for needle in (
        "ONE primary brand",
        "Liz & Co",
        "Tiffany",
        "sewn-in",
        "zipper",
        "Device screens",
        "NEW, LIKE NEW, VERY GOOD, GOOD, FAIR",
        "estate lot tape",
        "box ITEM#",
        "50 words",
        "This is a",
        "handsome",
        "Brass/bronze",
        "pewter",
        "Czechoslovakia",
    ):
        assert needle in blob, f"missing must-keep rule: {needle}"


def test_flash_listing_text_token_budget() -> None:
    system_tok = _approx_tokens(SYSTEM_INSTRUCTION)
    user_tok = _approx_tokens(_user_prompt(manifest_tag=""))
    schema_tok = _approx_tokens(json.dumps(inventory_table_entry_gemini_schema(), ensure_ascii=False))
    total = system_tok + user_tok + schema_tok
    assert 2500 <= total <= 7500, (
        f"system={system_tok} user={user_tok} schema={schema_tok} total={total} "
        "(trimmed Flash text; ~10k before collapsing restated rules)"
    )


def test_listing_cost_rates_and_token_estimate() -> None:
    assert settings.gemini_flash_input_cost_per_million == 0.75
    assert settings.gemini_flash_output_cost_per_million == 3.75
    assert EST_PIPELINE_LISTING_INPUT == 5500


def test_pro_author_keeps_coin_and_ceramic_guidance() -> None:
    g = PRO_AUTHOR_SPECIALTY_GUIDANCE
    assert "Littleton" in g
    assert "map slots" in g
    assert "Royal Bonn" in g
    assert "Hutschenreuther" in g
    assert "Haas & Czjzek" in g
    assert "Ditmar-Urbach" in g
    assert "Czechoslovakia" in g
    assert "never invent years" in g
