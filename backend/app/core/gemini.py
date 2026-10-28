from __future__ import annotations

import asyncio
import atexit
import base64
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import requests
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.config import settings
from app.services.ctseller_categories import ct_categories_for_ai
from app.core.lotting import (
    MERGED_PRO_DESCRIPTION_MAX_CHARS,
    PROFESSIONAL_DESCRIPTION_MAX_WORDS,
    _broaden_auction_title_for_components,
    _drop_prose_sentences_conflicting_with_components,
    _ensure_multi_piece_description,
    _trim_prose_at_limit,
    _trim_prose_to_max_words,
    dedupe_specification_lines_preserve_order,
    infer_brands_from_label_context,
    normalize_brand_list,
    sanitize_description_unknowns,
    sku_digits,
    tape_marking_canonical,
)
from app.services.image_preprocess import prepare_gemini_image_file

TRANSIENT_HTTP = frozenset({429, 502, 503, 504})
TRANSIENT_GENAI_CODES = frozenset({408, 429, 502, 503, 504})

_tape_verify_pool: ThreadPoolExecutor | None = None


def _get_tape_verify_pool() -> ThreadPoolExecutor:
    global _tape_verify_pool
    if _tape_verify_pool is None:
        _tape_verify_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tape_verify")
    return _tape_verify_pool


def _shutdown_tape_verify_pool() -> None:
    global _tape_verify_pool
    if _tape_verify_pool is not None:
        _tape_verify_pool.shutdown(wait=False, cancel_futures=False)
        _tape_verify_pool = None


atexit.register(_shutdown_tape_verify_pool)


def _tape_verify_prompt_extra() -> str:
    extra = (settings.tape_verify_prompt_suffix or "").strip()
    if not extra:
        return ""
    return f"\n\nAdditional instructions:\n{extra}"

# --- google-genai structured row: manifest table + estate catalog extensions ---


