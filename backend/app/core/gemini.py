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
    item_name: str = ""
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
            "Handwritten lot code on PURPLE or LAVENDER adhesive estate tape (when a second crop image is "
            "provided, prefer reading sku from that crop). Codes may be digits-only (e.g. 015) or letters+digits "
            "on one line (e.g. SR006, SA004, SA 003) — return that main line as written (spaces optional); "
            "ignore a small stray digit drawn above/beside the main code if it is clearly not part of the lot id. "
            "If the tape shows #, return digits after # unless the visible code is clearly letter+digit style. "
            "Do not merge unrelated numbers. null if unreadable or not on qualifying tape. "
            "Never use stamped or engraved pattern / catalog numbers on the underside of silverplate or Sheffield "
            "reproduction holloware (e.g. spaced 1 4 2 0 or 1420 near SHEFFIELD REPRODUCTION) as sku — those are "
            "maker marks, not estate tape."
        ),
    )
    sku_read_confidence: str | None = Field(
        default=None,
        validation_alias=AliasChoices("sku_read_confidence", "SkuConfidence"),
        description=(
            "One of: high, medium, low, unsure — how confident you are in the sku read from tape. "
            "Use low or unsure when the crop is blurry, oblique, or digits ambiguous."
        ),
    )
    professional_description: str = Field(
        default="",
        validation_alias=AliasChoices("professional_description", "ProfessionalDescription"),
        description=(
            "2–6 sentences of flowing prose. Name each confirmed maker in the text with **bold** at first mention. "
            "Never write ``Noted brand:`` / ``Noted brands:`` lines or list unknown makers — omit unknowns entirely. "
            "If a maker is confirmed but awkward in prose, leave it out here and put ``Brand: Name`` in specification_lines only."
        ),
    )
    specification_lines: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("specification_lines", "SpecificationLines"),
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
        description=(
            "True when the frame is mainly a blank white or off-white paper/card used as a studio divider BETWEEN "
            "lots (no sellable product, no purple tape). False for merchandise, tape-only markers, porcelain, "
            "labels, or bright glare on an object."
        ),
    )


class TapeSkuAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    new_lot_from_tape: bool = Field(
        default=False,
        description=(
            "True only if HANDWRITTEN estate lot digits are visible on PURPLE/LAVENDER ADHESIVE TAPE (strip or roll) "
            "separate from the product body — not on paper-only markers, tags, or the item itself. "
            "JSON key name is historical."
        ),
    )
    lot_digits_on_tape: str | None = Field(
        default=None,
        description=(
            "Digits only from handwritten lot number on purple/lavender tape; null if not present or not on tape."
        ),
    )


SYSTEM_INSTRUCTION = (
    "You are an AI assistant for Caring Transitions of Enterprise-Las Vegas, owned by Julie Zorn. "
    "Your goal is to analyze photos and generate professional manifest descriptions for our online auction platform. "
    "Use a tone that is helpful, clear, and follows our franchise standards. "
    "Focus on item accuracy to ensure our clients get the best value for their estate transitions. "
    "As a professional estate liquidation specialist, "
    "identify brand, model, material, condition flaws, and era from the image — including partial views, "
    "rear stickers, power bricks, and cable shots when they belong to the same catalog item. "
    "When two images are provided, image 1 is the full catalog frame; image 2 is a high-contrast crop of "
    "purple/lavender estate tape — use image 2 first to read sku. "
    "For sku ONLY: HANDWRITTEN lot codes on PURPLE or LAVENDER adhesive tape near the rack or item — either "
    "digits-only (e.g. 015) or letter+digit tape marks (e.g. SR006, SA004, SA 003). Ignore tiny stray digits "
    "scribbled above the main code if they are not part of that line. "
    "Never use sewn-in garment tags, size tags, RN/style codes, care-label numbers, years, or ZIP codes as sku. "
    "Never use stamped underside pattern numbers on silverplate (e.g. 1420 next to SHEFFIELD REPRODUCTION HAND CHASED) "
    "as sku — only handwritten codes on purple/lavender adhesive tape. "
    "If a # is visible on the tape, prefer digits after # unless the main readable code is clearly letter+digit. "
    "Never merge unrelated digit strings (e.g. 015 stays three characters, not five). "
    "Set sku_read_confidence to low or unsure when the tape read is not crisp. "
    "Use condition_notes for numbers not on purple/lavender tape. "
    "After a valid tape sku, later angles of the same item usually have sku null until new tape digits appear. "
    "Photographers sometimes insert a blank white or off-white paper sheet between lots: when the frame is "
    "primarily that divider (no product, no tape), set lot_boundary_blank_page true, sku null, and a short "
    "item_name like 'Blank lot separator'. The next photo after a divider starts a NEW lot — read sku only from "
    "tape visible on that frame (do not carry sku forward from before the divider). Purple/lavender tape with "
    "handwritten lot digits still marks a new lot via sku as before. "
    "Output must match the JSON schema only — dense factual fields, no prose or banter."
)


