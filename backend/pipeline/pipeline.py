"""Stage 1 triage + Stage 2 brand audit orchestration (isolated prototype).

Implements Sections 3, 4, 5, and 11 of estate_pipeline_spec.md without touching
production backend routes or existing gemini integration.
"""

from __future__ import annotations

import io
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from google import genai
from google.genai import types

from pipeline.gemini_schema import fully_audited_manifest_gemini_schema, pydantic_model_to_gemini_schema
from pipeline.schemas import (
    BrandAuditResult,
    FullyAuditedManifest,
    InventoryLotDraft,
    LowTierDraftItem,
    PhotoTriageResult,
    VerifiedAndPricedItem,
)

# --- Environment knobs (Section 10) — read directly; no app.config import ---

GEMINI_FLASH_MODEL = os.environ.get("GEMINI_FLASH_MODEL", "gemini-2.5-flash")
GEMINI_PRO_MODEL = os.environ.get("GEMINI_PRO_MODEL", "gemini-2.5-pro")
LISTING_FLASH_MAX_FRAMES = max(1, int(os.environ.get("LISTING_FLASH_MAX_FRAMES", "5")))
DIMENSION_FLASH_MAX_FRAMES = max(1, int(os.environ.get("DIMENSION_FLASH_MAX_FRAMES", "3")))
BRAND_PRO_MAX_LABEL_FRAMES = max(1, int(os.environ.get("BRAND_PRO_MAX_LABEL_FRAMES", "4")))
BRAND_SKIP_PRO_IF_AGREEMENT = os.environ.get("BRAND_SKIP_PRO_IF_AGREEMENT", "1") == "1"
ENABLE_GROUNDED_VALUATION = os.environ.get("ENABLE_GROUNDED_VALUATION", "0") == "1"
TRIAGE_IMAGE_MAX_EDGE = max(256, int(os.environ.get("TRIAGE_IMAGE_MAX_EDGE", "2048")))

CONFIDENCE_SCORE = {"high": 3, "medium": 2, "low": 1}
QUALITY_SCORE = {"sharp": 3, "ok": 2, "blur": 1}
LEGIBILITY_SCORE = {"clear": 3, "partial": 2, "none": 1}


class PipelineCancelled(Exception):
    """Raised when a single-lot pipeline job is cancelled mid-stage."""

_BRAND_PLACEHOLDERS = frozenset(
    {"unknown", "generic", "unbranded", "n/a", "none", "unidentified", "no brand"}
)

# Bottom foil / maker stickers (music boxes, figurines, collectibles) — prioritize for Pro pool.
_MAKER_STICKER_SIGNAL_RE = re.compile(
    r"(?i)(?:"
    r"22\s*kt\s*gold|"
    r"hand[- ]?sculpted\s+glass|"
    r"\btune\s*:\s*|"
    r"hand[- ]?sculpted|"
    r"made\s+in\s+(?:china|usa|taiwan|japan|korea|italy)|"
    r"\b(?:ug|wg)\b"
    r")"
)

# Engraved/stamped marks, firearms tags, handwritten inventory notes — also prioritize for Pro.
_MAKER_MARK_SIGNAL_RE = re.compile(
    r"(?i)(?:"
    r"\bpedersoli\b|\buberti\b|\btaylor'?s?\b|\barmi\s+san\s+paolo\b|"
    r"\bbrown\s+bess\b|\btower\b|\block\s*plate\b|"
    r"\b(?:cal\.?|caliber)\s*[\d./]+|"
    r"\b(?:flintlock|musket|rifle|pistol|revolver|shotgun|firearm)\b|"
    r"\b(?:engraved|stamped|etched|embossed)\b|"
    r"\b(?:serial|model|pat\.?|patent)\s*#?\s*[\w-]+|"
    r"\b(?:gr|grice)\s*\d{4}\b|\b(?:georgius|victoria)\s+rex\b|"
    r"\bmade\s+in\s+(?:italy|usa|germany|japan|china)\b"
    r")"
)

_HANDWRITTEN_TAG_SIGNAL_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:mine|estate|consign|inventory)\b|"
    r"#[\s]*\d{3,}|"
    r"\b(?:with|includes?)\s+(?:sling|case|box|accessories)\b"
    r")"
)

_COMPLEX_LOT_MIN_PHOTOS = max(8, int(os.environ.get("PIPELINE_COMPLEX_LOT_MIN_PHOTOS", "10")))

_genai_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        api_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is required for pipeline calls.")
        _genai_client = genai.Client(api_key=api_key)
    return _genai_client


def _detect_mime_type(img_bytes: bytes) -> str:
    if img_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if img_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if img_bytes[:4] == b"RIFF" and img_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _maybe_downscale(img_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """Bound triage payload size without importing production image_preprocess."""
    try:
        from PIL import Image
    except ImportError:
        return img_bytes, mime_type

    try:
        with Image.open(io.BytesIO(img_bytes)) as im:
            im = im.convert("RGB")
            w, h = im.size
            edge = max(w, h)
            if edge <= TRIAGE_IMAGE_MAX_EDGE:
                return img_bytes, mime_type
            scale = TRIAGE_IMAGE_MAX_EDGE / edge
            nw = max(1, int(w * scale))
            nh = max(1, int(h * scale))
            im = im.resize((nw, nh), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=95, optimize=True)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        return img_bytes, mime_type


def _normalize_brand(text: str | None) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.strip().split())
    if not cleaned or cleaned.lower() in _BRAND_PLACEHOLDERS:
        return ""
    return cleaned


def _image_part(img_bytes: bytes, mime_type: str) -> types.Part:
    if hasattr(types.Part, "from_bytes"):
        return types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
    return types.Part(inline_data=types.Blob(mime_type=mime_type, data=img_bytes))


TRIAGE_SYSTEM_INSTRUCTION = (
    "You are a professional estate sale photo classifier. "
    "Tag each image with its catalog role and verbatim label text. "
    "Output structured JSON only — no conversational text."
)

