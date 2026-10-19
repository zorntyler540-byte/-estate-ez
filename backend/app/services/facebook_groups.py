"""Sale-level Facebook group list: real Gemini/Search hits plus search-link fallbacks.

Meta's Graph API cannot search or post to Groups. Real group names/URLs come from the
existing audience hunt (Gemini Pro + Google Search grounding). This module aggregates
those hits for a sale and adds clickable Facebook group-search links as extras.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote_plus

from app.models import Account, Lot, Sale
from app.services.audience_hunt import COLLECTOR_CATEGORY_HINTS, lot_brands

FACEBOOK_GROUP_SEARCH = "https://www.facebook.com/search/groups/?q="

GROUP_POSTING_TIPS = (
    "Join active groups, read each group's rules, then share 1–2 of this sale's best lots. "
    "From a Page post use Share → Share to a group. Do not spam the same listing across every group."
)

_CT_OF = re.compile(r"caring\s+transitions\s+of\s+(.+)", re.I)
_CT_PREFIX = re.compile(r"^(?:caring\s+transitions|ct)\b[\s:.\-]*", re.I)
_TRAILING_FLUFF = re.compile(
    r"[\s,.\-]+(?:page|llc|inc|estate\s+sales?|facebook)?\.?$",
    re.I,
)
_SPLIT_PLACES = re.compile(r"\s*[-–—,/|]\s*|\s+of\s+", re.I)

_LOCAL_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("{place} estate sales", "Local people who follow estate sales in {place}."),
    ("{place} estate sale", "Same city, slightly different Facebook search wording."),
    ("{place} buy sell trade", "Neighborhood buy/sell groups near the sale."),
    ("{place} garage sale", "Garage-sale and yard-sale groups in {place}."),
    ("{place} antiques", "Local antiques and vintage groups in {place}."),
)

_GENERIC_LOCAL: tuple[tuple[str, str], ...] = (
    ("estate sales", "Generic US estate-sale groups — add a city once the franchise location is on the account."),
    ("buy sell trade", "General buy/sell groups when no city is on the account yet."),
)

_HINT_QUERIES: tuple[tuple[str, str, str], ...] = (
    ("jewelry", "vintage costume jewelry collectors", "National US collector groups for jewelry lots (ships nationwide)."),
    ("glass", "vintage glass collectors", "National US collector groups for glass lots (ships nationwide)."),
    ("crystal", "crystal collectors", "National US collector groups for crystal lots (ships nationwide)."),
    ("china", "vintage china collectors", "National US collector groups for china lots (ships nationwide)."),
    ("porcelain", "porcelain collectors", "National US collector groups for porcelain lots (ships nationwide)."),
    ("pottery", "pottery collectors", "National US collector groups for pottery lots (ships nationwide)."),
    ("coin", "coin collectors", "National US collector groups for coin lots (ships nationwide)."),
    ("stamp", "stamp collectors", "National US collector groups for stamp lots (ships nationwide)."),
    ("doll", "doll collectors", "National US collector groups for doll lots (ships nationwide)."),
    ("figurine", "figurine collectors", "National US collector groups for figurine lots (ships nationwide)."),
    ("camera", "vintage camera collectors", "National US collector groups for camera lots (ships nationwide)."),
    ("watch", "vintage watch collectors", "National US collector groups for watch lots (ships nationwide)."),
    ("clock", "antique clock collectors", "National US collector groups for clock lots (ships nationwide)."),
    ("military", "military collectors", "National US collector groups for military lots (ships nationwide)."),
    ("toy", "vintage toy collectors", "National US collector groups for toy lots (ships nationwide)."),
    ("train", "model train collectors", "National US collector groups for train lots (ships nationwide)."),
    ("silver", "sterling silver collectors", "National US collector groups for silver lots (ships nationwide)."),
    ("book", "rare book collectors", "National US collector groups for book lots (ships nationwide)."),
    ("comic", "comic book collectors", "National US collector groups for comic lots (ships nationwide)."),
    ("art", "art collectors", "National US collector groups for art lots (ships nationwide)."),
    ("antique", "antiques collectors", "National US collector groups for antique lots (ships nationwide)."),
)


def facebook_group_search_url(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return FACEBOOK_GROUP_SEARCH
    return FACEBOOK_GROUP_SEARCH + quote_plus(q)


def _title_place(raw: str) -> str:
    text = _TRAILING_FLUFF.sub("", (raw or "").strip(" \t.-–—,/|"))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return " ".join(part[:1].upper() + part[1:] if part else "" for part in text.split(" "))


def _add_unique(dest: list[str], value: str) -> None:
    text = _title_place(value)
    if not text or len(text) < 3:
        return
    if text.lower() in {"caring transitions", "ct", "page", "facebook"}:
        return
    if any(text.lower() == existing.lower() for existing in dest):
        return
    dest.append(text)


def _places_from_text(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    found: list[str] = []
    match = _CT_OF.search(text)
    blob = ""
    if match:
        blob = match.group(1)
    else:
        stripped = _CT_PREFIX.sub("", text).strip()
        if stripped and stripped.lower() != text.lower():
            blob = stripped
    if blob:
        _add_unique(found, blob)
        for part in _SPLIT_PLACES.split(blob):
            _add_unique(found, part)
        return found
    # Bare sale names like "Henderson" or "Oct estate — Las Vegas"
    for part in _SPLIT_PLACES.split(text):
        cleaned = re.sub(
            r"\b(estate|sales?|auction|facebook|demo|test|pickup|oct|nov|dec|jan|feb|mar|apr|may|jun|jul|aug|sep)\b",
            "",
            part,
            flags=re.I,
        )
        cleaned = re.sub(r"\d+", "", cleaned)
        _add_unique(found, cleaned)
    return found


def infer_location_parts(
    *,
    account_name: str = "",
    sale_name: str = "",
    page_name: str = "",
    extra: str = "",
) -> tuple[str, list[str]]:
    """Return (primary city label, unique place names) from franchise / sale / Page names."""
    places: list[str] = []
    for text in (account_name, page_name, extra, sale_name):
        for place in _places_from_text(text):
            _add_unique(places, place)
    if not places:
        return "", []
    hyphenated = [p for p in places if "-" in p]
    # Enterprise-Las Vegas → prefer Las Vegas as the search city.
    if hyphenated:
        tail = hyphenated[0].split("-")[-1].strip()
        if tail:
            primary = tail
            _add_unique(places, tail)
        else:
            primary = places[-1]
    else:
        primary = places[0]
    # Keep primary first for display.
    ordered = [primary] + [p for p in places if p.lower() != primary.lower()]
    return primary, ordered


def infer_location_label(
    *,
    account_name: str = "",
    sale_name: str = "",
    page_name: str = "",
    extra: str = "",
) -> str:
    primary, _places = infer_location_parts(
        account_name=account_name,
        sale_name=sale_name,
        page_name=page_name,
        extra=extra,
    )
    return primary


def location_for_account_sale(account: Account | None, sale: Sale | None) -> tuple[str, list[str]]:
    return infer_location_parts(
        account_name=(account.name if account else "") or "",
        sale_name=(sale.name if sale else "") or "",
        page_name=(account.facebook_page_name if account else "") or "",
    )


def classify_group_kind(name: str, why: str = "", url: str = "", places: list[str] | None = None) -> str:
    blob = f"{name} {why} {url}".lower()
    for place in places or []:
        if place and place.lower() in blob:
            return "local"
    if any(token in blob for token in ("estate sale", "buy sell", "garage sale", "yard sale", "bst ")):
        return "local"
    return "interest"


def collect_sale_themes(lots: list[Lot]) -> list[tuple[str, str]]:
    """(search query, why) from brands and collector categories already used by Find audience."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []

    def add(query: str, why: str) -> None:
        key = query.strip().lower()
        if not key or key in seen:
            return
        seen.add(key)
        out.append((query.strip(), why))

    for lot in lots:
        for brand in lot_brands(lot):
            add(
                f"{brand} collectors",
                f"National US groups for this brand from lot {(lot.sku or '').strip() or 'in this sale'} (ships nationwide).",
            )
        blob = f"{lot.ct_category or ''} {lot.auction_title or ''}".lower()
        for hint, query, why in _HINT_QUERIES:
            if hint in blob:
                add(query, why)
        for hint in COLLECTOR_CATEGORY_HINTS:
            if hint in blob and not any(hint in q.lower() for q, _why in out):
                add(f"{hint} collectors", f"National US groups for {hint} lots (ships nationwide).")
    return out[:10]


