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
from app.core.lotting import tape_marking_canonical
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
            "Single-line auction title: era/style + confirmed maker (when known) + material/form + **only subjects "
            "clearly visible in the photos** — never add animals or object types you do not see (e.g. do not say "
            "'frog' on a bird figurine). Example: 'Art Deco Ditmar-Urbach Lusterware Bird Figurine'."
        ),
    )
    brand: str = Field(
        default="",
        description=(
            "Primary maker on the product or label, plain text. Leave empty when not legibly identified — "
            "never use Unknown, Unidentified, Unbranded, N/A, or similar placeholders."
        ),
    )
    material: str = ""
    condition_notes: str = ""
    era: str = ""
    sku: str | None = Field(
        default=None,
        validation_alias=AliasChoices("sku", "SKU"),
        description=(
            "Handwritten lot code on PURPLE or LAVENDER adhesive estate tape. Read sku from focused crop if provided. "
            "Never use stamped manufacturer numbers (e.g. 1420) or silverplate pattern numbers as SKU."
        ),
    )
    sku_read_confidence: str | None = Field(
        default=None,
        validation_alias=AliasChoices("sku_read_confidence", "SkuConfidence"),
    )
    professional_description: str = Field(
        default="",
        validation_alias=AliasChoices("professional_description", "ProfessionalDescription"),
        description=(
            "2–6 sentences of flowing prose. Name each confirmed maker in the text with **bold** at first mention. "
            "Omit unknown makers entirely. Describe only visible subjects and forms — do not invent animals or "
            "mislabel birds as flower frogs."
        ),
    )
    specification_lines: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("specification_lines", "SpecificationLines"),
        description=(
            "Bullet facts as 'Label: value'. Include dimension lines when a yellow measuring tape, ruler, or scale "
            "in the photo clearly shows a span (use approximate inches with ~, note orientation e.g. width vs height). "
            "Do not invent numbers without a visible scale."
        ),
    )
    labels_and_tags: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("labels_and_tags", "LabelsAndTags"),
    )
    starting_price_usd: float | None = Field(
        default=None,
        validation_alias=AliasChoices("starting_price_usd", "StartingPrice"),
    )
    lot_boundary_blank_page: bool = Field(
        default=False,
        validation_alias=AliasChoices("lot_boundary_blank_page", "lotBoundaryBlankPage"),
    )


class TapeSkuAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    new_lot_from_tape: bool = Field(default=False)
    lot_digits_on_tape: str | None = Field(default=None)


