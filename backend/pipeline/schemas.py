"""Pydantic schemas for the estate inventory AI pipeline (Sections 2 and 5)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PhotoRole = Literal["hero", "item_front", "angle_only", "tape", "unusable"]
LabelLegibility = Literal["clear", "partial", "none"]
PhotoQuality = Literal["sharp", "ok", "blur"]
ConfidenceLevel = Literal["low", "medium", "high"]


class PhotoTriageResult(BaseModel):
    """Per-photo Stage 1 triage output (order-independent)."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    photo_role: PhotoRole
    shows_whole_lot: bool = False
    distinct_item_id: str | None = None
    is_primary_front_for_item: bool = False
    label_legibility: LabelLegibility
    brand_text: str | None = None
    quality: PhotoQuality
    duplicate_of_filename: str | None = None
    confidence: ConfidenceLevel
    shows_measuring_scale: bool = Field(
        default=False,
        description=(
            "True when a yellow measuring tape, metal ruler, yardstick, or similar scale is visibly "
            "measuring the merchandise (even if photo_role is angle_only)."
        ),
    )
    lot_tape_marking: str | None = Field(
        default=None,
        description=(
            "Handwritten or printed lot code on colored estate adhesive tape (purple, lavender, blue, neon green, "
            "or bright yellow masking/painter tape) when visible anywhere in this frame — e.g. MS530, SR006, #8524. "
            "Include tape on a roll or strip in the foreground. null if no estate lot tape marking is readable."
        ),
    )


class LowTierDraftItem(BaseModel):
    """Flash-derived item row after triage merge (listing draft building block)."""

    model_config = ConfigDict(extra="forbid")

    temporary_id: str
    suggested_title: str
    raw_ocr_text_found: str | None = None
    condition_cues: str = ""
    primary_front_filename: str
    alternate_label_filenames: list[str] = Field(default_factory=list)


class InventoryLotDraft(BaseModel):
    """Lot-level draft assembled from triage rows + deterministic selection."""

    model_config = ConfigDict(extra="forbid")

    primary_category: str = ""
    estimated_distinct_item_count: int = 0
    items: list[LowTierDraftItem] = Field(default_factory=list)
    selected_hero_filename: str = ""
    selected_tape_filenames: list[str] = Field(default_factory=list)
    analysis_photo_filenames: list[str] = Field(default_factory=list)
    brand_needs_review: bool = False


class BrandAuditResult(BaseModel):
    """Stage 2a brand audit (MVP — no grounded valuation)."""

    model_config = ConfigDict(extra="forbid")

    brands: list[str] = Field(default_factory=list)
    brand_needs_review: bool = False
    was_corrected_by_pro: bool = False
    correction_details: str | None = None
    source_filenames: list[str] = Field(default_factory=list)


class VerifiedAndPricedItem(BaseModel):
    """Stage 2b audited line item (Phase 2 — grounded valuation)."""

    model_config = ConfigDict(extra="forbid")

    line_item_id: int
    final_title: str
    confirmed_brand: str = Field(
        default="",
        description="Empty when unbranded — never placeholder tokens like Unknown.",
    )
    accurate_description: str = ""
    detected_condition_issues: str = ""
    suggested_liquidation_low: float = 0.0
    suggested_liquidation_high: float = 0.0
    valuation_rationale: str = ""
    was_corrected_by_pro: bool = False
    correction_details: str | None = None


class FullyAuditedManifest(BaseModel):
    """Pro multimodal audit manifest — proofreads listing text, brands, and item lines."""

    model_config = ConfigDict(extra="forbid")

    final_auction_title: str = Field(
        default="",
        description="Pro-corrected single-line auction title; lead with confirmed brand when readable.",
    )
    final_professional_description: str = Field(
        default="",
        description=(
            "Pro-corrected lot overview — warm CTBids seller voice (~50 words, one paragraph). Lead with appeal, "
            "not 'This is a…' or feature checklists. Vary adjectives — do not overuse handsome; prefer specific "
            "visual details. Exact sizes and marks belong in final_specification_lines; weave size casually in prose if needed."
        ),
    )
    final_specification_lines: list[str] = Field(
        default_factory=list,
        description=(
            "Pro-corrected spec bullets. When a ruler or measuring tape is visible, include "
            "**Dimensions:** / **Measurement:** lines with ~inch reads (or note that dimensions are shown in photos). "
            "Only these labels: Brand, Condition note, Size / tags, Dimensions, Measurement, Weight."
        ),
    )
    listing_was_corrected_by_pro: bool = False
    listing_correction_details: str | None = None
    total_items_processed: int = 0
    verified_inventory: list[VerifiedAndPricedItem] = Field(default_factory=list)


ListingChannel = Literal["ct_bids", "ebay", "either", "needs_review"]


class AppraisalItemLine(BaseModel):
    """One distinct object visible in a field appraisal check."""

    model_config = ConfigDict(extra="forbid")

    line_item_id: int
    title: str
    brand: str = ""
    estimated_low: float = 0.0
    estimated_high: float = 0.0
    notes: str = ""


class AppraisalCheckResult(BaseModel):
    """Structured output for job-site spot appraisal (Pro + search)."""

    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    recommended_starting_price: float = 0.0
    price_low: float = 0.0
    price_high: float = 0.0
    high_value: bool = False
    high_value_rationale: str = ""
    listing_channel: ListingChannel = "needs_review"
    listing_channel_rationale: str = ""
    recommended_ct_category: str = ""
    confidence: ConfidenceLevel = "medium"
    rationale: str = ""
    search_summary: str = ""
    items: list[AppraisalItemLine] = Field(default_factory=list)