TRIAGE_USER_PROMPT = """Classify this estate-sale lot photo.

Rules:
- hero: single best wide shot showing most of the lot (only one file should win hero selection). Use hero even when lot tape with SKU is visible at the edge of the frame.
- item_front: primary face of each distinct sellable item; brand/tag when visible.
- angle_only: side, back, detail, damage close-up, or **ruler/measuring-tape scale shots** — not used for hero listing.
  Frames whose main purpose is showing size with a ruler or yellow tape should still be tagged angle_only (not unusable).
  Set **shows_measuring_scale** true whenever a yellow measuring tape, metal ruler, yardstick, or similar scale is visibly
  measuring the merchandise — even when photo_role is angle_only. When it is legible, copy the size into
  **measurement_text** verbatim with units (e.g. "12 in", "~30 cm", "18 x 24 in"); leave null if unreadable — never guess.
  Set **shows_weight_scale** true when the item sits on a weight scale (kitchen, postal, hanging, luggage, or bathroom
  scale) with a visible readout — even when photo_role is angle_only. When the readout is legible, copy it into
  **weight_text** verbatim with units (e.g. "2.4 lb", "540 g", "1.2 kg"); leave null if unreadable — never guess.
  A yellow **measuring** tape / ruler is a size scale (measurement_text), NOT estate lot tape (SKU); a **weight** scale
  display is weight_text, not a size.
  Tag/label close-ups whose main subject is a sewn-in tag or hang tag should use item_front (not angle_only).
  Bottom/underside **maker foil stickers** (oval foil with logo, "Hand-Sculpted Glass", "22 KT GOLD", tune name)
  are high-value label frames: use angle_only if needed but set label_legibility clear/partial and copy **all**
  readable sticker text verbatim into brand_text (maker mark, material, tune — not just a guessed brand name).
  **Engraved or stamped metal marks** (lock plates, barrels, undersides, silver/pewter hallmarks): use angle_only
  or item_front; set label_legibility clear/partial when any letters/numbers are readable; copy **all** engraved
  text verbatim into brand_text (e.g. PEDERSOLI, TOWER, GR, maker name + date).
  **Handwritten inventory tags** (paper/card in plastic, tied to item): use item_front when the tag fills much of
  the frame; copy every readable line into brand_text (model name, caliber, maker, serial).
- tape: dedicated frame where colored estate lot tape / SKU is the main subject (purple, lavender, blue, bright green, or yellow masking tape with handwriting). Prefer tape over hero when the strip fills much of the image.
  When **any** colored estate lot tape with a lot number is visible (even on a **tape roll** in the foreground of a hero shot), copy the marking into **lot_tape_marking** (e.g. MS530, MS 530, #8524).
- unusable: blank, extreme blur, or not catalog-worthy.
- Do NOT assign a new distinct_item_id for another angle of the same object.
- is_primary_front_for_item: True only for THE front shot, not side/back of the same object.
- Flag every image with any readable tag text (label_legibility clear or partial), even if corner of frame.
- brand_text: verbatim OCR from labels/tags/stickers/**engravings** — copy readable text exactly (maker logo letters,
  material lines like "22 KT GOLD", tune lines like "Tune: Amazing Grace", care/size when on same tag, lock-plate
  engravings like "PEDERSOLI MADE IN ITALY", handwritten tag lines like "Brown Bess Cal. 80"); empty string if
  not readable (never use Unknown/Generic/Unbranded).
- duplicate_of_filename: set when this image is a near-duplicate of another file in the lot.
- **Device screens are not the merchandise:** when the photographed item is a phone, tablet, monitor, or TV whose
  screen is showing a photo, gallery, video, or app, classify and describe the **device itself** (e.g. Apple iPhone,
  Samsung tablet). Never treat the on-screen picture content as the sellable item, and never copy on-screen photo
  content into brand_text unless it is the device's own UI text identifying the device.

The preceding image filename is: {filename}
"""


def triage_single_photo(filename: str, img_bytes: bytes) -> dict[str, Any]:
    """Stage 1: one light Flash call per file (order-independent)."""
    client = _get_client()
    payload_bytes, mime_type = _maybe_downscale(img_bytes, _detect_mime_type(img_bytes))

    parts: list[Any] = [
        _image_part(payload_bytes, mime_type),
        TRIAGE_USER_PROMPT.format(filename=filename),
    ]

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=pydantic_model_to_gemini_schema(PhotoTriageResult),
        system_instruction=TRIAGE_SYSTEM_INSTRUCTION,
        temperature=0.1,
    )

    from app.core.gemini_slots import gemini_slot

    with gemini_slot():
        response = client.models.generate_content(
            model=GEMINI_FLASH_MODEL,
            contents=parts,
            config=config,
        )

    if response.parsed is not None:
        row = PhotoTriageResult.model_validate(response.parsed)
    elif response.text:
        row = PhotoTriageResult.model_validate(json.loads(response.text))
    else:
        raise RuntimeError(f"Triage returned no content for {filename}")

    if not row.filename or row.filename != filename:
        row = row.model_copy(update={"filename": filename})
    return row.model_dump()


def _hero_score(row: PhotoTriageResult) -> tuple[int, int, int]:
    return (
        CONFIDENCE_SCORE.get(row.confidence, 0),
        QUALITY_SCORE.get(row.quality, 0),
        1 if row.shows_whole_lot else 0,
    )


def _front_score(row: PhotoTriageResult) -> tuple[int, int, int]:
    return (
        LEGIBILITY_SCORE.get(row.label_legibility, 0),
        QUALITY_SCORE.get(row.quality, 0),
        CONFIDENCE_SCORE.get(row.confidence, 0),
    )


def _dimension_score(row: PhotoTriageResult) -> tuple[int, int, int]:
    return (
        QUALITY_SCORE.get(row.quality, 0),
        CONFIDENCE_SCORE.get(row.confidence, 0),
        1 if row.shows_measuring_scale else 0,
    )