def _user_prompt(*, manifest_tag: str) -> str:
    tag_line = ""
    if manifest_tag.strip():
        tag_line = f"Manifest / batch tag: {manifest_tag.strip()}\n"
    return (
        tag_line
        + "Analyze this single catalog photo for an estate sale inventory row.\n"
        "Every frame is part of the catalog: rear labels, underside, power adapters, cables, close-ups of "
        "ports/buttons, and partial product shots still describe the SAME item in this lot — infer from text "
        "logos (HDMI, DVD, Blu-ray, brand names) and visible model numbers. Never return empty JSON; fill "
        "fields from whatever is visible.\n"
        "Rules:\n"
        "- manifest_id: use the manifest tag above if provided, else empty.\n"
        "- item_name: short auction-style title for the item.\n"
        "- brand: primary visible brand as plain text (single string).\n"
        "- material, condition_notes, era: concise factual strings.\n"
        "- sku: NON-NULL ONLY for HANDWRITTEN lot codes on PURPLE or LAVENDER estate tape (not the product body). "
        "Codes may be digits-only (015) or letters plus digits on one line (SR006, SA004, SA 003). Ignore a small "
        "stray digit above the main line if it is not part of the lot id. "
        "Ignore brand/size/care tags and label addresses — those are not the estate lot sku. "
        "Ignore stamped or engraved numbers on the metal base (Sheffield reproduction, pattern numbers like 1420) — "
        "those are manufacturer marks, not purple-tape lot sku. "
        "If a second image is attached, it is a zoomed high-contrast tape crop — read sku from it when present. "
        "If the tape shows #, return digits after # unless the readable code is clearly letter+digit style. "
        "Do not concatenate unrelated numbers. null if unreadable or not on purple/lavender tape. "
        "Describe other numbers in condition_notes.\n"
        "- sku_read_confidence: high | medium | low | unsure — set low/unsure for wide shots, blur, or ambiguous "
        "strokes.\n"
        "- If there is no qualifying tape lot number, set sku to null.\n"
        "- Same physical lot: additional angles usually have sku null until new purple-tape digits appear.\n"
        "- lot_boundary_blank_page: true ONLY when the photo is mostly a blank white/off-white divider page/sheet "
        "between lots (no merchandise, no purple tape). false for product shots, tape-only frames, or white that is "
        "porcelain/label/fabric. When true, set sku null and item_name to something like 'Blank lot separator'. "
        "The frame after a divider is the new lot's main catalog shot — put sku on that frame when tape is visible; "
        "do not leave sku null there just because earlier lots had sku null on detail angles.\n"
        "- item_name must never be empty when the main product is visible: use a short title (e.g. 'Black HDMI DVD player') "
        "or at minimum 'Estate lot' plus digits if sku is set.\n"
        "- labels_and_tags: sizes, fits, care tags only (not long prose).\n"
        "- professional_description: flowing prose only; name confirmed makers with **bold** at first mention. "
        "Never write ``Noted brand:``, ``Noted brands:``, or similar tag lines (not even at the end). "
        "Never mention Unknown, Unidentified, Unbranded, or N/A as a maker — omit them completely. "
        "Put ``Brand: ActualName`` in ``specification_lines`` when a maker is known but not woven into the paragraph; "
        "use ``brand`` only for a clear primary maker (else leave ``brand`` empty).\n"
        "- Use specification_lines for measurable specs; include Model line there if identifiable. "
        "When a ruler, yardstick, or scale appears, add one clear measurement line for that item (dimensions, "
        "length, etc.). Across extra angles of the **same** lot, do not add another line for the same category "
        "when the fact is unchanged (one combined Style / Material / Base / Era / Color / Form / Type row per lot, "
        "or append only genuinely new facts like a second dimension or a new mark).\n"
        "- specification_lines: each row must be plain ``Label: value`` text. Never use Python/JSON dict literals "
        "like ``{'Form': '…'}`` — always ``Form: …`` on one line.\n"
        "- specification_lines: never repeat the same decorative fact under several labels. Use **at most one** "
        "line among Pattern / Motif / Design / Surface pattern (pick the clearest label and merge wording); "
        "do not restate spongeware, spatterware, floral medallions, or glaze mottling in multiple pattern rows. "
        "If Pattern or Motif already names the ware or glaze look, do **not** add a separate Style line that only "
        "repeats that same ware type.\n"
        "- Use **at most one** ``Finish:`` (or ``Glaze:`` / lustre sheen) row unless two values clearly describe "
        "different surfaces (e.g. interior vs exterior); do not restate iridescent / lustre / gloss in multiple "
        "finish lines.\n"
        "- Use **at most one** ``Brand:`` row for makers, importers, or ``Made in …`` origin phrases (join distinct "
        "facts with ``; ``). Do **not** repeat the same maker or origin under ``Brands:``, ``Mark:``, or ``Brand:``. "
        "The separate ``brands`` array should list the same makers once — never restate them only to mirror a "
        "``Brand:`` row you already filled.\n"
        "- Use **at most one** ``Markings:`` row for stamps, impressed numbers, molded numbers, and factory marks "
        "(join distinct facts with ``; ``). Prefer that over separate ``Production mark:``, ``Mark:`` (when it "
        "describes a stamp), or multiple ``Markings:`` rows.\n"
        "- starting_price_usd: numeric USD estimate if reasonable, else null.\n"
    )


