"""Find public collector groups for niche lots (Google Search + structured JSON)."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session, object_session

from app.models import AudienceHuntEvent, Lot

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


def empty_hunt_usage() -> dict[str, int]:
    return {"pro_in": 0, "pro_out": 0, "search_calls": 0}


def audience_usage_tokens(response: object) -> tuple[int, int]:
    """Prompt + billed output (candidates + thinking) from Gemini usage_metadata."""
    try:
        meta = getattr(response, "usage_metadata", None)
        if meta is None:
            return 0, 0
        inp = int(getattr(meta, "prompt_token_count", 0) or 0)
        out = int(getattr(meta, "candidates_token_count", 0) or 0)
        thoughts = int(getattr(meta, "thoughts_token_count", 0) or 0)
        return inp, out + thoughts
    except (TypeError, ValueError):
        return 0, 0


def add_audience_usage(usage: dict[str, int], response: object, *, search: bool = False) -> None:
    inp, out = audience_usage_tokens(response)
    usage["pro_in"] = int(usage.get("pro_in", 0) or 0) + inp
    usage["pro_out"] = int(usage.get("pro_out", 0) or 0) + out
    if search:
        usage["search_calls"] = int(usage.get("search_calls", 0) or 0) + 1


def hunt_pro_cost_usd(*, pro_in: int, pro_out: int, in_rate: float, out_rate: float) -> float:
    return round((pro_in / 1_000_000.0) * in_rate + (pro_out / 1_000_000.0) * out_rate, 4)


def summarize_hunt_events(events: list[Any]) -> dict[str, Any]:
    """Roll hunt rows into admin analytics fields."""
    hunts_run = hunts_skipped = hunts_failed = 0
    hunt_groups_found = hunt_search_calls = 0
    pro_in = pro_out = 0
    last_at = None
    for ev in events:
        skipped = bool(getattr(ev, "skipped", False))
        err = (getattr(ev, "error", None) or "").strip()
        if skipped:
            hunts_skipped += 1
        elif err:
            hunts_failed += 1
        else:
            hunts_run += 1
        hunt_groups_found += int(getattr(ev, "group_count", 0) or 0)
        hunt_search_calls += int(getattr(ev, "search_calls", 0) or 0)
        pro_in += int(getattr(ev, "actual_pro_input_tokens", 0) or 0)
        pro_out += int(getattr(ev, "actual_pro_output_tokens", 0) or 0)
        created = getattr(ev, "created_at", None)
        if created is not None and (last_at is None or created > last_at):
            last_at = created
    return {
        "hunts_run": hunts_run,
        "hunts_skipped": hunts_skipped,
        "hunts_failed": hunts_failed,
        "hunt_groups_found": hunt_groups_found,
        "hunt_search_calls": hunt_search_calls,
        "hunt_pro_input_tokens": pro_in,
        "hunt_pro_output_tokens": pro_out,
        "last_hunt_at": last_at,
    }


def record_audience_hunt_event(
    lot: Lot,
    *,
    skipped: bool = False,
    skip_reason: str = "",
    payload: dict[str, Any] | None = None,
    usage: dict[str, int] | None = None,
    error: str | None = None,
) -> None:
    """Persist one hunt attempt. Caller owns commit. No-op if the lot is not session-bound."""
    try:
        sess = object_session(lot)
    except Exception:
        return
    if sess is None:
        return
    data = payload or {}
    groups = data.get("groups") if isinstance(data.get("groups"), list) else []
    u = usage or empty_hunt_usage()
    sess.add(
        AudienceHuntEvent(
            account_id=lot.account_id,
            lot_id=getattr(lot, "id", None),
            sale_id=getattr(lot, "sale_id", None),
            skipped=bool(skipped),
            skip_reason=(skip_reason or str(data.get("skip_reason") or ""))[:400] or None,
            worthy=bool(data.get("worthy")),
            recommend_hold=bool(data.get("recommend_hold")),
            group_count=len(groups),
            search_calls=int(u.get("search_calls", 0) or 0),
            actual_pro_input_tokens=int(u.get("pro_in", 0) or 0),
            actual_pro_output_tokens=int(u.get("pro_out", 0) or 0),
            error=(error or None)[:2000] if error else None,
        )
    )
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


# Longest first so ``.co.uk`` matches before ``.uk``.
_NON_US_HOST_SUFFIXES = (
    ".co.uk",
    ".org.uk",
    ".ac.uk",
    ".gov.uk",
    ".com.au",
    ".org.au",
    ".net.au",
    ".co.nz",
    ".co.za",
    ".co.jp",
    ".gc.ca",
    ".uk",
    ".ca",
    ".au",
    ".nz",
    ".za",
    ".ie",
    ".eu",
    ".de",
    ".fr",
    ".nl",
    ".be",
    ".at",
    ".ch",
    ".it",
    ".es",
    ".pt",
    ".pl",
    ".se",
    ".no",
    ".dk",
    ".fi",
    ".jp",
    ".kr",
    ".cn",
    ".tw",
    ".hk",
    ".sg",
    ".in",
    ".mx",
    ".br",
    ".ru",
)

# Group location / membership — not object origin (keep "English china" US groups).
_FOREIGN_GROUP_LOCATION = re.compile(
    r"\b("
    r"united kingdom|u\.?k\.?|britain|great britain|"
    r"canada|canadian|"
    r"australia|australian|"
    r"new zealand|"
    r"ireland|"
    r"germany|deutschland|"
    r"france|"
    r"netherlands|holland|"
    r"sweden|norway|denmark|"
    r"japan|"
    r"singapore|hong kong|"
    r"south africa|"
    r"mexico|mexican|"
    r"india|"
    r"scotland|wales"
    r")\b",
    re.I,
)
_INTERNATIONAL_SCOPE = re.compile(r"\b(international|worldwide|global|europe(?:an)?)\b", re.I)
_US_GROUP_LOCATION = re.compile(
    r"\b(united states|u\.s\.a\.?|usa|u\.s\.|american|us-based|us based)\b",
    re.I,
)


def _host_is_non_us(host: str) -> bool:
    h = (host or "").lower().rstrip(".")
    if h.startswith("www."):
        h = h[4:]
    if not h:
        return False
    for suffix in _NON_US_HOST_SUFFIXES:
        bare = suffix.lstrip(".")
        if h == bare or h.endswith(suffix):
            return True
    return False


def is_us_audience_group(*, name: str, url: str, why: str = "") -> bool:
    """True when this community is usable for a US estate sale (not a foreign/international club)."""
    title = (name or "").strip()
    if _US_GROUP_LOCATION.search(title):
        return True
    href = (url or "").strip()
    if href:
        try:
            parsed = urlparse(href)
        except ValueError:
            parsed = None
        if parsed is not None and _host_is_non_us(parsed.hostname or ""):
            return False
        if _FOREIGN_GROUP_LOCATION.search(href) or _INTERNATIONAL_SCOPE.search(href):
            return False
    loc_text = f"{title} {why or ''}"
    if _INTERNATIONAL_SCOPE.search(loc_text):
        return False
    if _FOREIGN_GROUP_LOCATION.search(title):
        return False
    if _FOREIGN_GROUP_LOCATION.search(why or "") and not _US_GROUP_LOCATION.search(why or ""):
        # "UK based club" in why — drop. "English hobnail" does not match these patterns.
        return False
    return True


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
        hit = {
            "name": name[:200],
            "url": _safe_public_url(row.get("url")),
            "why": str(row.get("why") or "").strip()[:400],
        }
        if not is_us_audience_group(name=hit["name"], url=hit["url"], why=hit["why"]):
            continue
        groups.append(hit)
    kept = groups[:8]
    return {
        "worthy": bool(data.get("worthy")) and bool(kept),
        "skip_reason": str(data.get("skip_reason") or "")[:400],
        "groups": kept,
        "blurb_ctbids": str(data.get("blurb_ctbids") or "")[:1500],
        "blurb_hold": str(data.get("blurb_hold") or "")[:1500],
        "recommend_hold": bool(data.get("recommend_hold")) and bool(kept),
        "researched_at": data.get("researched_at") if isinstance(data.get("researched_at"), str) else None,
    }


def audience_payload_for_lot(lot: Lot) -> dict[str, Any]:
    return parse_audience_payload(getattr(lot, "audience_json", None) or "{}")


def lot_brands(lot: Lot) -> list[str]:
    try:
        raw = json.loads(getattr(lot, "brands_json", None) or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    return [str(b).strip() for b in raw if str(b).strip()]


def lot_has_audience_research(lot: Lot) -> bool:
    """True after a hunt or skip has been saved (so sale-wide re-runs can leave it alone)."""
    payload = audience_payload_for_lot(lot)
    if payload.get("researched_at"):
        return True
    if payload.get("groups"):
        return True
    if str(payload.get("skip_reason") or "").strip():
        return True
    return False


def plan_sale_audience_hunts(
    lots: list[Lot],
    *,
    force: bool = False,
    only_unhunted: bool = True,
    limit: int = 40,
) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Pick lots worth queueing. Returns (queue_ids, skips of lot_id, sku, reason)."""
    queued: list[str] = []
    skipped: list[tuple[str, str, str]] = []
    for lot in lots:
        lot_id = str(getattr(lot, "id", "") or "")
        sku = str(getattr(lot, "sku", None) or "")
        if not lot_id:
            continue
        if only_unhunted and not force and lot_has_audience_research(lot):
            skipped.append((lot_id, sku, "Already hunted"))
            continue
        hunt, reason = should_run_audience_hunt(
            title=getattr(lot, "auction_title", None) or "",
            brands=lot_brands(lot),
            category=getattr(lot, "ct_category", None),
            force=force,
        )
        if not hunt:
            skipped.append((lot_id, sku, reason or "Skipped"))
            continue
        if len(queued) >= limit:
            skipped.append((lot_id, sku, "Over hunt limit this run"))
            continue
        queued.append(lot_id)
    return queued, skipped