SYSTEM_INSTRUCTION = (
    "You are an AI assistant for Caring Transitions of Enterprise-Las Vegas, owned by Julie Zorn. "
    "Your goal is to analyze photos and generate professional manifest descriptions.\n\n"
    "### THE SINGLE-BRAND RULE:\n"
    "- Identify exactly ONE primary brand. No brand-soup or competing guesses.\n"
    "- If 'Czechoslovakia' is present, the brand is **Ditmar-Urbach**. Omit 'Royal Bonn'.\n\n"
    "### VISIBLE SUBJECT ONLY (CRITICAL — NO HALLUCINATED KEYWORDS):\n"
    "- In **item_name** and **professional_description**, describe only what is **clearly visible** in the image(s). "
    "Do not add extra animals, figures, or object-type words to sound like a catalog category.\n"
    "- **Flower frog** is a specific ceramic form (flower-arranging frog / pin holder). Use that term **only** if the "
    "piece is visibly that type. A **bird** on a branch with a small cup or well is **not** a frog — say bird figurine, "
    "spill vase, or posy-style form as fits; **never** append 'frog'.\n"
    "- If the exact ceramic term is uncertain, use neutral wording (e.g. 'ceramic bird figurine') rather than guessing.\n\n"
    "### AUCTION TITLE (item_name):\n"
    "- When the maker is confirmed, put the brand in the title as a keyword shoppers can search — same spelling as brand.\n"
    "- Typical shape: era or style + maker + material/type + **visible** subject or form only (compact; no lot SKU).\n"
    "- Example: 'Art Deco Ditmar-Urbach Lusterware Bird Figurine' (not '... Bird Flower Frog' unless a real flower frog is visible).\n\n"
    "### DESCRIPTION LOGIC:\n"
    "- Do **not** transcribe stamps, mold numbers, incised digits, or bottom-mark details in **specification_lines**.\n"
    "- Professional description: focus on appearance, style, and visible condition; avoid long stamp or number inventories.\n\n"
    "### DIMENSIONS FROM PHOTOS (specification_lines):\n"
    "- If a **yellow measuring tape**, metal ruler, or yardstick appears and the readout is legible, add one line per "
    "clear measurement to **specification_lines** (not the purple estate lot tape — that is SKU only).\n"
    "- Use 'Label: value' with units (prefer inches: ~10 in., ~4.5 in.). Prefix with ~ for estimates from the photo.\n"
    "- Name what was measured: e.g. 'Overall width (set): ~10 in. (tape along front)', "
    "'Height (tallest figurine): ~4.5 in. (tape vertical)', 'Height (center piece): ~3.5 in.'\n"
    "- For multi-piece lots, separate lines for overall span vs individual heights when the tape shows them.\n"
    "- If no scale is visible or numbers are unreadable, omit dimension lines — do not guess.\n\n"
    "### CERAMICS & POTTERY ACCURACY RULES (STRICT):\n"
    "- STRIKE RULE: If the item is marked 'Czechoslovakia', you are FORBIDDEN from using the brand 'Royal Bonn' or origin 'Germany'. These are mutually exclusive.\n"
    "- If 'CM-R' or 'CMHR' is present: This is C.M. HUTSCHENREUTHER ownership. NEVER identify as 'Carl-Moritz Reichenbach'.\n"
    "- 'Star' mark = Haas & Czjzek (Schlaggenwald).\n"
    "- 'Shield' mark = Utilitarian Schlaggenwald.\n"
    "- 'Red circular stamps' with 'Czechoslovakia' = DITMAR-URBACH. Omit all mentions of Royal Bonn or Mehlem.\n"
    "- 'MZ Altrohlau' = Moritz Zdekauer factory.\n"
    "- DECISIVENESS: Return exactly ONE primary brand. Do not provide a list of competing guesses (no Brand Soup).\n\n"
    "Identify brand, model, material, condition flaws, and era from the image. Use a tone that is helpful, clear, and professional. "
    "When two images are provided, image 1 is the full frame; image 2 is a high-contrast crop of purple/lavender tape — prioritize image 2 for SKU. "
    "SKU must be HANDWRITTEN on purple/lavender tape only. Never use sewn tags, size tags, or stamped numbers on the item body as SKU. "
    "Condition must use exactly one of: NEW, LIKE NEW, VERY GOOD, GOOD, FAIR. "
    "Output must match JSON schema only."
)


def _user_prompt(*, manifest_tag: str) -> str:
    tag_line = f"Manifest / batch tag: {manifest_tag.strip()}\n" if manifest_tag.strip() else ""
    return (
        tag_line
        + "Analyze this catalog photo for an estate inventory row.\n"
        + "Identify specific finishes: **Lusterware**, **Faux Bois (Wood Grain)**, or **Geometric Art Deco**.\n"
        + "RULES:\n"
        "- item_name: One-line auction title — confirmed maker as keyword when known; **only name subjects/forms you see** "
        "(no extra animals: e.g. bird only → do not add frog). Pattern: style/era + maker + material/form + visible subject. "
        "Example: Art Deco Ditmar-Urbach Lusterware Bird Figurine.\n"
        "- brand: Primary visible maker ONLY. If 'Czechoslovakia' is present, default to **Ditmar-Urbach** for circular stamps and omit 'Royal Bonn'.\n"
        "- professional_description: Flowing prose; **bold** confirmed makers at first mention. Omit guesses.\n"
        "- condition_notes: Choose exactly one value from NEW, LIKE NEW, VERY GOOD, GOOD, FAIR.\n"
        "  * NEW: no signs of wear; may include tags/packaging.\n"
        "  * LIKE NEW: clearly unused, but tags may be missing.\n"
        "  * VERY GOOD: minor signs of wear (light scuffs/scratches).\n"
        "  * GOOD: moderate signs of wear (moderate scuffs/scratches).\n"
        "  * FAIR: heavy wear and visible scratches/damage.\n"
        "- specification_lines: 'Label: value' — Material, Style, Finish, Era, Subject, **and dimensions from a visible "
        "yellow tape or ruler** (e.g. Overall width (set): ~10 in.; Height (tallest): ~4.5 in.). "
        "Never use purple/lavender **estate** tape numbers as dimensions. "
        "Never include a Markings line or stamp/mold-number transcription.\n"
        "- sku: Handwritten digits on purple/lavender tape ONLY. null if not on tape.\n"
    )