def inventory_table_entry_to_lot_payload(row: InventoryTableEntry) -> dict[str, Any]:
    """Convert validated table row to the dict shape expected by LotOutput.from_model."""
    sku_clean = tape_marking_canonical(row.sku)
    title = row.item_name.strip()
    if not title:
        if sku_clean:
            title = f"Estate lot #{sku_clean} (see photos for item)"
        else:
            title = "Estate catalog item (see photos)"
    specs = list(row.specification_lines)
    if row.material.strip() and not any(s.strip().lower().startswith("material:") for s in specs):
        specs.append(f"Material: {row.material.strip()}")
    if row.era.strip() and not any(s.strip().lower().startswith("era:") for s in specs):
        specs.append(f"Era: {row.era.strip()}")
    brands: list[str] = [row.brand.strip()] if row.brand.strip() else []
    desc = row.professional_description.strip() or title
    out: dict[str, Any] = {
        "SKU": sku_clean or None,
        "AuctionTitle": title,
        "ProfessionalDescription": desc,
        "SpecificationLines": specs,
        "Brands": brands,
        "LabelsAndTags": row.labels_and_tags,
        "Condition": row.condition_notes.strip(),
        "StartingPrice": row.starting_price_usd,
    }
    if row.sku_read_confidence is not None:
        out["SkuConfidence"] = row.sku_read_confidence
    if row.lot_boundary_blank_page:
        out["lot_boundary_blank_page"] = True
        out["SKU"] = None
        out["AuctionTitle"] = "Blank lot separator (page)"
        out["ProfessionalDescription"] = "Separator frame between catalog lots; not a listed item."
    return out


_genai_client: Any = None


def _get_genai_client() -> Any | None:
    global _genai_client
    key = (settings.google_api_key or "").strip()
    if not key:
        return None
    if _genai_client is None:
        from google import genai  # noqa: PLC0415

        _genai_client = genai.Client(api_key=key)
    return _genai_client