class InventoryTableEntry(BaseModel):
    """Response schema for `google-genai` JSON mode; maps to LotOutput.from_model keys."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    manifest_id: str = ""
    item_name: str = Field(
        default="",
        description=(
            "Hook-first auction title: brand first when a label is readable, then distinctive material/handle/finish "
            "before the object. Title case. Visible subjects only. Do not use the word lot."
        ),
    )
    brand: str = Field(
        default="",
        description=(
            "One primary maker from a readable label or hallmark; empty if none. Never Unknown, Unbranded, or N/A."
        ),
    )
    material: str = Field(
        default="",
        description=(
            "Primary visible material. Hallmark-first; brass/bronze ≠ pewter. Must match title and prose."
        ),
    )
    condition_notes: str = Field(
        default="",
        validation_alias=AliasChoices("condition_notes", "ConditionNotes", "Condition"),
        description="Exactly one of NEW, LIKE NEW, VERY GOOD, GOOD, or FAIR.",
    )
    era: str = ""
    sku: str | None = Field(
        default=None,
        validation_alias=AliasChoices("sku", "SKU"),
        description="Lot code on colored estate adhesive tape only — not measuring tape or box ITEM#.",
    )
    sku_read_confidence: str | None = Field(
        default=None,
        validation_alias=AliasChoices("sku_read_confidence", "SkuConfidence"),
    )
    professional_description: str = Field(
        default="",
        validation_alias=AliasChoices("professional_description", "ProfessionalDescription"),
        description=(
            "Warm ~50-word paragraph: flair, then title hook. No 'This is a…'. No condition essay."
        ),
    )
    specification_lines: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("specification_lines", "SpecificationLines"),
        description=(
            "Only these labels: Brand:, Condition note:, Size / tags: (apparel/shoes/bags), Dimensions:, Weight:. "
            "Add Dimensions: when a ruler/tape is measuring the item; Weight: when a scale readout is visible. "
            "Do not invent numbers."
        ),
    )
    labels_and_tags: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("labels_and_tags", "LabelsAndTags"),
        description="Apparel/footwear/handbags only: verbatim sewn-in or hang-tag strings. Empty otherwise.",
    )
    starting_price_usd: float | None = Field(
        default=None,
        validation_alias=AliasChoices("starting_price_usd", "StartingPrice"),
    )
    ct_category: str = Field(
        default="",
        validation_alias=AliasChoices("ct_category", "CtCategory", "category"),
        description=(
            "Exact CT Seller category from the allow-list. Prefer specific names over Household Goods Other."
        ),
    )


class TapeSkuAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    new_lot_from_tape: bool = Field(default=False)
    lot_digits_on_tape: str | None = Field(default=None)


SYSTEM_INSTRUCTION = (
    "You are an AI assistant for Caring Transitions of Enterprise-Las Vegas, owned by Julie Zorn. "
    "Analyze photos and generate professional manifest descriptions. Output structured JSON only.\n\n"
    "### BRAND (ONE MAKER):\n"
    "- Identify exactly ONE primary brand. No brand-soup, related-brand guesses, or competing lists.\n"
    "- Only names explicitly visible on the item, label, or hallmark. If several marks appear, use the one "
    "clearest on the physical hallmark. If none is readable, leave brand empty — never Unknown, Unidentified, Unbranded, or N/A.\n"
    "- **Czechoslovakia** is country-of-origin text, not a maker.\n"
    "- **Liz & Co. / Liz Claiborne** is **not** Tiffany & Co. If a stamp reads Liz, LIZ&CO, Liz & Co, or Liz Claiborne, "
    "use that maker — never retitle it Tiffany from an '& Co' stamp.\n"
    "- Do **not** lead with Tiffany, Cartier, Rolex, Van Cleef, or similar luxury houses unless those **exact words** "
    "are clearly readable on a mark. Gold-tone / silver-tone costume jewelry with '& Co' is usually Liz & Co, Napier, "
    "Monet, or unbranded — not Tiffany.\n"
    "- **Apparel:** brand = the **sewn-in neck/care label** (e.g. St. John's Bay, Levi's), never a zipper pull, snap vendor, "
    "or generic hardware unless that same name is on the garment label. Copy the label maker into **brand** and the "
    "verbatim tag into **labels_and_tags**.\n\n"
    "### VISIBLE SUBJECT ONLY:\n"
    "- Describe only what is **clearly visible**. Do not invent objects, animals, or catalog-category words.\n"
    "- Use **flower frog** only for a visible flower-arranging frog / pin holder — a bird on a branch with a cup is a "
    "bird figurine or posy vase, not a frog. If the ceramic form is uncertain, use a neutral term.\n"
    "- **Device screens are not the merchandise:** catalog the phone, tablet, monitor, or TV itself — never the on-screen picture.\n\n"
    "### TITLE + VOICE:\n"
    "- **item_name:** hook-first auction title — Brand (when known) + distinctive material/handle/finish **before** the object type. "
    "Title case. Shape: 'St. John's Bay Cotton-Blend Zip-Up Jacket', 'Pedersoli Brown Bess Flintlock Musket'. "
    "Do **not** use the word **lot**. Do not lead with Pair of / Set of two when the noun is already plural "
    "(Sculptures, Knives, Figurines, Earrings). Never title from a macro/detail frame alone ('Detail with Zippered Pocket'). "
    "Jewelry: Brand + metal tone + distinctive form + type + (count); never pad with Collection, Statement, Fashion, "
    "Assorted, Ft., Feat., or Featuring. Put the number in parentheses at the end — (5), not (5 Pcs); do not lead with Three/Four. "
    "Lead with a short readable brand; do not trail ', including Brand'.\n"
    "- **professional_description:** ~50 words max, one paragraph, warm CTBids seller voice. Open with appeal "
    "(look, display, who would love it), then restate the title hook in the same Title Case. "
    "No 'This is a…', 'This lot consists of…', 'The item features…', or 'Each … features…'. "
    "Do not chain 'is marked on the base with' or 'Dimensions are shown in the photos, with a height of…'. "
    "Do not spam **handsome** — at most one generic praise word; prefer a specific visual detail "
    "(cross-hatch cut, brass patina, flintlock lockplate, woven texture). Rotate: polished, classic, striking, solid, "
    "crisp, display-ready, well-made, inviting, timeless, sharp-looking, charming. "
    "No condition essay in prose. Marks, mold numbers, and exact sizes → specification_lines; at most one casual size clause "
    "('about 6¼ in tall per photos').\n"
    "- **Good:** 'Deer Hoof Handled Hunting Knives' — 'A rustic showpiece for the cabin — deer hoof handled hunting knives with polished blades.'\n"
    "- **Bad:** 'This is a pair of hunting knives with deer hoof handles. Each knife features a polished blade and is marked on the tang…'\n"
    "- Multi-object titles must name the salient types (or type + count). Prefer material + object / group / set / these pieces "
    "over 'this lot' / 'Estate lot'. Never open item_name with Estate lot.\n"
    "- Do not use vintage as a default filler. Use vintage/antique only with a specific decade, copyright year, or era tag. "
    "Never open item_name with Vintage without dated evidence. At most one use of vintage in title + description combined. "
    "Do not set Era: Vintage or Era: Antique alone — use a real period or omit Era.\n\n"
    "### CONDITION:\n"
    "- **condition_notes** is exactly one of NEW, LIKE NEW, VERY GOOD, GOOD, FAIR. No condition essay in prose. "
    "At most one **Condition note:** spec line with a single combined wear sentence — not several condition bullets.\n\n"
    "### METAL (HALLMARK-FIRST):\n"
    "- Read visible hallmarks first: 925 / STERLING / Lion Passant → Sterling Silver; EPNS / Silver Plate / Plate → Silver Plate; "
    "Pewter / Zinn / étain → Pewter. Without a confirmable mark, do **not** call it Sterling; use silver-tone metal.\n"
    "- Luster clues: mirror-like often plate; soft/dull often pewter; warm-white can support sterling only with a hallmark. "
    "Pitting can indicate pewter; base-metal bleed/wear-through can indicate plate.\n"
    "- Pewter only with a pewter/zinn mark or clear cool-gray pewter luster — not a guess for dark satin table metal. "
    "**Brass/bronze ≠ pewter.** Warm gold-brown cast, spun, or lathe-turned decorative metal is Brass or Bronze "
    "(those lots often read darker in photos than shiny yellow brass).\n"
    "- **item_name**, **professional_description**, JSON **material**, and any Material: line **must match**. "
    "If unsure between brass and pewter, use metalware / dark-toned metal / non-precious metal — never contradict yourself.\n\n"
    "### BRASS / BRONZE GROUPINGS:\n"
    "- Name **all visible forms** (censers, bells, ewers, dragon stands, strikers). Do **not** title only the dragon stand. "
    "Bells are separate from censers and ewers — if a bell is visible, include it in the title and the prose overview.\n\n"
    "### MULTI-PIECE:\n"
    "- Cover every major visible piece in **item_name** and a short overview in **professional_description**. "
    "Do not collapse the grouping to one subset (e.g. only cups) if a flagon, tray, or unique piece is present. "
    "Prioritize dominant/structural pieces, then smaller ones. "
    "If several distinct pieces share a frame, use a compound title and name positions or counts by type "
    "(left / center / right). Leave brand empty unless every piece is the same maker — never invent one brand for mixed sculpts.\n"
    "- **Wardrobe / hanger stacks:** inventory is the number of separate garments, not the number of photos. "
    "Keep details in title + prose; put sewn-in labels and sizes in **labels_and_tags** only.\n"
    "- **Lace / linen:** different runners, scarves, or panels with different tape measurements = a multi-piece textile grouping. "
    "Title and prose must cover the whole spread, not one rectangle; add Dimensions: per piece when the tape applies.\n\n"
    "### COINS / MEDALS:\n"
    "- Do not invent headcounts when coins overlap, sit in piles, or spill from rolls.\n"
    "- Name dates you can actually see (coin faces, envelopes, cards) — never invent years. If types are unclear, "
    "say assorted U.S. coinage.\n"
    "- Coins in map slots or album pages are part of that product, not separate items.\n\n"
    "### SPECIFICATION LINES:\n"
    "- Only these labels: **Brand:**, **Condition note:** (at most one combined sentence), **Size / tags:** (apparel/shoes/bags), "
    "**Dimensions:**, **Weight:**. Do not transcribe stamps, mold numbers, or bottom-mark essays.\n\n"
    "### DIMENSIONS / WEIGHT:\n"
    "- When a ruler, yellow measuring tape, yardstick, or quilting ruler is **measuring** the item, add **Dimensions:** "
    "with units (prefer inches, ~ if estimated) and name what was measured "
    "(e.g. 'Dimensions: Overall width ~10 in. (tape along front)'). Do not invent numbers. "
    "If unreadable, note 'Dimensions: Shown with ruler in photos (readout unclear)'. Omit the line if no scale is visible.\n"
    "- When a weight-scale readout is visible, add **Weight:** with units (e.g. 'Weight: 2.4 lb') — not Dimensions. "
    "If the readout is unreadable, note that a scale is shown.\n\n"
    "### SKU:\n"
    "- Estate lot tape only (colored adhesive), not measuring tape or box ITEM#. "
    "Letter-prefix codes (MS530, SR006) on that tape are valid SKUs. Never sewn tags, size tags, UPC, or stamped body numbers. "
    "When two images are provided, image 2 is a tape crop — prioritize it for SKU.\n\n"
    "### CERAMICS:\n"
    "- Exactly one primary brand. **Czechoslovakia ≠ maker** — do not invent a factory from origin text alone.\n\n"
    "### FINISH:\n"
    "- Describe visible sheen (glazed ceramic, satin metal, polished chrome, patinated brass). "
    "Use Lusterware, Faux Bois, or Geometric Art Deco only when that finish is clearly visible "
    "(iridescent glaze, wood-grain slip, strong stepped geometry). If unsure, say glazed or painted ceramic. "
    "Metal finish (polished / brushed / satin) is separate from sterling vs plate — follow the hallmark.\n\n"
    "Identify brand, material, condition_notes, and era. Set **ct_category** to the best CT Seller top-level category "
    "(prefer specific names over Household Goods Other). Output must match JSON schema only."
)


def _ct_category_prompt_line() -> str:
    names = ", ".join(ct_categories_for_ai())
    return (
        f"- ct_category: Exact CT Seller category for this lot. Must be one of: {names}. "
        "Prefer specific categories (Electronics, Kitchen & Home, Glassware, Books, Women's, etc.) over "
        "Household Goods Other when the photos support it. Gaming mice, keyboards, headsets, TVs, and "
        "similar consumer tech → Electronics (not Household Goods Other).\n"
    )


def _user_prompt(*, manifest_tag: str) -> str:
    tag_line = f"Manifest / batch tag: {manifest_tag.strip()}\n" if manifest_tag.strip() else ""
    return (
        tag_line
        + "Analyze this catalog photo for an estate inventory row.\n"
        + "RULES:\n"
        + _ct_category_prompt_line()
        + "- item_name: One-line hook-first auction title (brand first when a label is legible). Visible subjects only.\n"
        + "- brand: ONE primary maker, or empty if none is readable. Apparel = sewn-in neck/care label, not zipper pulls. "
        "Copy label text into labels_and_tags.\n"
        + "- material: Hallmark-first; brass/bronze ≠ pewter; title/prose/material must match.\n"
        + "- professional_description: Warm ~50-word paragraph; flair then title hook; no 'This is a…'; no condition essay.\n"
        + "- condition_notes: Exactly one of:\n"
        "  * NEW: no signs of wear; may include tags/packaging.\n"
        "  * LIKE NEW: clearly unused, but tags may be missing.\n"
        "  * VERY GOOD: minor signs of wear (light scuffs/scratches).\n"
        "  * GOOD: moderate signs of wear (moderate scuffs/scratches).\n"
        "  * FAIR: heavy wear and visible scratches/damage.\n"
        + "- specification_lines: only Brand:, Condition note:, Size / tags: (apparel/shoes/bags), Dimensions:, Weight:. "
        "Dimensions required when a ruler/tape is measuring the item; Weight required when a scale readout is visible.\n"
        + "- labels_and_tags: apparel/footwear/handbags only — empty for coins, art supplies, home goods.\n"
        + "- sku: estate lot tape only, not measuring tape or box ITEM#; null if not on tape.\n"
        + "- Czechoslovakia is origin, not a maker. No luxury-house names without an exact readable mark.\n"
        + "- Coins: don't invent counts; name dates you can see; map/album coins are not separate items.\n"
        + "- Footwear: open-toe perforated shoes are clogs/mules, not Mary Jane.\n"
        + "- Multi-brand wardrobe: leave brand empty; put sewn-in labels in labels_and_tags only.\n"
    )


def _normalize_condition_label(raw: str) -> str:
    from app.services.ctseller_conditions import canonicalize_ct_condition

    return canonicalize_ct_condition(raw)


_BRASS_NARRATIVE_RE = re.compile(
    r"\b(brass|brassware|bronze|ormolu|gilt\s*metal|spelter)\b",
    re.I,
)
_PEWTER_TOKEN_RE = re.compile(r"\b(pewter|zinn|étain|etain)\b", re.I)


def _spec_lines_without_material_label(specs: list[str]) -> list[str]:
    return [s for s in specs if not re.match(r"^\s*material\s*:", s.strip(), re.I)]


def _infer_metal_material(
    *, material: str, specs: list[str], description: str, title: str
) -> str | None:
    """
    Infer a single Material: upsert value. Excludes existing ``Material:`` spec lines from the blob so a wrong
    model-only pewter bullet cannot self-reinforce; resolves brass-in-prose vs pewter-in-schema contradictions when
    pewter is not repeated on non-Material spec lines.
    """
    narrative = f"{description} {title}".strip().lower()
    specs_non_mat = _spec_lines_without_material_label(specs)
    body_no_mat_specs = " ".join(specs_non_mat).lower()
    mat_low = (material or "").strip().lower()

    brass_in_narrative = bool(_BRASS_NARRATIVE_RE.search(narrative))
    pewter_in_non_material_specs = bool(_PEWTER_TOKEN_RE.search(body_no_mat_specs))
    pewter_in_material_field = bool(_PEWTER_TOKEN_RE.search(mat_low))
    pewter_in_material_spec_lines = any(
        _PEWTER_TOKEN_RE.search(s)
        for s in specs
        if re.match(r"^\s*material\s*:", s.strip(), re.I)
    )

    if brass_in_narrative and (pewter_in_material_field or pewter_in_material_spec_lines) and not pewter_in_non_material_specs:
        # Pewter only came from the structured material field and/or a ``Material:`` line — prose/title says brass.
        return "Brass"

    evidence = " ".join([material, description, title, *specs_non_mat]).lower()
    has_pewter = bool(_PEWTER_TOKEN_RE.search(evidence))
    has_plate = any(k in evidence for k in ("silver plate", "silverplate", "epns"))
    has_sterling = any(k in evidence for k in (" sterling", "sterling ", " 925", "925 "))

    # Hallmark-first preference: explicit pewter/zinn/etain should override loose silver-plate wording.
    if has_pewter:
        return "Pewter"
    if has_sterling:
        return "Sterling Silver"
    if has_plate:
        return "Silver Plate"
    return None


def _upsert_material_spec(specs: list[str], material_value: str | None) -> list[str]:
    out = [s for s in specs if not re.match(r"^\s*material\s*:", s.strip(), re.I)]
    if material_value:
        out.append(f"Material: {material_value}")
    return out


def inventory_table_entry_to_lot_payload(row: InventoryTableEntry) -> dict[str, Any]:
    sku_clean = tape_marking_canonical(row.sku)
    title = (row.item_name.strip() or (f"Estate item #{sku_clean}" if sku_clean else "Estate catalog item"))
    specs = [
        s
        for s in row.specification_lines
        if s.strip() and not re.match(r"^\s*markings?\s*:", s.strip(), re.I)
    ]
    desc = row.professional_description.strip()
    metal_material = _infer_metal_material(
        material=row.material.strip(),
        specs=specs,
        description=desc,
        title=title,
    )
    material_value = metal_material or row.material.strip() or None
    if not material_value and _BRASS_NARRATIVE_RE.search(f"{desc} {title}".strip().lower()):
        material_value = "Brass"
    specs = _upsert_material_spec(specs, material_value)
    if row.era.strip() and not any(s.lower().startswith("era:") for s in specs):
        specs.append(f"Era: {row.era.strip()}")
    specs = dedupe_specification_lines_preserve_order(specs)
    title = _broaden_auction_title_for_components(title, specs)
    desc = sanitize_description_unknowns(desc)
    desc = _drop_prose_sentences_conflicting_with_components(desc, specs)
    desc = _trim_prose_to_max_words(desc, max(1, PROFESSIONAL_DESCRIPTION_MAX_WORDS - 8))
    desc = _ensure_multi_piece_description(desc, specs)
    desc = _trim_prose_to_max_words(desc, PROFESSIONAL_DESCRIPTION_MAX_WORDS)
    desc = _trim_prose_at_limit(desc, MERGED_PRO_DESCRIPTION_MAX_CHARS)

    desc = desc or title
    brand_list = normalize_brand_list([row.brand.strip()] if row.brand.strip() else [])
    brand_list = normalize_brand_list(
        brand_list + infer_brands_from_label_context(row.labels_and_tags, specs)
    )
    return {
        "SKU": sku_clean or None,
        "AuctionTitle": title,
        "ProfessionalDescription": desc,
        "SpecificationLines": specs,
        "Brands": brand_list,
        "brand": brand_list[0] if brand_list else "",
        "LabelsAndTags": row.labels_and_tags,
        "Condition": _normalize_condition_label(row.condition_notes),
        "StartingPrice": row.starting_price_usd,
        "SkuConfidence": row.sku_read_confidence,
        "ct_category": (row.ct_category or "").strip(),
    }


def inventory_table_entry_gemini_schema() -> dict[str, Any]:
    """Gemini JSON schema for InventoryTableEntry with ct_category enum allow-list."""
    from pipeline.gemini_schema import pydantic_model_to_gemini_schema

    schema = pydantic_model_to_gemini_schema(InventoryTableEntry)
    props = schema.get("properties") if isinstance(schema, dict) else None
    if isinstance(props, dict):
        cat = props.get("ct_category")
        if isinstance(cat, dict):
            cat["enum"] = ct_categories_for_ai()
    return schema


_genai_client: Any = None


def _get_genai_client() -> Any | None:
    global _genai_client
    key = (settings.google_api_key or "").strip()
    if not key or _genai_client: return _genai_client
    from google import genai
    _genai_client = genai.Client(api_key=key)
    return _genai_client


def _analyze_photo_sdk_from_bytes(
    image_bytes: bytes,
    mime_type: str,
    *,
    manifest_tag: str = "",
    focus_jpeg: bytes | None = None,
) -> dict[str, Any] | None:
    client = _get_genai_client()
    if not client: return None
    from google.genai import types as genai_types

    focus_note = (
        "\nA second image follows: high-contrast crop of colored estate lot tape. Read SKU from this crop."
        if focus_jpeg
        else ""
    )
    contents = [
        _user_prompt(manifest_tag=manifest_tag) + focus_note,
        genai_types.Part(inline_data=genai_types.Blob(mime_type=mime_type, data=image_bytes)),
    ]
    if focus_jpeg:
        contents.append(genai_types.Part(inline_data=genai_types.Blob(mime_type="image/jpeg", data=focus_jpeg)))

    config = genai_types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=inventory_table_entry_gemini_schema(),
        system_instruction=SYSTEM_INSTRUCTION,
    )

    try:
        response = client.models.generate_content(model=settings.gemini_model, contents=contents, config=config)
        if response.parsed:
            row = InventoryTableEntry.model_validate(response.parsed)
            return inventory_table_entry_to_lot_payload(row)
    except Exception:
        return None
    return None


def _model_url() -> str:
    return f"https://generativelanguage.googleapis.com/{settings.gemini_api_version}/models/{settings.gemini_model}:generateContent"


def _generate_content(payload: dict[str, Any], timeout: int = 120) -> str | None:
    from app.core.gemini_slots import gemini_slot

    headers = {"Content-Type": "application/json", "x-goog-api-key": settings.google_api_key}
    for _ in range(3): # Shorter retry loop for responsiveness
        try:
            with gemini_slot():
                response = requests.post(_model_url(), headers=headers, json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            time.sleep(2)
    return None


def _analyze_photo_rest_from_bytes(
    image_bytes: bytes,
    mime_type: str,
    *,
    manifest_tag: str = "",
    focus_jpeg: bytes | None = None,
) -> dict[str, Any] | None:
    img_b64 = base64.b64encode(image_bytes).decode("utf-8")
    prompt = _user_prompt(manifest_tag=manifest_tag) + "\nReturn JSON matching schema. Decisiveness required."
    parts = [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": img_b64}}]
    if focus_jpeg:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(focus_jpeg).decode("utf-8")}})
    
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "responseSchema": inventory_table_entry_gemini_schema(),
        },
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
    }
    text = _generate_content(payload)
    if not text: return None
    try:
        parsed = json.loads(re.sub(r"```json|```", "", text).strip())
        row = InventoryTableEntry.model_validate(parsed)
        return inventory_table_entry_to_lot_payload(row)
    except Exception:
        return None


def analyze_photo_from_bytes(image_bytes: bytes, mime_type: str, **kwargs) -> dict[str, Any] | None:
    return _analyze_photo_sdk_from_bytes(image_bytes, mime_type, **kwargs) or _analyze_photo_rest_from_bytes(image_bytes, mime_type, **kwargs)


def analyze_path_with_retries(photo_path: Path, *, manifest_tag: str = "") -> dict[str, Any] | None:
    from app.services.tape_focus import estate_tape_focus_jpeg_from_path
    focus_jpeg = None
    if settings.tape_focus_enabled:
        try: focus_jpeg = estate_tape_focus_jpeg_from_path(photo_path)
        except Exception: pass

    img_bytes, mime = prepare_gemini_image_file(photo_path, max_edge=settings.gemini_image_max_edge, quality=settings.gemini_jpeg_quality)
    return analyze_photo_from_bytes(img_bytes, mime, manifest_tag=manifest_tag, focus_jpeg=focus_jpeg)


async def analyze_photos_parallel_async(
    photo_paths: list[Path],
    *,
    manifest_tag: str = "",
    limit: int | None = None,
    on_each: Callable[[int, int, Path, dict[str, Any] | None], None] | None = None,
) -> list[dict[str, Any] | None]:
    sem = asyncio.Semaphore(max(1, settings.gemini_parallelism if limit is None else limit))
    total = len(photo_paths)

    async def _one(idx: int, path: Path) -> tuple[int, dict[str, Any] | None]:
        result: dict[str, Any] | None = None
        try:
            async with sem:
                result = await asyncio.to_thread(
                    analyze_path_with_retries,
                    path,
                    manifest_tag=manifest_tag,
                )
        except Exception:
            result = None
        if on_each is not None:
            try:
                on_each(idx, total, path, result)
            except Exception:
                pass
        return idx, result

    pairs = await asyncio.gather(*[_one(i, p) for i, p in enumerate(photo_paths)])
    pairs.sort(key=lambda x: x[0])
    return [p[1] for p in pairs]


def analyze_photo(photo_path: Path, **kwargs) -> dict[str, Any] | None:
    return analyze_path_with_retries(photo_path, **kwargs)

# --- Tape Verification logic follows standard audit rules ---
def verify_tape_sku(photo_path: Path, claimed_marking: str) -> bool | None:
    timeout = float(settings.tape_verify_total_timeout_seconds)
    fut = _get_tape_verify_pool().submit(_verify_tape_sku_inner, photo_path, claimed_marking)
    try: return fut.result(timeout=timeout)
    except Exception: return None

def _normalize_tape_audit_reading(raw: object) -> str:
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s or s.lower() in ("null", "none", ""):
        return ""
    return (tape_marking_canonical(s) or sku_digits(s) or "").strip().upper()


def _verify_tape_sku_inner(photo_path: Path, claimed_marking: str) -> bool | None:
    claim = (claimed_marking or "").strip()
    if not claim:
        return None
    prompt = (
        f"Audit: Is the lot marking {claim!r} clearly visible on estate lot tape? "
        "Tape may be PURPLE, LAVENDER, BLUE, bright NEON GREEN, or bright YELLOW adhesive masking/painter tape "
        "with handwriting or print on its surface — including on a **tape roll** in the foreground. "
        "Letter-prefix codes (e.g. MS530) count when the letters+digits are on that colored tape. "
        "Set new_lot_from_tape false if the marking is only on a yellow MEASURING tape (inch/cm scale), ruler, "
        "sticker, tag, or item body. "
        "Return JSON {\"new_lot_from_tape\": bool, \"lot_digits_on_tape\": string}. "
        "lot_digits_on_tape: exact marking read from the tape (letters+digits as seen), or null if none/unreadable."
    )
    prompt = prompt + _tape_verify_prompt_extra()
    img_bytes, mime = prepare_gemini_image_file(
        photo_path,
        max_edge=settings.triage_image_max_edge,
        quality=settings.gemini_jpeg_quality,
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime, "data": base64.b64encode(img_bytes).decode("utf-8")}}]}]
    }
    text = _generate_content(payload)
    if not text: return None
    try:
        res = json.loads(re.sub(r"```json|```", "", text).strip())
        if res.get("new_lot_from_tape") is not True:
            return False
        read_norm = _normalize_tape_audit_reading(res.get("lot_digits_on_tape"))
        claim_norm = _normalize_tape_audit_reading(claim)
        return bool(claim_norm) and read_norm == claim_norm
    except Exception: return None