def lot_is_held_from_ctseller(lot: Lot) -> bool:
    """True when staff (or hunt) asked to keep this lot off CT Bids for now."""
    return bool(getattr(lot, "audience_hold", False))


def save_audience_payload(lot: Lot, payload: dict[str, Any]) -> None:
    lot.audience_json = json.dumps(parse_audience_payload(payload), ensure_ascii=True)


def _lot_research_text(lot: Lot, *, location: str = "") -> str:
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
    loc = (location or "").strip()
    loc_line = (
        f"Pickup city (United States; ships nationwide — city is never a filter): {loc}\n"
        if loc
        else "Pickup is at a US estate sale; items ship nationwide inside the US.\n"
    )
    return (
        f"{loc_line}"
        f"Title: {(lot.auction_title or '').strip() or '(untitled)'}\n"
        f"SKU: {(lot.sku or '').strip() or '(none)'}\n"
        f"CT category: {(lot.ct_category or '').strip() or '(none)'}\n"
        f"Brands: {brand_s}\n"
        f"Condition: {(lot.condition_text or '').strip() or '(none)'}\n"
        f"Description: {(lot.professional_description or '').strip() or '(none)'}\n"
        f"Specs:\n{spec_s}\n"
    )


def location_for_lot(lot: Lot) -> str:
    """City/metro from the franchise account, Facebook Page, or sale name."""
    from app.models import Account, Sale
    from app.services.facebook_groups import infer_location_label

    try:
        session = object_session(lot)
    except Exception:
        session = None
    account_name = ""
    page_name = ""
    sale_name = ""
    if session is not None:
        if lot.account_id:
            account = session.get(Account, lot.account_id)
            if account is not None:
                account_name = account.name or ""
                page_name = account.facebook_page_name or ""
        if lot.sale_id:
            sale = session.get(Sale, lot.sale_id)
            if sale is not None:
                sale_name = sale.name or ""
    return infer_location_label(
        account_name=account_name,
        sale_name=sale_name,
        page_name=page_name,
    )