def _analyze_photo_sdk_from_bytes(
    image_bytes: bytes,
    mime_type: str,
    *,
    manifest_tag: str = "",
    focus_jpeg: bytes | None = None,
) -> dict[str, Any] | None:
    """Gemini via ``google-genai``: ``Client.models.generate_content`` + ``response.parsed``."""
    try:
        from google.genai import errors as genai_errors  # noqa: PLC0415
    except ImportError:
        return None

    client = _get_genai_client()
    if client is None:
        return None

    try:
        from google.genai import types as genai_types  # noqa: PLC0415
    except ImportError:
        return None

    focus_note = ""
    if focus_jpeg:
        focus_note = (
            "\nA second image follows: high-contrast crop of purple/lavender estate tape. "
            "Read sku from that crop when possible; sku digits must be only those after # on the tape if # is shown."
        )
    contents: list[Any] = [
        _user_prompt(manifest_tag=manifest_tag) + focus_note,
        genai_types.Part(inline_data=genai_types.Blob(mime_type=mime_type, data=image_bytes)),
    ]
    if focus_jpeg:
        contents.append(
            genai_types.Part(inline_data=genai_types.Blob(mime_type="image/jpeg", data=focus_jpeg)),
        )
    config = genai_types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=InventoryTableEntry,
        system_instruction=SYSTEM_INSTRUCTION,
    )

    backoff = 4.0
    for _ in range(12):
        try:
            response = client.models.generate_content(
                model=settings.gemini_model,
                contents=contents,
                config=config,
            )
            parsed = response.parsed
            if isinstance(parsed, InventoryTableEntry):
                return inventory_table_entry_to_lot_payload(parsed)
            if parsed is not None:
                row = InventoryTableEntry.model_validate(parsed)
                return inventory_table_entry_to_lot_payload(row)
            return None
        except genai_errors.APIError as exc:
            if getattr(exc, "code", None) in TRANSIENT_GENAI_CODES:
                time.sleep(min(backoff, 120))
                backoff = min(backoff * 2, 120)
                continue
            return None
        except (ValueError, TypeError):
            return None
    return None


def _retry_after_seconds(response: requests.Response, fallback: float) -> float:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return min(fallback, 180)
    try:
        return min(180, float(raw))
    except ValueError:
        return min(fallback, 180)


def _mime_for_path(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    return "image/jpeg"


def _parse_model_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


def _coerce_photo_dict(parsed: Any) -> dict[str, Any] | None:
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                return item
    return None


def _model_url() -> str:
    return (
        f"https://generativelanguage.googleapis.com/"
        f"{settings.gemini_api_version}/models/{settings.gemini_model}:generateContent"
    )


def _generate_content(payload: dict[str, Any], timeout: int = 120) -> str | None:
    headers = {"Content-Type": "application/json", "x-goog-api-key": settings.google_api_key}
    backoff = 8.0
    for _ in range(12):
        try:
            response = requests.post(_model_url(), headers=headers, json=payload, timeout=timeout)
            if response.status_code in TRANSIENT_HTTP:
                wait = _retry_after_seconds(response, backoff)
                time.sleep(wait)
                backoff = min(backoff * 2, 180) if response.status_code == 429 else min(
                    max(backoff, 4.0) * 1.5,
                    90.0,
                )
                continue
            response.raise_for_status()
            body = response.json()
            return body["candidates"][0]["content"]["parts"][0]["text"]
        except (requests.RequestException, KeyError, IndexError, ValueError):
            return None
    return None


def _analyze_photo_rest_from_bytes(
    image_bytes: bytes,
    mime_type: str,
    *,
    manifest_tag: str = "",
    focus_jpeg: bytes | None = None,
) -> dict[str, Any] | None:
    """REST fallback when SDK is unavailable or fails."""
    img_b64 = base64.b64encode(image_bytes).decode("utf-8")

    focus_note = ""
    if focus_jpeg:
        focus_note = (
            "\nA second image part follows: high-contrast crop of purple/lavender estate tape — read sku from it "
            "when possible; sku = digits only after # on the tape if # is visible."
        )
    prompt = _user_prompt(manifest_tag=manifest_tag) + focus_note + (
        "\nReturn JSON with keys: manifest_id, item_name, brand, material, condition_notes, era, "
        "sku, sku_read_confidence, professional_description, specification_lines, labels_and_tags, "
        "starting_price_usd, lot_boundary_blank_page (boolean)."
    )
    parts: list[dict[str, Any]] = [
        {"text": prompt},
        {"inline_data": {"mime_type": mime_type, "data": img_b64}},
    ]
    if focus_jpeg:
        parts.append(
            {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(focus_jpeg).decode("utf-8")}})
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    text = _generate_content(payload)
    if text is None:
        return None
    try:
        parsed = _parse_model_json(text)
    except json.JSONDecodeError:
        return None
    coerced = _coerce_photo_dict(parsed)
    if coerced is None:
        return None
    try:
        row = InventoryTableEntry.model_validate(coerced)
        return inventory_table_entry_to_lot_payload(row)
    except ValueError:
        return coerced