def _weight_score(row: PhotoTriageResult) -> tuple[int, int, int]:
    return (
        QUALITY_SCORE.get(row.quality, 0),
        CONFIDENCE_SCORE.get(row.confidence, 0),
        1 if row.shows_weight_scale else 0,
    )


def _row_has_maker_sticker_signal(row: PhotoTriageResult) -> bool:
    if row.photo_role == "tape":
        return False
    text = str(row.brand_text or "")
    return bool(_MAKER_STICKER_SIGNAL_RE.search(text))


def _row_has_maker_mark_signal(row: PhotoTriageResult) -> bool:
    """True when triage OCR suggests engravings, firearms tags, or handwritten inventory notes."""
    if row.photo_role == "tape":
        return False
    text = str(row.brand_text or "")
    if not text.strip():
        return False
    if _MAKER_STICKER_SIGNAL_RE.search(text) or _MAKER_MARK_SIGNAL_RE.search(text):
        return True
    if _HANDWRITTEN_TAG_SIGNAL_RE.search(text) and row.label_legibility in ("clear", "partial"):
        return True
    if row.label_legibility in ("clear", "partial") and len(text.strip()) >= 24:
        return True
    return False


def _pro_score(row: PhotoTriageResult) -> tuple[int, int, int, int, int]:
    ocr = (row.brand_text or "").strip()
    brand_len = len(_normalize_brand(ocr) or ocr)
    maker_boost = 1 if _row_has_maker_mark_signal(row) else 0
    return (
        maker_boost,
        LEGIBILITY_SCORE.get(row.label_legibility, 0),
        brand_len,
        QUALITY_SCORE.get(row.quality, 0),
        CONFIDENCE_SCORE.get(row.confidence, 0),
    )


def _best_maker_sticker_row(rows: list[PhotoTriageResult]) -> PhotoTriageResult | None:
    pool = [
        r
        for r in rows
        if r.label_legibility in ("clear", "partial") and _row_has_maker_mark_signal(r)
    ]
    if not pool:
        return None
    return max(pool, key=_pro_score)


def _best_handwritten_tag_row(rows: list[PhotoTriageResult]) -> PhotoTriageResult | None:
    pool = [
        r
        for r in rows
        if r.photo_role != "tape"
        and r.label_legibility in ("clear", "partial")
        and (
            _HANDWRITTEN_TAG_SIGNAL_RE.search(str(r.brand_text or ""))
            or (r.photo_role == "item_front" and len(str(r.brand_text or "").strip()) >= 12)
        )
    ]
    if not pool:
        return None
    return max(pool, key=_pro_score)


def _ensure_maker_sticker_in_pro_filenames(
    pro_filenames: list[str],
    rows: list[PhotoTriageResult],
) -> list[str]:
    """Reserve Pro slots for maker marks / foil stickers and handwritten inventory tags."""
    cap = BRAND_PRO_MAX_LABEL_FRAMES
    out = list(pro_filenames)
    row_by_fn = {r.filename: r for r in rows}
    must_include: list[PhotoTriageResult] = []
    for picker in (_best_maker_sticker_row, _best_handwritten_tag_row):
        best = picker(rows)
        if best is not None and best.filename not in {r.filename for r in must_include}:
            must_include.append(best)

    for best in must_include:
        if best.filename in out:
            continue
        out.append(best.filename)
        if len(out) <= cap:
            continue
        droppable = [
            fn
            for fn in out
            if fn not in {b.filename for b in must_include}
            and not _row_has_maker_mark_signal(row_by_fn[fn])
        ]
        while len(out) > cap and droppable:
            drop = min(droppable, key=lambda fn: _pro_score(row_by_fn[fn]))
            out.remove(drop)
            droppable.remove(drop)
    return out[:cap]


def _parse_triage_rows(triage_results: list[dict[str, Any]]) -> list[PhotoTriageResult]:
    rows: list[PhotoTriageResult] = []
    for raw in triage_results:
        rows.append(PhotoTriageResult.model_validate(raw))
    return rows


