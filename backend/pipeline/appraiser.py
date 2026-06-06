"""Field appraisal: Pro + Google Search research, then structured JSON (no catalog pipeline)."""

from __future__ import annotations

import json
from typing import Any

from google.genai import types

from app.services.category_inference import ct_category_names
from pipeline.gemini_schema import pydantic_model_to_gemini_schema
from pipeline.pipeline import (
    GEMINI_PRO_MODEL,
    _detect_mime_type,
    _get_client,
    _image_part,
    _maybe_downscale,
)
from pipeline.schemas import AppraisalCheckResult

APPRAISER_RESEARCH_SYSTEM = (
    "You are a senior estate liquidation appraiser for Caring Transitions. "
    "Use Google Search to find realistic sold/asking comparables for estate-sale liquidation pricing. "
    "Consider whether items fit a local CT Bids estate auction vs broader eBay/national market. "
    "Flag high-value pieces (roughly $200+ resale or strong collector interest). "
    "Be conservative — estate opening bids are lower than retail. "
    "Output a concise research memo in plain text (not JSON)."
)

APPRAISER_STRUCTURE_SYSTEM = (
    "You are a senior estate liquidation appraiser. "
    "Given photos, optional scene note, and a research memo, emit structured JSON only. "
    "Pick recommended_ct_category from the allowed CT Seller categories list. "
    "listing_channel guidance: ct_bids for typical local estate lots under ~$75 opening; "
    "ebay for national/collectible/high-shipping items; either when both work; needs_review when uncertain."
)


def _image_parts(photo_bytes_by_name: dict[str, bytes]) -> list[Any]:
    parts: list[Any] = []
    for filename, raw in photo_bytes_by_name.items():
        img_bytes, mime_type = _maybe_downscale(raw, _detect_mime_type(raw))
        parts.append(_image_part(img_bytes, mime_type))
        parts.append(f"The preceding image filename is: {filename}")
    return parts


def _usage_tokens(response: Any) -> tuple[int, int]:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return 0, 0
    inp = int(getattr(meta, "prompt_token_count", 0) or 0)
    out = int(getattr(meta, "candidates_token_count", 0) or 0)
    return inp, out


def run_appraisal_research(
    photo_bytes_by_name: dict[str, bytes],
    *,
    scene_note: str = "",
) -> tuple[str, int, int]:
    """Step 1: Pro + Google Search → plain-text research memo."""
    client = _get_client()
    note_block = f"\n\nScene note from appraiser:\n{scene_note.strip()}\n" if scene_note.strip() else ""
    parts: list[Any] = [
        "Appraise what is visible in these job-site photos (multiple distinct items per photo is OK).\n"
        "Research realistic estate-sale liquidation value and where to sell (CT Bids local auction vs eBay).\n"
        "Include: identification, condition cues, comp ranges, high-value assessment, channel recommendation."
        f"{note_block}",
    ]
    parts.extend(_image_parts(photo_bytes_by_name))

    config = types.GenerateContentConfig(
        system_instruction=APPRAISER_RESEARCH_SYSTEM,
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2,
    )
    response = client.models.generate_content(
        model=GEMINI_PRO_MODEL,
        contents=parts,
        config=config,
    )
    memo = (response.text or "").strip()
    if not memo:
        raise RuntimeError("Appraisal research returned no text.")
    inp, out = _usage_tokens(response)
    return memo, inp, out


def run_appraisal_structure(
    photo_bytes_by_name: dict[str, bytes],
    *,
    scene_note: str,
    research_memo: str,
) -> tuple[AppraisalCheckResult, int, int]:
    """Step 2: Pro structured JSON (no search — avoids schema + tools 400)."""
    client = _get_client()
    categories = ", ".join(ct_category_names())
    note_block = f"\n\nScene note:\n{scene_note.strip()}\n" if scene_note.strip() else ""
    parts: list[Any] = [
        "Convert the research memo and photos into structured appraisal JSON.\n"
        f"Allowed CT Seller categories: {categories}\n"
        f"\nResearch memo:\n{research_memo}"
        f"{note_block}",
    ]
    parts.extend(_image_parts(photo_bytes_by_name))

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=pydantic_model_to_gemini_schema(AppraisalCheckResult),
        system_instruction=APPRAISER_STRUCTURE_SYSTEM,
        temperature=0.1,
    )
    response = client.models.generate_content(
        model=GEMINI_PRO_MODEL,
        contents=parts,
        config=config,
    )
    if response.text:
        result = AppraisalCheckResult.model_validate(json.loads(response.text))
    elif response.parsed is not None:
        result = AppraisalCheckResult.model_validate(response.parsed)
    else:
        raise RuntimeError("Appraisal structure call returned no content.")

    if not (result.search_summary or "").strip():
        result = result.model_copy(update={"search_summary": research_memo[:2000]})

    inp, out = _usage_tokens(response)
    return result, inp, out


def run_field_appraisal(
    photo_bytes_by_name: dict[str, bytes],
    *,
    scene_note: str = "",
) -> tuple[AppraisalCheckResult, dict[str, int]]:
    """Two-step field appraisal; returns result and token usage totals."""
    memo, in1, out1 = run_appraisal_research(photo_bytes_by_name, scene_note=scene_note)
    result, in2, out2 = run_appraisal_structure(
        photo_bytes_by_name,
        scene_note=scene_note,
        research_memo=memo,
    )
    return result, {
        "input": in1 + in2,
        "output": out1 + out2,
    }