def _normalize_condition_label(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "GOOD"
    norm = re.sub(r"\s+", " ", text).strip().lower()

    if norm == "new":
        return "NEW"
    if norm in {"like new", "likenew"}:
        return "LIKE NEW"
    if norm in {"very good", "verygood"}:
        return "VERY GOOD"
    if norm == "good":
        return "GOOD"
    if norm == "fair":
        return "FAIR"

    if "new with tag" in norm or "new w/" in norm:
        return "NEW"
    if "like new" in norm or "unused" in norm:
        return "LIKE NEW"
    if "very good" in norm or "minor" in norm or "light scuff" in norm or "light scratch" in norm:
        return "VERY GOOD"
    if "fair" in norm or "heavy wear" in norm or "heavy scratch" in norm:
        return "FAIR"
    if "good" in norm or "moderate" in norm:
        return "GOOD"

    return "GOOD"


def inventory_table_entry_to_lot_payload(row: InventoryTableEntry) -> dict[str, Any]:
    sku_clean = tape_marking_canonical(row.sku)
    title = row.item_name.strip() or (f"Estate lot #{sku_clean}" if sku_clean else "Estate catalog item")
    specs = [
        s
        for s in row.specification_lines
        if s.strip() and not re.match(r"^\s*markings?\s*:", s.strip(), re.I)
    ]
    if row.material.strip() and not any(s.lower().startswith("material:") for s in specs):
        specs.append(f"Material: {row.material.strip()}")
    if row.era.strip() and not any(s.lower().startswith("era:") for s in specs):
        specs.append(f"Era: {row.era.strip()}")
    
    desc = row.professional_description.strip() or title
    return {
        "SKU": sku_clean or None,
        "AuctionTitle": title,
        "ProfessionalDescription": desc,
        "SpecificationLines": specs,
        "Brands": [row.brand.strip()] if row.brand.strip() else [],
        "LabelsAndTags": row.labels_and_tags,
        "Condition": _normalize_condition_label(row.condition_notes),
        "StartingPrice": row.starting_price_usd,
        "SkuConfidence": row.sku_read_confidence,
        "lot_boundary_blank_page": row.lot_boundary_blank_page,
    }


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
    
    focus_note = "\nA second image follows: high-contrast crop of purple tape. Read SKU from this crop." if focus_jpeg else ""
    contents = [
        _user_prompt(manifest_tag=manifest_tag) + focus_note,
        genai_types.Part(inline_data=genai_types.Blob(mime_type=mime_type, data=image_bytes)),
    ]
    if focus_jpeg:
        contents.append(genai_types.Part(inline_data=genai_types.Blob(mime_type="image/jpeg", data=focus_jpeg)))

    config = genai_types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=InventoryTableEntry,
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
    headers = {"Content-Type": "application/json", "x-goog-api-key": settings.google_api_key}
    for _ in range(3): # Shorter retry loop for responsiveness
        try:
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
    
    payload = {"contents": [{"parts": parts}], "generationConfig": {"response_mime_type": "application/json"}}
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
    from app.services.tape_focus import purple_tape_focus_jpeg_from_path
    focus_jpeg = None
    if settings.tape_focus_enabled:
        try: focus_jpeg = purple_tape_focus_jpeg_from_path(photo_path)
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
def verify_tape_sku(photo_path: Path, claimed_digits: str) -> bool | None:
    timeout = float(settings.tape_verify_total_timeout_seconds)
    fut = _get_tape_verify_pool().submit(_verify_tape_sku_inner, photo_path, claimed_digits)
    try: return fut.result(timeout=timeout)
    except Exception: return None

def _verify_tape_sku_inner(photo_path: Path, claimed_digits: str) -> bool | None:
    prompt = f"Audit: Is handwritten {claimed_digits} visible on PURPLE/LAVENDER tape? Return JSON {{'new_lot_from_tape': bool, 'lot_digits_on_tape': str}}."
    img_bytes, mime = prepare_gemini_image_file(photo_path, max_edge=1536, quality=85)
    payload = {
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime, "data": base64.b64encode(img_bytes).decode("utf-8")}}]}]
    }
    text = _generate_content(payload)
    if not text: return None
    try:
        res = json.loads(re.sub(r"```json|```", "", text).strip())
        return res.get("new_lot_from_tape") is True and str(res.get("lot_digits_on_tape", "")) == claimed_digits
    except Exception: return None