def audience_research_prompt(lot: Lot, *, location: str = "") -> str:
    """Plain-text research brief sent to Gemini Pro + Google Search."""
    loc = (location or "").strip()
    loc_bit = ""
    if loc:
        loc_bit = (
            f"SECONDARY only — never a filter: pickup is in {loc}. After national groups, you may "
            "add a few local public Facebook groups there (estate sales, buy/sell/trade, garage sale). "
            "Do not limit, rank, or drop national collector groups because they are not in that city. "
            "Items ship nationwide.\n"
        )
    return (
        "Find public United States collector, hobby, and interest communities for this estate lot.\n"
        "PRIMARY: national / US-wide Facebook groups, collector clubs, forums, and association pages "
        "for this object type or brand. Items ship nationwide inside the US, so do not limit results "
        "to the sale city. Prefer official/group URLs. Do not invent member names or emails.\n"
        "United States only: drop UK, Canada, Australia, EU, or worldwide-only clubs. US groups that "
        "collect foreign-made objects (English china, Japanese pottery) are OK.\n"
        f"{loc_bit}"
        "If this is generic household junk, say so.\n\n"
        f"{_lot_research_text(lot, location=loc)}"
    )


def _ensure_gemini_env() -> None:
    """Pipeline client reads os.environ; copy the same key catalog jobs use from settings/.env."""
    from app.config import settings

    key = (settings.google_api_key or "").strip()
    if key:
        os.environ.setdefault("GOOGLE_API_KEY", key)
        os.environ.setdefault("GEMINI_API_KEY", key)


