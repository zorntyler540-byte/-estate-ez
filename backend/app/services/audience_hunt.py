"""Find public collector groups for niche lots (Google Search + structured JSON)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import Lot

GENERIC_SKIP_CATEGORIES = frozenset(
    {
        "household goods other",
        "household goods",
        "kitchen & home",
        "bath",
        "bedroom",
        "bedroom goods",
        "bedroom furniture",
        "dining room",
        "decor",
        "linens",
        "appliances",
    }
)

COLLECTOR_CATEGORY_HINTS = (
    "art",
    "antique",
    "glass",
    "crystal",
    "china",
    "porcelain",
    "pottery",
    "jewelry",
    "coin",
    "stamp",
    "doll",
    "figurine",
    "collect",
    "camera",
    "firearm",
    "gun",
    "military",
    "book",
    "comic",
    "luxury",
    "designer",
    "watch",
    "clock",
    "train",
    "toy",
    "advertising",
    "music",
    "instrument",
    "silver",
    "bronze",
)


def _norm_cat(raw: object | None) -> str:
    return str(raw or "").strip().lower()


def should_run_audience_hunt(
    *,
    title: str,
    brands: list[str],
    category: str | None,
    force: bool = False,
) -> tuple[bool, str]:
    """Whether this lot is worth a Gemini hunt. Force always hunts."""
    if force:
        return True, ""
    brands_clean = [str(b).strip() for b in brands if str(b).strip()]
    cat = _norm_cat(category)
    if brands_clean:
        return True, ""
    if any(hint in cat for hint in COLLECTOR_CATEGORY_HINTS):
        return True, ""
    title_l = (title or "").strip().lower()
    if any(hint in title_l for hint in COLLECTOR_CATEGORY_HINTS):
        return True, ""
    if not cat or cat in GENERIC_SKIP_CATEGORIES:
        return False, "Everyday household lot — no collector hunt."
    return False, "No brand or collector category — skip hunt unless you force it."


def empty_audience_payload(*, skip_reason: str = "") -> dict[str, Any]:
    return {
        "worthy": False,
        "skip_reason": skip_reason,
        "groups": [],
        "blurb_ctbids": "",
        "blurb_hold": "",
        "recommend_hold": False,
        "researched_at": None,
    }


def _safe_public_url(raw: object | None) -> str:
    """Official http(s) group/club URLs only — never javascript: or data:."""
    u = str(raw or "").strip()
    if not u:
        return ""
    low = u.lower()
    if low.startswith("www."):
        u = "https://" + u
        low = u.lower()
    if low.startswith("https://") or low.startswith("http://"):
        return u[:500]
    return ""


def parse_audience_payload(raw: object | None) -> dict[str, Any]:
    base = empty_audience_payload()
    data: Any = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return base
    if not isinstance(data, dict):
        return base
    groups_in = data.get("groups") if isinstance(data.get("groups"), list) else []
    groups: list[dict[str, str]] = []
    for row in groups_in:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        groups.append(
            {
                "name": name[:200],
                "url": _safe_public_url(row.get("url")),
                "why": str(row.get("why") or "").strip()[:400],
            }
        )
    return {
        "worthy": bool(data.get("worthy")),
        "skip_reason": str(data.get("skip_reason") or "")[:400],
        "groups": groups[:8],
        "blurb_ctbids": str(data.get("blurb_ctbids") or "")[:1500],
        "blurb_hold": str(data.get("blurb_hold") or "")[:1500],
        "recommend_hold": bool(data.get("recommend_hold")),
        "researched_at": data.get("researched_at") if isinstance(data.get("researched_at"), str) else None,
    }


def audience_payload_for_lot(lot: Lot) -> dict[str, Any]:
    return parse_audience_payload(getattr(lot, "audience_json", None) or "{}")


def lot_is_held_from_ctseller(lot: Lot) -> bool:
    """True when staff (or hunt) asked to keep this lot off CT Bids for now."""
    return bool(getattr(lot, "audience_hold", False))


def save_audience_payload(lot: Lot, payload: dict[str, Any]) -> None:
    lot.audience_json = json.dumps(parse_audience_payload(payload), ensure_ascii=True)


def _lot_research_text(lot: Lot) -> str:
    brands = []
    try:
        brands = json.loads(lot.brands_json or "[]")
    except json.JSONDecodeError:
        brands = []
    specs = []
    try:
        specs = json.loads(lot.specification_lines_json or "[]")
    except json.JSONDecodeError:
        specs = []
    brand_s = ", ".join(str(b) for b in brands if str(b).strip()) or "(none)"
    spec_s = "\n".join(f"- {s}" for s in specs[:12]) or "(none)"
    return (
        f"Title: {(lot.auction_title or '').strip() or '(untitled)'}\n"
        f"SKU: {(lot.sku or '').strip() or '(none)'}\n"
        f"CT category: {(lot.ct_category or '').strip() or '(none)'}\n"
        f"Brands: {brand_s}\n"
        f"Condition: {(lot.condition_text or '').strip() or '(none)'}\n"
        f"Description: {(lot.professional_description or '').strip() or '(none)'}\n"
        f"Specs:\n{spec_s}\n"
    )


def _ensure_gemini_env() -> None:
    """Pipeline client reads os.environ; copy the same key catalog jobs use from settings/.env."""
    from app.config import settings

    key = (settings.google_api_key or "").strip()
    if key:
        os.environ.setdefault("GOOGLE_API_KEY", key)
        os.environ.setdefault("GEMINI_API_KEY", key)


def run_audience_research_and_structure(lot: Lot) -> dict[str, Any]:
    """Two-step hunt: Google Search memo, then structured JSON (no tools on schema call)."""
    from google.genai import types

    from pipeline.gemini_schema import pydantic_model_to_gemini_schema
    from pipeline.pipeline import GEMINI_PRO_MODEL, _get_client
    from pipeline.schemas import AudienceHuntResult

    _ensure_gemini_env()
    client = _get_client()
    listing = _lot_research_text(lot)

    research_parts = [
        "Find public collector communities for this estate lot.\n"
        "Search for Facebook groups, collector clubs, forums, and association pages that would "
        "care about this object. Prefer official/group URLs. Do not invent member names or emails.\n"
        "If this is generic household junk, say so.\n\n"
        f"{listing}"
    ]
    research_config = types.GenerateContentConfig(
        system_instruction=(
            "You research where collectors of estate-sale items gather online. "
            "Use Google Search. Name public groups/clubs/forums with URLs when you can find them. "
            "Never harvest personal emails or private member lists. Plain text memo only."
        ),
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2,
    )
    research = client.models.generate_content(
        model=GEMINI_PRO_MODEL,
        contents=research_parts,
        config=research_config,
    )
    memo = (research.text or "").strip()
    if not memo:
        raise RuntimeError("Audience research returned no text.")

    structure_parts = [
        "Turn the research memo into structured JSON for staff. "
        "groups: public communities only (name, url, why). Empty url is OK if the name is real. "
        "blurb_ctbids: 2–4 sentences inviting those people to bid on a local CT Bids estate auction "
        "(bidding often starts at $1; pickup at the sale). Do not invent a sale URL. "
        "blurb_hold: 2–4 sentences offering the piece to a collector group before it goes to auction. "
        "worthy true only if a named collector community is realistic. "
        "recommend_hold true only for clearly niche/high-interest pieces.\n\n"
        f"Lot:\n{listing}\n\nResearch memo:\n{memo}"
    ]
    structure_config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=pydantic_model_to_gemini_schema(AudienceHuntResult),
        system_instruction=(
            "You convert collector-community research into structured JSON only. "
            "Public group/club/forum names and official URLs — no personal contact harvesting."
        ),
        temperature=0.1,
    )
    structured = client.models.generate_content(
        model=GEMINI_PRO_MODEL,
        contents=structure_parts,
        config=structure_config,
    )
    if structured.parsed is not None:
        result = AudienceHuntResult.model_validate(structured.parsed)
    elif structured.text:
        result = AudienceHuntResult.model_validate(json.loads(structured.text))
    else:
        raise RuntimeError("Audience structure call returned no content.")

    payload = parse_audience_payload(result.model_dump())
    payload["researched_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def apply_skip_without_model(lot: Lot, reason: str) -> dict[str, Any]:
    payload = empty_audience_payload(skip_reason=reason)
    payload["researched_at"] = datetime.now(timezone.utc).isoformat()
    save_audience_payload(lot, payload)
    return parse_audience_payload(payload)


def run_audience_hunt_for_lot(db: Session, lot: Lot, *, force: bool = False) -> dict[str, Any]:
    """Hunt (or skip) and persist on the lot. Caller owns commit."""
    brands: list[str] = []
    try:
        brands = [str(b) for b in json.loads(lot.brands_json or "[]")]
    except json.JSONDecodeError:
        brands = []
    hunt, reason = should_run_audience_hunt(
        title=lot.auction_title or "",
        brands=brands,
        category=lot.ct_category,
        force=force,
    )
    if not hunt:
        return apply_skip_without_model(lot, reason)
    payload = run_audience_research_and_structure(lot)
    save_audience_payload(lot, payload)
    if payload.get("recommend_hold") and not lot.audience_hold:
        lot.audience_hold = True
    return parse_audience_payload(lot.audience_json)