def select_analysis_photos(triage_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Section 4 — deterministic hero, fronts, pro pool from all triage rows."""
    rows = _parse_triage_rows(triage_results)

    # A. Hero (one file)
    hero_pool = [
        r
        for r in rows
        if r.photo_role == "hero" or (r.shows_whole_lot and r.quality != "blur")
    ]
    hero_filename = ""
    brand_needs_review = False

    if hero_pool:
        hero_filename = max(hero_pool, key=_hero_score).filename
    else:
        fallback_fronts = [r for r in rows if r.photo_role == "item_front" and r.shows_whole_lot]
        if fallback_fronts:
            hero_filename = max(fallback_fronts, key=_front_score).filename
        else:
            brand_needs_review = True

    # B. Item fronts — one best primary front per distinct_item_id
    front_pool = [
        r
        for r in rows
        if r.photo_role == "item_front" and r.is_primary_front_for_item and r.distinct_item_id
    ]
    fronts_by_item: dict[str, PhotoTriageResult] = {}
    for row in front_pool:
        item_id = row.distinct_item_id
        if not item_id:
            continue
        prev = fronts_by_item.get(item_id)
        if prev is None or _front_score(row) > _front_score(prev):
            fronts_by_item[item_id] = row

    ranked_fronts = sorted(fronts_by_item.values(), key=_front_score, reverse=True)
    front_filenames = [r.filename for r in ranked_fronts[:LISTING_FLASH_MAX_FRAMES]]

    dimension_pool = [
        r
        for r in rows
        if r.shows_measuring_scale and r.photo_role != "unusable"
    ]
    dimension_pool.sort(key=_dimension_score, reverse=True)
    dimension_filenames = [r.filename for r in dimension_pool[:DIMENSION_FLASH_MAX_FRAMES]]

    # Weight-scale frames get the same treatment as ruler/measuring-tape frames so a scale readout
    # is always fed to the listing/Pro authoring calls (otherwise weight was captured only by luck).
    weight_pool = [
        r
        for r in rows
        if r.shows_weight_scale and r.photo_role != "unusable"
    ]
    weight_pool.sort(key=_weight_score, reverse=True)
    weight_filenames = [r.filename for r in weight_pool[:DIMENSION_FLASH_MAX_FRAMES]]

    listing_filenames: list[str] = []
    if hero_filename:
        listing_filenames.append(hero_filename)
    for fn in dimension_filenames:
        if fn not in listing_filenames:
            listing_filenames.append(fn)
    for fn in weight_filenames:
        if fn not in listing_filenames:
            listing_filenames.append(fn)
    for fn in front_filenames:
        if fn not in listing_filenames:
            listing_filenames.append(fn)

    # C. Brand / Pro pool (2–4 label images; include legible tag close-ups even when angle_only)
    pro_pool: list[PhotoTriageResult] = []
    for r in rows:
        if r.photo_role == "unusable":
            continue
        if r.label_legibility not in ("clear", "partial"):
            continue
        if r.photo_role in ("item_front", "tape"):
            pro_pool.append(r)
        elif r.photo_role == "angle_only":
            # Tag/label macro shots are often classified angle_only — still send to Pro.
            pro_pool.append(r)

    pro_pool.sort(key=_pro_score, reverse=True)
    pro_candidates = pro_pool[:BRAND_PRO_MAX_LABEL_FRAMES]

    pro_filenames: list[str] = []
    if pro_candidates:
        pro_filenames = [pro_candidates[0].filename]
        if len(pro_candidates) >= 2:
            pro_filenames.append(pro_candidates[1].filename)

        brands_top2 = [_normalize_brand(r.brand_text) for r in pro_candidates[:2]]
        brands_top2 = [b for b in brands_top2 if b]
        disagree = len(set(brands_top2)) > 1
        low_confidence = any(r.confidence == "low" for r in pro_candidates[:2])
        if disagree or low_confidence or len(brands_top2) < 1:
            pro_filenames = [r.filename for r in pro_candidates[:BRAND_PRO_MAX_LABEL_FRAMES]]

    pro_filenames = _ensure_maker_sticker_in_pro_filenames(pro_filenames, rows)
    for fn in dimension_filenames:
        if fn and fn not in pro_filenames:
            pro_filenames.append(fn)
    for fn in weight_filenames:
        if fn and fn not in pro_filenames:
            pro_filenames.append(fn)
    maker_row = _best_maker_sticker_row(rows)

    # D. Catalog-only
    analysis_set = set(listing_filenames) | set(pro_filenames)
    catalog_only = [r.filename for r in rows if r.filename not in analysis_set]

    tape_filenames = [r.filename for r in rows if r.photo_role == "tape"]

    return {
        "hero_filename": hero_filename,
        "front_filenames": front_filenames,
        "listing_filenames": listing_filenames,
        "dimension_filenames": dimension_filenames,
        "weight_filenames": weight_filenames,
        "pro_filenames": pro_filenames,
        "catalog_only": catalog_only,
        "tape_filenames": tape_filenames,
        "brand_needs_review": brand_needs_review,
        "pro_candidates": [r.filename for r in pro_candidates],
        "maker_sticker_filename": maker_row.filename if maker_row else None,
        "hero_triage_metadata": next(
            (dict(r.model_dump()) for r in rows if r.filename == hero_filename),
            None,
        )
        if hero_filename
        else None,
    }


def _build_flash_draft(
    triage_results: list[dict[str, Any]],
    selected: dict[str, Any],
) -> InventoryLotDraft:
    """Merge triage rows into a lot-level Flash draft (Section 2 listing draft block)."""
    rows = _parse_triage_rows(triage_results)
    by_filename = {r.filename: r for r in rows}

    items: list[LowTierDraftItem] = []
    for fn in selected.get("front_filenames", []):
        row = by_filename.get(fn)
        if row is None:
            continue
        item_id = row.distinct_item_id or f"ITEM_{len(items) + 1}"
        brand = _normalize_brand(row.brand_text)
        title = brand if brand else f"Estate item ({item_id})"

        alternates: list[str] = []
        if row.distinct_item_id:
            for other in rows:
                if (
                    other.filename != fn
                    and other.distinct_item_id == row.distinct_item_id
                    and other.label_legibility in ("clear", "partial")
                ):
                    alternates.append(other.filename)

        items.append(
            LowTierDraftItem(
                temporary_id=item_id,
                suggested_title=title,
                raw_ocr_text_found=brand or None,
                condition_cues="",
                primary_front_filename=fn,
                alternate_label_filenames=alternates,
            )
        )

    categories = [r.photo_role for r in rows if r.photo_role not in ("unusable", "angle_only")]
    primary_category = categories[0] if categories else ""

    distinct_ids = {i.temporary_id for i in items}
    estimated_count = len(distinct_ids) if distinct_ids else len(items)

    return InventoryLotDraft(
        primary_category=primary_category,
        estimated_distinct_item_count=estimated_count,
        items=items,
        selected_hero_filename=selected.get("hero_filename") or "",
        selected_tape_filenames=list(selected.get("tape_filenames") or []),
        analysis_photo_filenames=list(selected.get("listing_filenames") or []),
        brand_needs_review=bool(selected.get("brand_needs_review")),
    )


def _brands_agree_on_pro_pool(
    pro_filenames: list[str],
    triage_results: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    """Section 4C — skip Pro when top label frames agree on normalized brand."""
    if len(pro_filenames) < 2:
        return False, []

    by_filename = {r["filename"]: r for r in triage_results if r.get("filename")}
    brands: list[str] = []
    for fn in pro_filenames[:2]:
        raw = by_filename.get(fn) or {}
        brand = _normalize_brand(raw.get("brand_text"))
        if brand:
            brands.append(brand)

    if len(brands) < 2:
        return False, brands

    normalized = {_normalize_brand(b) for b in brands}
    normalized.discard("")
    if len(normalized) == 1:
        return True, sorted(normalized)
    return False, brands


def _brand_audit_from_triage_agreement(
    brands: list[str],
    source_filenames: list[str],
    flash_draft: InventoryLotDraft,
) -> BrandAuditResult:
    return BrandAuditResult(
        brands=brands,
        brand_needs_review=flash_draft.brand_needs_review and not brands,
        was_corrected_by_pro=False,
        correction_details=None,
        source_filenames=source_filenames,
    )


def _brand_audit_from_flash_draft(flash_draft: InventoryLotDraft) -> BrandAuditResult:
    brands: list[str] = []
    seen: set[str] = set()
    for item in flash_draft.items:
        brand = _normalize_brand(item.raw_ocr_text_found)
        key = brand.lower()
        if brand and key not in seen:
            seen.add(key)
            brands.append(brand)

    return BrandAuditResult(
        brands=brands,
        brand_needs_review=flash_draft.brand_needs_review or not brands,
        was_corrected_by_pro=False,
        correction_details="Pro audit skipped or unavailable; using Flash triage draft.",
        source_filenames=list(flash_draft.analysis_photo_filenames),
    )


BRAND_AUDIT_SYSTEM = (
    "You are a senior estate inventory specialist writing CTBids auction listings. "
    "Use the photos and triage OCR transcripts to produce accurate shopper-facing title, description, brands, "
    "specification lines, and **ct_category** (CT Seller posting category). "
    "Fix category errors (apparel vs tableware vs firearms vs electronics). "
    "Prefer specific CT categories over Household Goods Other — e.g. gaming mice/keyboards/headsets, TVs, tablets, "
    "and phones are Electronics. "
    "When rewriting final_professional_description, sound like a **warm CTBids specialist selling the piece** — "
    "not a stiff catalog ('This is a set of…', 'Each … features…'). **Shape:** one short **flair** opening clause, "
    "then **immediately restate the title hook** (material/handle/finish + object) in natural words — same hook as "
    "final_auction_title, woven in after the flair (em dash or comma). Put marks and exact sizes in "
    "final_specification_lines / **Dimensions:**. "
    "**Vary descriptive vocabulary** — do not lean on the same adjective across listings (especially **handsome**). "
    "Prefer specific visual details; rotate natural appeal words (classic, striking, solid, crisp, polished, "
    "display-ready, timeless, inviting) that fit the item type. "
    "When a yellow measuring tape, metal ruler, or yardstick is visibly measuring the item(s), ensure "
    "final_specification_lines include **Dimensions:** or **Measurement:** with legible reads (~ inches). "
    "Never invent brands or details not visible in the images or triage OCR. "
    "Do not use redundant **Pair of** when the object type is already plural (Sculptures, Knives, Figurines…). "
    "Leave confirmed_brand empty when no readable maker mark exists on any frame — do not guess. "
    "Never use placeholder tokens like Unknown, Generic, or Unbranded. Output structured JSON only."
)


def _ct_category_allowlist_text() -> str:
    from app.services.ctseller_categories import ct_categories_for_ai

    return ", ".join(ct_categories_for_ai())


def aggregate_triage_ocr(triage_results: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Deduped verbatim OCR from every triage row — fed to Pro even for catalog-only frames."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in triage_results:
        row = PhotoTriageResult.model_validate(raw)
        parts: list[str] = []
        if (row.brand_text or "").strip():
            parts.append(str(row.brand_text).strip())
        tape = (row.lot_tape_marking or "").strip()
        if tape:
            parts.append(f"lot tape: {tape}")
        text = " | ".join(parts).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "filename": row.filename,
                "photo_role": row.photo_role,
                "label_legibility": row.label_legibility,
                "text": text,
            }
        )
    return out


def is_complex_lot_for_pro_author(
    triage_results: list[dict[str, Any]],
    selected: dict[str, Any],
) -> bool:
    """
    Complex lots skip Flash listing (cost offset) and rely on Pro author + triage OCR.
    Heuristic: many photos, multiple markings, engravings/firearms signals, or brand review flag.
    """
    rows = _parse_triage_rows(triage_results)
    if len(rows) >= _COMPLEX_LOT_MIN_PHOTOS:
        return True
    if selected.get("brand_needs_review"):
        return True
    marking_rows = [
        r
        for r in rows
        if _row_has_maker_mark_signal(r)
        or (
            r.label_legibility in ("clear", "partial")
            and len(str(r.brand_text or "").strip()) > 20
        )
    ]
    if len(marking_rows) >= 2:
        return True
    if any(_row_has_maker_mark_signal(r) for r in rows):
        return True
    ocrs = {_normalize_brand(r.brand_text) for r in rows if _normalize_brand(r.brand_text)}
    if len(ocrs) >= 2:
        return True
    return False


def _hero_triage_block(
    triage_results: list[dict[str, Any]],
    hero_filename: str,
) -> dict[str, Any] | None:
    """Return the Stage 1 triage metadata row for the selected hero filename."""
    if not hero_filename:
        return None
    for row in triage_results:
        if row.get("filename") == hero_filename:
            return dict(row)
    return None


def _build_pro_image_payload(
    user_images_dict: dict[str, bytes],
    pro_filenames: list[str],
    *,
    hero_filename: str,
) -> dict[str, bytes]:
    """Pro multimodal bytes: hero first (when present), then label/front pool."""
    pro_image_order: list[str] = []
    if hero_filename and hero_filename in user_images_dict:
        pro_image_order.append(hero_filename)
    for fn in pro_filenames:
        if fn and fn not in pro_image_order:
            pro_image_order.append(fn)
    return {fn: user_images_dict[fn] for fn in pro_image_order if fn in user_images_dict}


def run_brand_audit_pro(
    item_specific_images_dict: dict[str, bytes],
    flash_item_draft_json: dict,
    *,
    hero_triage_metadata: dict[str, Any] | None = None,
    flash_listing_context: dict[str, Any] | None = None,
    triage_ocr_aggregate: list[dict[str, str]] | None = None,
    pro_author_mode: bool = True,
) -> str:
    """Stage 2: Pro writes (or proofreads) listing + brands using hero, labels, and triage OCR."""
    client = _get_client()

    valuation_block = ""
    if ENABLE_GROUNDED_VALUATION:
        valuation_block = (
            "\nWhen ENABLE_GROUNDED_VALUATION is active, use Google Search to research realistic "
            "estate-sale liquidation ranges. Populate suggested_liquidation_low/high and valuation_rationale."
        )
    else:
        valuation_block = (
            "\nDo NOT guess prices. Set suggested_liquidation_low and suggested_liquidation_high to 0 "
            "and leave valuation_rationale empty unless grounded search finds strong comparables."
        )

    hero_filename = str((hero_triage_metadata or {}).get("filename") or "").strip()
    ocr_block = ""
    if triage_ocr_aggregate:
        ocr_block = (
            "\n\nTriage OCR transcript (verbatim text Flash read from **every** photo in this lot — "
            "including frames not attached as images below). Treat as primary evidence for maker, model, "
            "caliber, and serial when legibility is clear/partial:\n"
            f"{json.dumps(triage_ocr_aggregate, indent=2)}\n"
        )

    listing_block = ""
    if flash_listing_context and not pro_author_mode:
        listing_block = (
            "\n\nFlash listing draft to PROOFREAD and correct (fix title/description if wrong vs images):\n"
            f"{json.dumps(flash_listing_context, indent=2)}\n"
            "Populate final_auction_title and final_professional_description with your corrected lot-level text. "
            "Rewrite stiff Flash prose into warm seller voice (no 'This is a…' / 'Each … features…'). "
            "If images show a ruler or measuring tape, put exact reads in final_specification_lines and mention "
            "size casually in final_professional_description if needed. "
            "Set listing_was_corrected_by_pro true when you change title or description meaningfully."
        )
    elif flash_listing_context and pro_author_mode:
        listing_block = (
            "\n\nOptional Flash listing seed (may be incomplete — prefer photos + triage OCR when they disagree):\n"
            f"{json.dumps(flash_listing_context, indent=2)}\n"
        )

    if pro_author_mode:
        task_block = (
            "Write the CTBids auction listing for this estate lot using the hero, marking/label images, "
            "triage OCR transcript, and JSON context below.\n"
            "Tasks:\n"
            "1. Use the hero wide shot for lot context (every distinct sellable piece visible).\n"
            "2. Transcribe and **use** visible tags, engravings, and lock-plate marks on label/detail frames.\n"
            "3. **Author** final_auction_title and final_professional_description — accurate shopper-facing search text. "
            "**Hook-first titles:** put the distinctive material, handle, finish, or feature **before** the object name — "
            "not trailing 'with …'. Good: 'Deer Hoof Handled Hunting Knives'. "
            "Bad: 'Pair of Hunting Knives with Deer Hoof Handles'. "
            "Do **not** lead with **Pair of** when the object noun is already plural (Sculptures, Knives, Figurines…) — "
            "plural forms already imply quantity.\n"
            "Do not rely on a vague object guess when triage OCR or tags name the maker/model.\n"
            "4. Emit one verified_inventory row per distinct sellable piece with final_title and confirmed_brand.\n"
            "5. **Manufacturer vs model:** put the maker in confirmed_brand (e.g. Pedersoli, Levi's) and the model/pattern "
            "in final_auction_title or final_specification_lines (e.g. Brown Bess, Model: …).\n"
            "6. **Firearms / militaria / engraved antiques:** when lock plates, tags, or OCR show maker + model + caliber, "
            "lead final_auction_title with maker + model + caliber when readable. Historical marks (TOWER, GR crown) on "
            "reproductions go in specification lines; prefer the actual manufacturer name from engravings/tags for brand.\n"
            "7. **Handwritten inventory tags** (paper in plastic, tied to item): treat as authoritative for model/serial when "
            "legible — they override a generic hero guess.\n"
            "8. Leave confirmed_brand empty only when no readable maker mark exists on any frame or OCR line — "
            "never guess from object type alone.\n"
            "9. When one primary brand is clearly readable, lead final_auction_title with it for search.\n"
            "10. When maker foil stickers show material lines (e.g. 22 KT GOLD) or tune names, include exact phrases — "
            "do not downgrade to vague 'gold-tone' when text is legible.\n"
            "11. Do not add 'vintage' or 'antique' as filler unless a specific decade or era mark is visible.\n"
            "12. Do not use the word 'lot' in final_auction_title or final_professional_description.\n"
            "13. **Vary descriptive words** — avoid repeating **handsome** (and similar default compliments). "
            "Use at most one generic praise word; prefer concrete visual details. Rotate: classic, striking, solid, "
            "crisp, polished, display-ready, timeless, inviting, sharp-looking.\n"
            "14. **Description flair + hook:** final_professional_description = one paragraph. Start with a short warm "
            "**flair** clause (appeal, display, who would love it), then **right after** restate the title hook "
            "(material/handle/finish + object) in natural words — e.g. flair, then 'deer hoof handled hunting knives'. "
            "Use the **same Title Case** as final_auction_title for the hook phrase (not all lowercase). "
            "Do not paste the full title verbatim; weave the same hook in after the opening flair.\n"
            "15. **Ruler / measuring tape:** when a ruler, yellow measuring tape, or yardstick is measuring the item, "
            "**read the number** and put a **Dimensions:** / **Measurement:** line (with units, e.g. '~12 in') in "
            "final_specification_lines. **Weight scale:** when the item sits on a scale with a visible readout, "
            "**read it** and add a **Weight:** line (e.g. 'Weight: 2.4 lb'). Do not omit these when a scale is visible.\n"
            "15b. **Handwritten / printed estate tags are NOT the title.** When a tag or price card shows form fields "
            "(e.g. No., STYLE, SIZE, PRICE, COLOR, QTY), use the *values* as facts in final_specification_lines — "
            "**never** copy the field labels or raw tag OCR into final_auction_title. Always compose a clean, "
            "human-readable title (object + material + brand when known). Never output a title that is a string of "
            "label words like 'No. STYLE SIZE PRICE ...', and never mix unrelated item types (e.g. plates and apparel) "
            "into one title — if the frames show unrelated items, title the dominant sellable item.\n"
            "16. Set listing_was_corrected_by_pro true when you write or materially change title or description.\n"
            "17. **Device screens are not the merchandise:** when the item is a phone, tablet, monitor, or TV whose "
            "screen shows a photo, gallery, or app, write the listing for the **device itself** — never for the "
            "on-screen picture content.\n"
            "18. Set **ct_category** to the best CT Seller category for this lot "
            f"(must be one of: {_ct_category_allowlist_text()}). "
            "Prefer Electronics for computer peripherals and consumer tech; never default to Household Goods Other "
            "when a clearer category fits.\n"
        )
    else:
        task_block = (
            "Audit and proofread this estate lot using the hero, label/front images, and JSON context below.\n"
            "Tasks:\n"
            "1. Use the hero wide shot for lot context (every distinct sellable piece visible).\n"
            "2. Transcribe visible tags exactly on label/front frames.\n"
            "3. PROOFREAD the Flash listing title and description — fix wrong category, wrong material, or missing brands.\n"
            "4. Emit one verified_inventory row per distinct sellable piece with final_title and confirmed_brand.\n"
            "5. When label/tag images show a maker name, set confirmed_brand on that item.\n"
            "6. Leave confirmed_brand empty when no readable maker mark exists — never guess.\n"
            "7. When one primary brand is clearly readable on a tag, lead final_auction_title with it for search.\n"
            "8. Do not use the word 'lot' in final_auction_title or final_professional_description.\n"
            "9. **Ruler / measuring tape:** read the number and put a **Dimensions:** line (with units) in "
            "final_specification_lines when visible. **Weight scale:** when the item is on a scale with a visible "
            "readout, read it and add a **Weight:** line (e.g. 'Weight: 2.4 lb').\n"
            "10. Set **ct_category** to the best CT Seller category "
            f"(must be one of: {_ct_category_allowlist_text()}). "
            "Fix Household Goods Other when Electronics, Kitchen & Home, Books, etc. fit better.\n"
        )

    parts: list[Any] = [
        task_block
        + f"{valuation_block}"
        + f"{ocr_block}"
        + f"{listing_block}\n\n"
        + f"Flash triage draft JSON:\n{json.dumps(flash_item_draft_json, indent=2)}",
    ]

    for filename, img_bytes in item_specific_images_dict.items():
        mime_type = _detect_mime_type(img_bytes)
        if hero_filename and filename == hero_filename and hero_triage_metadata:
            parts.append(
                "Stage 1 hero triage metadata (selected wide-shot context for this lot):\n"
                f"{json.dumps(hero_triage_metadata, indent=2)}"
            )
        parts.append(_image_part(img_bytes, mime_type))
        role_note = " (hero shot — whole-lot context)" if hero_filename and filename == hero_filename else ""
        parts.append(f"The preceding image filename is: {filename}{role_note}")

    config_kwargs: dict[str, Any] = {
        "response_mime_type": "application/json",
        "response_schema": fully_audited_manifest_gemini_schema(),
        "system_instruction": BRAND_AUDIT_SYSTEM,
        "temperature": 0.1,
    }
    # Google Search + structured JSON schema returns 400 on 2.5 Pro — only enable for valuation.
    if ENABLE_GROUNDED_VALUATION:
        config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    config = types.GenerateContentConfig(**config_kwargs)

    from app.core.gemini_slots import gemini_slot

    with gemini_slot():
        response = client.models.generate_content(
            model=GEMINI_PRO_MODEL,
            contents=parts,
            config=config,
        )

    if response.text:
        manifest = FullyAuditedManifest.model_validate(json.loads(response.text))
        return manifest.model_dump_json()
    if response.parsed is not None:
        manifest = FullyAuditedManifest.model_validate(response.parsed)
        return manifest.model_dump_json()

    raise RuntimeError("Brand audit Pro call returned no content.")


def _manifest_to_brand_audit(
    manifest_json: str,
    source_filenames: list[str],
    flash_draft: InventoryLotDraft,
) -> BrandAuditResult:
    """Derive MVP BrandAuditResult from FullyAuditedManifest Pro output."""
    try:
        manifest = FullyAuditedManifest.model_validate(json.loads(manifest_json))
    except Exception:
        return _brand_audit_from_flash_draft(flash_draft)

    brands: list[str] = []
    seen: set[str] = set()
    corrected = False
    corrections: list[str] = []

    flash_brands = {
        _normalize_brand(i.raw_ocr_text_found).lower()
        for i in flash_draft.items
        if _normalize_brand(i.raw_ocr_text_found)
    }

    for item in manifest.verified_inventory:
        brand = _normalize_brand(item.confirmed_brand)
        if not brand:
            continue
        key = brand.lower()
        if key not in seen:
            seen.add(key)
            brands.append(brand)
        if item.was_corrected_by_pro:
            corrected = True
        if item.correction_details:
            corrections.append(item.correction_details.strip())

    if manifest.listing_was_corrected_by_pro:
        corrected = True
    if manifest.listing_correction_details:
        corrections.append(manifest.listing_correction_details.strip())

    if flash_brands and brands:
        flash_set = {b.lower() for b in flash_brands if b}
        pro_set = {b.lower() for b in brands}
        if flash_set != pro_set:
            corrected = True

    if not brands:
        return _brand_audit_from_flash_draft(flash_draft)

    return BrandAuditResult(
        brands=brands,
        brand_needs_review=flash_draft.brand_needs_review and not brands,
        was_corrected_by_pro=corrected,
        correction_details="; ".join(corrections) if corrections else None,
        source_filenames=source_filenames,
    )


def _run_parallel_triage(
    user_images_dict: dict[str, bytes],
    *,
    on_triage_complete: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """Stage 1 — parallel per-photo triage (order-independent)."""
    triage: list[dict[str, Any]] = []
    max_workers = min(8, max(1, len(user_images_dict)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(triage_single_photo, filename, img_bytes): filename
            for filename, img_bytes in user_images_dict.items()
        }
        for future in as_completed(futures):
            if should_cancel and should_cancel():
                for pending in futures:
                    pending.cancel()
                raise PipelineCancelled()
            filename = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = PhotoTriageResult(
                    filename=filename,
                    photo_role="unusable",
                    label_legibility="none",
                    quality="blur",
                    confidence="low",
                ).model_dump()
                _ = exc
            triage.append(row)
            if on_triage_complete:
                on_triage_complete(row)
    return triage


def triage_and_select_field_lot(
    user_images_dict: dict[str, bytes],
    *,
    on_triage_complete: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Stage 1 only: triage every photo + deterministic selection (no Pro, no listing Flash)."""
    if not user_images_dict:
        return {
            "triage": [],
            "selected": select_analysis_photos([]),
            "flash_draft": InventoryLotDraft().model_dump(),
            "catalog_only_count": 0,
        }

    triage = _run_parallel_triage(
        user_images_dict,
        on_triage_complete=on_triage_complete,
        should_cancel=should_cancel,
    )
    if should_cancel and should_cancel():
        raise PipelineCancelled()
    selected = select_analysis_photos(triage)
    flash_draft = _build_flash_draft(triage, selected)
    return {
        "triage": triage,
        "selected": selected,
        "flash_draft": flash_draft.model_dump(),
        "catalog_only_count": len(selected.get("catalog_only") or []),
    }


def process_incoming_field_lot(user_images_dict: dict[str, bytes]) -> dict[str, Any]:
    """Core orchestration: Stage 1 triage + selection + Stage 2 brand audit (Phase 0 / standalone)."""
    if not user_images_dict:
        return {
            "triage": [],
            "selected": select_analysis_photos([]),
            "flash_draft": InventoryLotDraft().model_dump(),
            "brand_audit_json": FullyAuditedManifest().model_dump_json(),
            "brand_audit": BrandAuditResult(brand_needs_review=True).model_dump(),
            "catalog_only_count": 0,
            "pro_skipped_due_to_agreement": False,
        }

    core = triage_and_select_field_lot(user_images_dict)
    triage = core["triage"]
    selected = core["selected"]
    flash_draft_json = core["flash_draft"]
    flash_draft = InventoryLotDraft.model_validate(flash_draft_json)
    pro_filenames = list(selected.get("pro_filenames") or [])
    hero_filename = str(selected.get("hero_filename") or "").strip()
    hero_triage = _hero_triage_block(triage, hero_filename)
    pro_skipped = False
    brand_audit_json = ""
    brand_audit: BrandAuditResult

    agreed, agreed_brands = _brands_agree_on_pro_pool(pro_filenames, triage)
    if BRAND_SKIP_PRO_IF_AGREEMENT and agreed and agreed_brands:
        pro_skipped = True
        brand_audit = _brand_audit_from_triage_agreement(agreed_brands, pro_filenames, flash_draft)
        brand_audit_json = FullyAuditedManifest(
            total_items_processed=len(flash_draft.items),
            verified_inventory=[
                VerifiedAndPricedItem(
                    line_item_id=idx + 1,
                    final_title=item.suggested_title,
                    confirmed_brand=_normalize_brand(item.raw_ocr_text_found),
                    accurate_description=item.suggested_title,
                    detected_condition_issues=item.condition_cues,
                    was_corrected_by_pro=False,
                )
                for idx, item in enumerate(flash_draft.items)
            ],
        ).model_dump_json()
    elif pro_filenames or (hero_filename and hero_filename in user_images_dict):
        pro_images = _build_pro_image_payload(
            user_images_dict,
            pro_filenames,
            hero_filename=hero_filename,
        )
        try:
            brand_audit_json = run_brand_audit_pro(
                pro_images,
                flash_draft_json,
                hero_triage_metadata=hero_triage,
            )
            brand_audit = _manifest_to_brand_audit(
                brand_audit_json, list(pro_images.keys()), flash_draft
            )
        except Exception:
            # Section 4E — Pro error: keep Flash draft; no fake prices; no Unknown brand
            brand_audit = _brand_audit_from_flash_draft(flash_draft)
            brand_audit_json = FullyAuditedManifest(
                total_items_processed=len(flash_draft.items),
                verified_inventory=[
                    VerifiedAndPricedItem(
                        line_item_id=idx + 1,
                        final_title=item.suggested_title,
                        confirmed_brand=_normalize_brand(item.raw_ocr_text_found),
                        accurate_description=item.suggested_title,
                        detected_condition_issues=item.condition_cues,
                        was_corrected_by_pro=False,
                    )
                    for idx, item in enumerate(flash_draft.items)
                ],
            ).model_dump_json()
    else:
        # Section 4E — no label frames
        brand_audit = BrandAuditResult(
            brands=[],
            brand_needs_review=True,
            was_corrected_by_pro=False,
            correction_details=None,
            source_filenames=[],
        )
        brand_audit_json = FullyAuditedManifest(total_items_processed=len(flash_draft.items)).model_dump_json()

    return {
        "triage": triage,
        "selected": selected,
        "flash_draft": flash_draft_json,
        "brand_audit_json": brand_audit_json,
        "brand_audit": brand_audit.model_dump(),
        "catalog_only_count": core["catalog_only_count"],
        "pro_skipped_due_to_agreement": pro_skipped,
    }