def run_audience_research_and_structure(
    lot: Lot,
    usage: dict[str, int] | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Two-step hunt: Google Search memo, then structured JSON (no tools on schema call)."""
    from google.genai import types

    from pipeline.gemini_schema import pydantic_model_to_gemini_schema
    from pipeline.pipeline import GEMINI_PRO_MODEL, _get_client
    from pipeline.schemas import AudienceHuntResult

    bag = usage if usage is not None else empty_hunt_usage()
    _ensure_gemini_env()
    client = _get_client()
    location = location_for_lot(lot)
    listing = _lot_research_text(lot, location=location)
    if on_phase:
        on_phase("searching")

    research_parts = [audience_research_prompt(lot, location=location)]
    research_config = types.GenerateContentConfig(
        system_instruction=(
            "You research where United States collectors of estate-sale items gather online. "
            "Use Google Search. Primary target: national/US-wide public collector, hobby, and "
            "interest groups for this object — shipping is available nationwide, so do not filter "
            "by sale city. Local city groups are a short extra section only. "
            "Skip international, worldwide, and non-US country communities. "
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
    add_audience_usage(bag, research, search=True)
    memo = (research.text or "").strip()
    if not memo:
        raise RuntimeError("Audience research returned no text.")
    if on_phase:
        on_phase("structuring")

    structure_parts = [
        "Turn the research memo into structured JSON for staff. "
        "groups: United States public communities only (name, url, why). Empty url is OK if the name is real. "
        "List national/US-wide collector, hobby, and interest groups FIRST. Local sale-city groups "
        "SECOND if present. Do not drop a national group because it is not in the sale city — items "
        "ship nationwide. City is never a filter. "
        "Drop UK/Canada/Australia/EU/worldwide clubs even if they collect this brand. "
        "blurb_ctbids: 2–4 sentences inviting those US people to bid on a CT Bids estate auction "
        "(bidding often starts at $1; pickup at the sale; ships nationwide). Do not invent a sale URL. "
        "blurb_hold: 2–4 sentences offering the piece to a US collector group before it goes to auction. "
        "worthy true only if a named US collector community is realistic. "
        "recommend_hold true only for clearly niche/high-interest pieces.\n\n"
        f"Lot:\n{listing}\n\nResearch memo:\n{memo}"
    ]
    structure_config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=pydantic_model_to_gemini_schema(AudienceHuntResult),
        system_instruction=(
            "You convert collector-community research into structured JSON only. "
            "National/US-wide public groups first, local city groups second. City is never a filter. "
            "United States public group/club/forum names and official URLs — no international clubs, "
            "no personal contact harvesting."
        ),
        temperature=0.1,
    )
    structured = client.models.generate_content(
        model=GEMINI_PRO_MODEL,
        contents=structure_parts,
        config=structure_config,
    )
    add_audience_usage(bag, structured)
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
    record_audience_hunt_event(lot, skipped=True, skip_reason=reason, payload=payload)
    return parse_audience_payload(payload)


def run_audience_hunt_for_lot(
    db: Session,
    lot: Lot,
    *,
    force: bool = False,
    on_phase: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Hunt (or skip) and persist on the lot. Caller owns commit."""
    brands = lot_brands(lot)
    hunt, reason = should_run_audience_hunt(
        title=lot.auction_title or "",
        brands=brands,
        category=lot.ct_category,
        force=force,
    )
    if not hunt:
        return apply_skip_without_model(lot, reason)
    usage = empty_hunt_usage()
    try:
        payload = run_audience_research_and_structure(lot, usage, on_phase=on_phase)
        save_audience_payload(lot, payload)
        blurb = str(payload.get("blurb_ctbids") or "").strip()
        if blurb:
            from app.services.click_tracking import CHANNEL_GROUP_SHARE, rewrite_copy_with_tracked_links

            payload["blurb_ctbids"] = rewrite_copy_with_tracked_links(
                db,
                account_id=lot.account_id,
                lot=lot,
                text=blurb,
                channel=CHANNEL_GROUP_SHARE,
                append_if_missing=True,
            )
            save_audience_payload(lot, payload)
        if payload.get("recommend_hold") and not lot.audience_hold:
            lot.audience_hold = True
        record_audience_hunt_event(lot, payload=payload, usage=usage)
        return parse_audience_payload(lot.audience_json)
    except Exception as exc:
        record_audience_hunt_event(lot, usage=usage, error=f"{type(exc).__name__}: {exc}"[:2000])
        raise