def analyze_photo_from_bytes(
    image_bytes: bytes,
    mime_type: str,
    *,
    manifest_tag: str = "",
    focus_jpeg: bytes | None = None,
) -> dict[str, Any] | None:
    """
    Analyze pre-sized image bytes (e.g. JPEG from Pillow) for the inventory pipeline.

    ``focus_jpeg`` is an optional second JPEG (purple-tape crop) sent with the same prompt.
    """
    sdk = _analyze_photo_sdk_from_bytes(
        image_bytes, mime_type, manifest_tag=manifest_tag, focus_jpeg=focus_jpeg
    )
    if sdk is not None:
        return sdk
    return _analyze_photo_rest_from_bytes(
        image_bytes, mime_type, manifest_tag=manifest_tag, focus_jpeg=focus_jpeg
    )


def analyze_path_with_retries(photo_path: Path, *, manifest_tag: str = "") -> dict[str, Any] | None:
    """
    Try Gemini with progressively sharper JPEGs (detail / label shots often need higher quality).

    Pillow resize/encode runs before each API call so outbound payloads stay small on the first tier.
    When enabled, a purple-tape focus crop is built once from the file and sent as a second image.
    """
    from app.services.tape_focus import purple_tape_focus_jpeg_from_path

    focus_jpeg: bytes | None = None
    if settings.tape_focus_enabled:
        try:
            focus_jpeg = purple_tape_focus_jpeg_from_path(photo_path)
        except Exception:
            focus_jpeg = None

    base_e = settings.gemini_image_max_edge
    base_q = settings.gemini_jpeg_quality
    tiers: list[tuple[int, int]] = [
        (base_e, base_q),
        (min(4096, max(base_e, 2240)), max(base_q, 88)),
        (4096, min(95, max(base_q + 6, 92))),
    ]
    seen: set[tuple[int, int]] = set()
    for max_e, q in tiers:
        t = (max_e, q)
        if t in seen:
            continue
        seen.add(t)
        try:
            img_bytes, mime = prepare_gemini_image_file(photo_path, max_edge=max_e, quality=q)
            out = analyze_photo_from_bytes(
                img_bytes, mime, manifest_tag=manifest_tag, focus_jpeg=focus_jpeg
            )
        except Exception:
            continue
        if out is not None:
            return out
    return None


async def analyze_photos_parallel_async(
    photo_paths: list[Path],
    *,
    manifest_tag: str = "",
    limit: int | None = None,
    on_each: Callable[[int, int, Path, dict[str, Any] | None], None] | None = None,
) -> list[dict[str, Any] | None]:
    """
    Run Gemini on many images concurrently with ``asyncio.gather`` and a bounded semaphore.

    Results are returned in ``photo_paths`` order. ``on_each`` is invoked as soon as each
    photo finishes (completion order), before sibling tasks complete, so SSE can stream
    per-photo progress without waiting for the full batch.
    """
    n = len(photo_paths)
    cap = max(1, settings.gemini_parallelism if limit is None else limit)
    sem = asyncio.Semaphore(cap)

    async def _one(idx: int, path: Path) -> tuple[int, dict[str, Any] | None]:
        result: dict[str, Any] | None = None
        try:
            async with sem:
                result = await asyncio.to_thread(
                    analyze_path_with_retries, path, manifest_tag=manifest_tag
                )
        except Exception:
            result = None
        if on_each is not None:
            try:
                on_each(idx, n, path, result)
            except Exception:
                pass
        return idx, result

    pairs = await asyncio.gather(*[_one(i, p) for i, p in enumerate(photo_paths)])
    pairs.sort(key=lambda x: x[0])
    return [p[1] for p in pairs]


