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
    measurement_text: str | None = Field(
        default=None,
        description=(
            "Verbatim size read off a visible ruler / measuring tape / yardstick that is measuring the item, "
            "with units — e.g. '12 in', '~30 cm', '18 x 24 in', 'H 6.25 in'. Include what was measured when clear "
            "(width/height/diameter). null when no measuring scale is visible or the numbers are unreadable — never guess."
        ),
    )
    shows_weight_scale: bool = Field(
        default=False,
        description=(
            "True when the item sits on a weight scale (kitchen, postal, hanging, luggage, or bathroom scale) and a "
            "weight readout is visible in the frame (even if photo_role is angle_only)."
        ),
    )
    weight_text: str | None = Field(
        default=None,
        description=(
            "Verbatim weight read off a visible scale display, with units — e.g. '2.4 lb', '540 g', '1.2 kg', '3 lb 4 oz'. "
            "null when no scale readout is visible or the numbers are unreadable — never guess."
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
        description=(
            "Pro-corrected single-line auction title; lead with confirmed brand when readable. Put distinctive "
            "material/handle/finish before the object name (hook-first), not trailing 'with …'. Compose a clean, "
            "human-readable title — NEVER copy estate-tag field labels (No., STYLE, SIZE, PRICE, COLOR, QTY) or raw "
            "tag OCR into the title; those values belong in final_specification_lines."
        ),
    )
    final_professional_description: str = Field(
        default="",
        description=(
            "Pro-corrected lot overview — warm CTBids seller voice (~50 words, one paragraph). Open with a short flair "
            "clause, then immediately restate the title hook (material/handle/finish + object) in natural words. "
            "Not 'This is a…' or feature checklists. Vary adjectives — do not overuse handsome; prefer specific "
            "visual details. Exact sizes and marks belong in final_specification_lines; weave size casually in prose if needed."
        ),
    )
    final_specification_lines: list[str] = Field(
        default_factory=list,
        description=(
            "Pro-corrected spec bullets. When a ruler or measuring tape is visible, include a "
            "**Dimensions:** / **Measurement:** line with the read (~inch, or note dimensions are shown in photos). "
            "When the item is on a weight scale with a visible readout, include a **Weight:** line with the read "
            "(e.g. '2.4 lb', '540 g'). Only these labels: Brand, Condition note, Size / tags, Dimensions, Measurement, Weight."
        ),
    )
    ct_category: str = Field(
        default="",
        description=(
            "Exact CT Seller top-level category for posting this lot. Prefer a specific category "
            "(Electronics, Kitchen & Home, Glassware, Books, Women's, etc.) over Household Goods Other. "
            "Gaming mice/keyboards/headsets, TVs, tablets, and phones → Electronics. "
            "Must be one of the allowed CT Seller category names from the prompt allow-list."
        ),
    )
    condition_notes: str = Field(
        default="GOOD",
        description=(
            "Exact CT Seller item condition tier for this lot. Must be exactly one of: "
            "NEW, LIKE NEW, VERY GOOD, GOOD, FAIR. "
            "NEW = tags/packaging intact unused; LIKE NEW = unused without tags; "
            "VERY GOOD = minor wear; GOOD = moderate wear; FAIR = heavy wear."
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


class AudienceGroupHit(BaseModel):
    """One public collector community (group, club, or forum) — official URL only."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str = ""
    why: str = ""


class AudienceHuntResult(BaseModel):
    """Structured audience hunt after a Google Search research memo."""

    model_config = ConfigDict(extra="forbid")

    worthy: bool = False
    skip_reason: str = ""
    groups: list[AudienceGroupHit] = Field(default_factory=list)
    blurb_ctbids: str = ""
    blurb_hold: str = ""
    recommend_hold: bool = False
