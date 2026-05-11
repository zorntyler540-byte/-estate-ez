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
from app.core.lotting import sku_digits, tape_marking_canonical
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
            "Single-line auction title: era/style + confirmed maker when one maker fits the whole visible group + "
            "material/form + **only subjects clearly visible**. For several distinct pieces in one shot, use a compound "
            "title that states quantity and type (e.g. 'Seven ceramic Disney character figurines') rather than naming "
            "only one figure. Never add animals or object types you do not see."
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
            "Handwritten or printed lot code on colored estate adhesive tape: PURPLE, LAVENDER, bright NEON GREEN, "
            "or bright YELLOW masking/painter tape (lot marker — not yellow measuring tape). Read sku from focused crop "
            "if provided. Never use stamped manufacturer numbers (e.g. 1420) or silverplate pattern numbers as SKU."
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
            "2–8 sentences when multiple distinct items appear; otherwise 2–6. Name each confirmed maker with **bold** "
            "at first mention only when legibly marked or tagged on that piece. Enumerate distinct visible pieces "
            "(count, placement left-to-right, character/type) so buyers see this is a multi-piece lot — do not merge "
            "several subjects into one vague noun. Omit unknown makers entirely."
        ),
    )
    specification_lines: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("specification_lines", "SpecificationLines"),
        description=(
            "Bullet facts as 'Label: value'. For photos with several distinct salable pieces (figurines in a row, "
            "multiple coins/medals, pairs, or a set), add separate lines per distinct item or subgroup — e.g. "
            "'Item A (left): …', 'Item B (center): …', 'Finish (item A): …', 'Visible mark (item B): …'. "
            "Include dimension lines when a yellow measuring tape or ruler clearly shows a span (~ inches). "
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
        description="Deprecated: server ignores blank-divider detection; always leave false.",
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
    "- 'Czechoslovakia' alone is country-of-origin text, not a maker. Do not default brand to Ditmar-Urbach from origin text alone.\n"
    "- Only list brands or names explicitly visible in the photos or on the hallmarks. Do not include 'related' brands.\n"
    "- If multiple brand names are identified, only list the one that appears most clearly on the physical hallmark. "
    "Do not list 'similar' or 'related' brands.\n"
    "- If no manufacturer mark is clearly visible or confirmed, do not include a brand rather than speculating.\n\n"
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
    "### METAL IDENTIFICATION (HALLMARK-FIRST):\n"
    "- Read hallmark/stamp text from the item bottom/underside when visible and use that as primary evidence.\n"
    "- If stamp shows '925', 'STERLING', or Lion Passant: identify as Sterling Silver.\n"
    "- If stamp shows 'EPNS', 'Silver Plate', or 'Plate': identify as Silver Plate.\n"
    "- If stamp shows 'Pewter' or 'Zinn': identify as Pewter.\n"
    "- If no hallmark is clearly visible or confirmable, do **not** call it Sterling; use 'silver-tone metal' or "
    "'pewter' only when surface/luster supports that call.\n"
    "- Luster clues: mirror-like often suggests plate; soft/dull often suggests pewter; warm-white can support sterling "
    "only with hallmark confirmation.\n"
    "- Condition clues: pitting can indicate pewter; base-metal bleed/wear-through can indicate plate.\n\n"
    "### MULTI-PIECE LOT COVERAGE (CRITICAL):\n"
    "- When a lot includes multiple distinct forms, include ALL major visible components in title/description/specs.\n"
    "- Do not collapse the lot to only one repeated subset (e.g., cups/tumblers) if larger or unique pieces are present.\n"
    "- Prioritize the dominant/structural pieces in wording: e.g., flagon/jug, serving tray/platter, then cups/goblets.\n"
    "- Add one concise components line in specification_lines, such as: "
    "'Components: 1 flagon/jug, 1 tray, 6 cups'. Use only clearly visible counts.\n\n"
    "### MULTI-DISTINCT ITEMS IN ONE FRAME (figurines, coins, jewelry rows):\n"
    "- If several separate salable pieces appear (e.g. a row of character figurines, multiple medals, pairs of earrings), "
    "you MUST NOT summarize as a single generic subject. Name or describe each distinct piece or position "
    "(left / center / right, or count by type).\n"
    "- **brand** field: use the ONE maker that applies to the whole lot only when every piece is clearly the same line; "
    "otherwise leave brand empty and put per-piece marks in **specification_lines** "
    "(e.g. 'Visible mark (3rd from left): …' only if legible).\n"
    "- Never invent a single brand to cover different sculpts, eras, or mismatched bases.\n\n"
    "### FINISHES AND SURFACES (DO NOT DEFAULT TO DECOR STYLES):\n"
    "- **Finish** means physical surface: glazed ceramic, satin metal, polished chrome, textured enamel, patinated brass, "
    "matte paint — describe what you see (sheen, texture, wear), not a style movement unless the whole piece clearly fits.\n"
    "- Use **Lusterware**, **Faux Bois**, or **Geometric Art Deco** only when that finish or decoration is **visibly** "
    "present (iridescent metallic glaze, wood-grain slip, strong stepped geometry). If unsure, say 'glazed ceramic' or "
    "'painted ceramic' instead of inventing a collectible finish name.\n"
    "- For metals: finish (polished / brushed / satin) is separate from whether it is sterling vs plate — follow hallmark evidence.\n\n"
    "### DIMENSIONS FROM PHOTOS (specification_lines):\n"
    "- If a **yellow measuring tape**, metal ruler, or yardstick appears and the readout is legible, add one line per "
    "clear measurement to **specification_lines** (not colored estate lot tape — that is SKU only).\n"
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
    "- Assign DITMAR-URBACH only when hallmark evidence supports it (e.g., known red circular Ditmar-style stamp with matching wording/mark structure), not from 'Czechoslovakia' text alone.\n"
    "- 'MZ Altrohlau' = Moritz Zdekauer factory.\n"
    "- DECISIVENESS: Return exactly ONE primary brand. Do not provide a list of competing guesses (no Brand Soup).\n\n"
    "Identify brand, model, material, condition flaws, and era from the image. Use a tone that is helpful, clear, and professional. "
    "When two images are provided, image 1 is the full frame; image 2 is a high-contrast crop of colored estate lot tape "
    "(purple, lavender, neon green, or yellow masking tape) — prioritize image 2 for SKU. "
    "SKU must appear on that estate tape only (not yellow measuring tape, not rulers). "
    "Never use sewn tags, size tags, or stamped numbers on the item body as SKU. "
    "Condition must use exactly one of: NEW, LIKE NEW, VERY GOOD, GOOD, FAIR. "
    "Output must match JSON schema only."
)