def analyze_photo(photo_path: Path, *, manifest_tag: str = "") -> dict[str, Any] | None:
    """
    Analyze one image for the inventory pipeline (multi-tier resolution for hard frames).

    Primary path: ``google-genai`` with ``response_schema=InventoryTableEntry`` and ``response.parsed``.
    Fallback: REST ``generateContent`` (no client-side schema enforcement).
    """
    return analyze_path_with_retries(photo_path, manifest_tag=manifest_tag)


def _tape_verify_prompt(claimed_digits: str) -> str:
    return (
        "Strict visual audit. Return JSON keys new_lot_from_tape (boolean) and lot_digits_on_tape (string or null). "
        "Set new_lot_from_tape true ONLY when HANDWRITTEN lot digits are visible on PURPLE or LAVENDER ADHESIVE "
        "TAPE (strip or roll) separate from the retail product — including CURVED SIDE or TOP RIM of a tape roll. "
        "False for digits only on paper, tags, sticky notes, printed labels, OEM text, or on the product body. "
        f"Claimed digits are {claimed_digits!r}; lot_digits_on_tape must match when that handwritten-on-tape marker "
        "is visible. Respond JSON only."
        + _tape_verify_prompt_extra()
    )


def _generate_content_tape_verify(payload: dict[str, Any]) -> str | None:
    """
    Same endpoint as ``_generate_content`` but uses explicit (connect, read) timeouts so a hung TLS/socket
    cannot block the inventory worker indefinitely (the old SDK path had no reliable per-call timeout).

    Single POST (no 429 sleep loop): catalog merge must never wait minutes inside one verify call.
    """
    headers = {"Content-Type": "application/json", "x-goog-api-key": settings.google_api_key}
    connect = float(settings.tape_verify_http_connect_seconds)
    read = float(settings.tape_verify_http_read_seconds)
    timeout = (connect, read)
    try:
        response = requests.post(_model_url(), headers=headers, json=payload, timeout=timeout)
        if response.status_code in TRANSIENT_HTTP:
            return None
        response.raise_for_status()
        body = response.json()
        return body["candidates"][0]["content"]["parts"][0]["text"]
    except requests.Timeout:
        return None
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def _verify_tape_sku_inner(photo_path: Path, claimed_digits: str) -> bool | None:
    prompt = _tape_verify_prompt(claimed_digits)
    hi_bytes, hi_mime = prepare_gemini_image_file(
        photo_path,
        max_edge=min(2560, max(settings.gemini_image_max_edge, 1536)),
        quality=min(92, max(settings.gemini_jpeg_quality, 82)),
    )
    img_b64 = base64.b64encode(hi_bytes).decode("utf-8")
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": hi_mime, "data": img_b64}},
                ]
            }
        ],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    text = _generate_content_tape_verify(payload)
    if text is None:
        return None
    try:
        obj = _parse_model_json(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    try:
        audit = TapeSkuAudit.model_validate(obj)
    except ValueError:
        flag = obj.get("new_lot_from_tape")
        if isinstance(flag, str):
            flag = flag.lower().strip() in ("true", "1", "yes")
        if flag is not True:
            return False
        tape_digits = re.sub(r"\D", "", str(obj.get("lot_digits_on_tape") or ""))
        return bool(tape_digits) and int(tape_digits) == int(claimed_digits)
    if audit.new_lot_from_tape is not True:
        return False
    tape_digits = re.sub(r"\D", "", str(audit.lot_digits_on_tape or ""))
    return bool(tape_digits) and int(tape_digits) == int(claimed_digits)


def verify_tape_sku(photo_path: Path, claimed_digits: str) -> bool | None:
    """
    Second-pass audit when the catalog model claims a tape SKU.

    Uses REST ``generateContent`` with bounded connect/read timeouts (not the SDK client, which can
    hang without a socket deadline). Still wrapped in a thread plus ``fut.result`` as a backstop.
    """
    timeout = float(settings.tape_verify_total_timeout_seconds)
    fut = _get_tape_verify_pool().submit(_verify_tape_sku_inner, photo_path, claimed_digits)
    try:
        return fut.result(timeout=timeout)
    except TimeoutError:
        return None