def fallback_group_searches(
    *,
    places: list[str],
    themes: list[tuple[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(query: str, why: str, kind: str) -> None:
        q = (query or "").strip()
        if not q:
            return
        key = q.lower()
        if key in seen:
            return
        seen.add(key)
        rows.append({"query": q, "url": facebook_group_search_url(q), "why": why, "kind": kind})

    for query, why in themes:
        add(query, why, "interest")
        if sum(1 for r in rows if r["kind"] == "interest") >= 8:
            break
    local_places = [p for p in places if p][:3]
    if local_places:
        for place in local_places:
            for template, why_t in _LOCAL_TEMPLATES:
                add(template.format(place=place), why_t.format(place=place), "local")
                if sum(1 for r in rows if r["kind"] == "local") >= 8:
                    break
            if sum(1 for r in rows if r["kind"] == "local") >= 8:
                break
    else:
        for query, why in _GENERIC_LOCAL:
            add(query, why, "local")
    return rows


def found_groups_from_lots(lots: list[Lot], places: list[str] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for lot in lots:
        try:
            payload = json.loads(lot.audience_json or "")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        for group in payload.get("groups") or []:
            if not isinstance(group, dict):
                continue
            name = str(group.get("name") or "").strip()
            if not name:
                continue
            url = str(group.get("url") or "").strip()
            why = str(group.get("why") or "").strip()
            key = (name.lower(), url.lower())
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "name": name,
                    "url": url,
                    "why": why,
                    "kind": classify_group_kind(name, why, url, places),
                    "lot_id": lot.id,
                    "sku": (lot.sku or "").strip() or None,
                }
            )
    rows.sort(key=lambda row: 1 if row.get("kind") == "local" else 0)
    return rows


def sale_facebook_groups_payload(
    *,
    sale: Sale,
    account: Account | None,
    lots: list[Lot],
    hunt_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    primary, places = location_for_account_sale(account, sale)
    groups = found_groups_from_lots(lots, places)
    themes = collect_sale_themes(lots)
    searches = fallback_group_searches(places=places, themes=themes)
    hunted = sum(1 for lot in lots if (lot.audience_json or "").strip())
    return {
        "sale_id": sale.id,
        "sale_name": sale.name or "",
        "location": primary,
        "locations": places,
        "groups": groups,
        "searches": searches,
        "hunted_lots": hunted,
        "lot_count": len(lots),
        "tips": GROUP_POSTING_TIPS,
        "hunt": hunt_status
        or {
            "active": False,
            "total": 0,
            "done": 0,
            "current_lot_id": None,
            "current_sku": None,
            "current_phase": None,
            "lots": [],
        },
    }