def _user_prompt(*, manifest_tag: str) -> str:
    tag_line = f"Manifest / batch tag: {manifest_tag.strip()}\n" if manifest_tag.strip() else ""
    return (
        tag_line
        + "Analyze this catalog photo for an estate inventory row.\n"
        + "If several distinct salable pieces are visible (row of figurines, multiple coins/medals, pairs, tableware set), "
        "describe EACH distinct piece or subgroup in specification_lines and professional_description — do not collapse "
        "several items into one vague subject line.\n"
        + "Surface / finish: describe visible sheen and material (e.g. glazed ceramic, polished vs satin metal). "
        "Only call out **Lusterware**, **Faux Bois**, or strong **Art Deco** styling when clearly visible; "
        "otherwise use neutral accurate terms — do not guess a decorative finish label.\n"
        + "RULES:\n"
        "- item_name: One-line auction title — confirmed maker as keyword when one maker clearly applies to the whole "
        "visible group; otherwise omit maker from title and list marks per piece in specs. **Only name subjects/forms you see**. "
        "For multi-piece lots, prefer a compound title that signals quantity/type (e.g. 'Seven ceramic Disney character figurines'). "
        "Pattern: style/era + maker (if unified) + material/form + visible subjects.\n"
        "- brand: ONE primary maker ONLY when that maker clearly applies to all pieces OR one dominant branded piece; "
        "otherwise leave empty and use specification_lines for legible marks per item.\n"
        "- Treat 'Czechoslovakia' / country text as origin unless a specific maker hallmark is visible.\n"
        "- If no manufacturer mark is clearly visible or confirmed, leave brand empty (do not guess).\n"
        "- For metal items, read and quote visible hallmark text in specs; metal type must follow hallmark-first rules.\n"
        "- professional_description: Cover each distinct visible piece or row where relevant; **bold** makers only when "
        "confirmed on that piece.\n"
        "- condition_notes: Choose exactly one value from NEW, LIKE NEW, VERY GOOD, GOOD, FAIR.\n"
        "  * NEW: no signs of wear; may include tags/packaging.\n"
        "  * LIKE NEW: clearly unused, but tags may be missing.\n"
        "  * VERY GOOD: minor signs of wear (light scuffs/scratches).\n"
        "  * GOOD: moderate signs of wear (moderate scuffs/scratches).\n"
        "  * FAIR: heavy wear and visible scratches/damage.\n"
        "- specification_lines: 'Label: value' — Material, Style, **Finish** (surface sheen/texture), Era, **and dimensions** "
        "from a visible yellow tape or ruler. "
        "Never use colored **estate** tape numbers as dimensions. "
        "For multi-piece lots: include 'Components: …' AND separate lines per distinct item or position when pieces differ "
        "(e.g. 'Figurine 1 (left): Mickey…', 'Figurine 2: Pluto…'). "
        "Use multiple Subject/Form lines when multiple distinct subjects appear — do NOT reduce to a single Subject when "
        "several different items are clearly visible. "
        "Never include a Markings line or stamp/mold-number transcription.\n"
        "- sku: Lot marking on purple, lavender, bright green, or bright yellow estate masking tape ONLY "
        "(same code you would read from image 2 when provided). null if not on tape.\n"
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


def _infer_metal_material(*, material: str, specs: list[str], description: str) -> str | None:
    blob = " ".join([material, description, *specs]).lower()
    has_pewter = any(k in blob for k in ("pewter", "zinn", "etain"))
    has_plate = any(k in blob for k in ("silver plate", "silverplate", "epns"))
    has_sterling = any(k in blob for k in (" sterling", "sterling ", " 925", "925 "))

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
    title = row.item_name.strip() or (f"Estate lot #{sku_clean}" if sku_clean else "Estate catalog item")
    specs = [
        s
        for s in row.specification_lines
        if s.strip() and not re.match(r"^\s*markings?\s*:", s.strip(), re.I)
    ]
    metal_material = _infer_metal_material(
        material=row.material.strip(),
        specs=specs,
        description=row.professional_description.strip(),
    )
    material_value = metal_material or row.material.strip() or None
    specs = _upsert_material_spec(specs, material_value)
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
        "Tape may be PURPLE, LAVENDER, bright NEON GREEN, or bright YELLOW adhesive masking/painter tape "
        "with handwriting or print on its surface. "
        "Set new_lot_from_tape false if the marking is only on a yellow MEASURING tape (inch/cm scale), ruler, "
        "sticker, tag, or item body. "
        "Return JSON {\"new_lot_from_tape\": bool, \"lot_digits_on_tape\": string}. "
        "lot_digits_on_tape: exact marking read from the tape (letters+digits as seen), or null if none/unreadable."
    )
    prompt = prompt + _tape_verify_prompt_extra()
    img_bytes, mime = prepare_gemini_image_file(photo_path, max_edge=1536, quality=85)
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