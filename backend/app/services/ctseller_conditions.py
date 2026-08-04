"""CT Seller item condition tiers (Settings → Item Condition)."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any

import requests

logger = logging.getLogger(__name__)

SELLER_CONDITION_URL = "https://seller.ctbids.com/services/api/v1/static/content/view/condition"

# Live SPA allow-list (GET /static/content/view/condition) — keep in sync as fallback.
CT_SELLER_CONDITIONS: tuple[str, ...] = (
    "NEW",
    "LIKE NEW",
    "VERY GOOD",
    "GOOD",
    "FAIR",
)

CT_SELLER_CONDITION_DESCRIPTIONS: dict[str, str] = {
    "NEW": "Items show no signs of being worn and include tags, packaging, etc.",
    "LIKE NEW": "Items are clearly unused, but tags are not attached, packaging missing, etc.",
    "VERY GOOD": "Items show minor signs of wear. Example: shoes may have light scuffing on the soles, light scratches.",
    "GOOD": "Items show moderate signs of wear, moderate scuffing or scratches",
    "FAIR": "Heavy wear, scratches, etc.",
}

CT_SELLER_CONDITION_DEFAULT = "GOOD"


def canonicalize_ct_condition(raw: str | None) -> str:
    """Map free text / AI labels onto an exact CT Seller condition name."""
    text = (raw or "").strip()
    if not text:
        return CT_SELLER_CONDITION_DEFAULT
    if text in CT_SELLER_CONDITIONS:
        return text
    upper = text.upper().strip()
    if upper in CT_SELLER_CONDITIONS:
        return upper
    # LIKE NEW is two words — normalize spacing before upper compare.
    spaced = re.sub(r"\s+", " ", text).strip().upper()
    if spaced in CT_SELLER_CONDITIONS:
        return spaced

    norm = spaced.lower()
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

    return CT_SELLER_CONDITION_DEFAULT


@lru_cache(maxsize=1)
def fetch_ct_seller_conditions() -> tuple[dict[str, str], ...]:
    """
    Return ``({condition, description}, …)`` from CT Seller, or the built-in fallback.

    Cached for process lifetime — the catalog rarely changes.
    """
    try:
        resp = requests.get(SELLER_CONDITION_URL, timeout=20.0, headers={"User-Agent": "CaringTransitionsCapture/1.0"})
        if resp.status_code < 400:
            body = resp.json()
            rows = body.get("data") if isinstance(body, dict) else None
            if isinstance(rows, list) and rows:
                out: list[dict[str, str]] = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    name = canonicalize_ct_condition(str(row.get("condition") or ""))
                    desc = str(row.get("description") or "").strip() or CT_SELLER_CONDITION_DESCRIPTIONS.get(name, "")
                    if name and not any(x["condition"] == name for x in out):
                        out.append({"condition": name, "description": desc})
                if out:
                    return tuple(out)
    except Exception as exc:  # noqa: BLE001
        logger.warning("CT Seller condition list fetch failed: %s", exc)
    return tuple(
        {"condition": name, "description": CT_SELLER_CONDITION_DESCRIPTIONS.get(name, "")}
        for name in CT_SELLER_CONDITIONS
    )


def ct_condition_rows() -> list[dict[str, Any]]:
    return [dict(row) for row in fetch_ct_seller_conditions()]
