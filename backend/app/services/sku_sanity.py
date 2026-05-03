"""Detect SKU issues within a sale: duplicate tape digits and mixed lot numbers in listing text."""

from __future__ import annotations

import json
import re
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models import Lot

_EXPLICIT_LOT_PATTERNS = [
    r"(?:SKU|Sku)\s*#?\s*(\d{2,12})",
    r"\(\s*SKU\s+(\d{2,12})\s*\)",
    r"(?:lot\s*#|Lot\s*#|LOT\s*#)\s*(\d{2,12})",
    r"estate\s*lot\s*#?\s*(\d{2,12})",
    r"lot\s+(?:number|#|no\.?)\s*(\d{2,12})",
]


def sku_digits_from_string(value: str | None) -> str | None:
    if not value:
        return None
    d = re.sub(r"\D", "", str(value))
    return d or None


def combined_text_from_lot_row(lot: Lot) -> str:
    parts = [
        str(lot.auction_title or ""),
        str(lot.professional_description or ""),
        str(lot.condition_text or ""),
    ]
    for key in ("specification_lines_json", "brands_json", "labels_and_tags_json"):
        raw = getattr(lot, key, None) or "[]"
        try:
            v = json.loads(raw)
        except (TypeError, ValueError):
            v = []
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif isinstance(v, str):
            parts.append(v)
    return "\n".join(parts)


def extract_explicit_lot_numbers(text: str) -> set[str]:
    out: set[str] = set()
    for pat in _EXPLICIT_LOT_PATTERNS:
        for m in re.finditer(pat, text, re.I):
            digits = re.sub(r"\D", "", m.group(1))
            if digits:
                out.add(digits)
    return out


def _numeric_dedupe(digit_strings: set[str]) -> set[int]:
    return {int(s) for s in digit_strings if s.isdigit()}


def lot_has_sku_text_mismatch(lot: Lot) -> bool:
    """
    True when canonical SKU digits and explicit lot-number phrases in text imply
    more than one distinct numeric lot identity (same heuristic as ``analyze_lot_sku_consistency.py``).
    """
    canon = sku_digits_from_string(lot.sku)
    mentioned = extract_explicit_lot_numbers(combined_text_from_lot_row(lot))
    variant_digits: set[str] = set(mentioned)
    if canon:
        variant_digits.add(canon)
    return len(_numeric_dedupe(variant_digits)) >= 2


def recompute_sku_sanity_flags_for_sale(db: Session, sale_id: str, account_id: str) -> None:
    """Refresh ``sku_duplicate_in_sale`` and ``sku_text_mismatch`` for every lot in the sale."""
    lots = (
        db.query(Lot)
        .filter(Lot.sale_id == sale_id, Lot.account_id == account_id)
        .order_by(Lot.created_at.asc())
        .all()
    )
    if not lots:
        return
    by_digits: dict[str, list[Lot]] = defaultdict(list)
    for lot in lots:
        d = sku_digits_from_string(lot.sku)
        if d:
            by_digits[d].append(lot)
    dup_ids = {x.id for d, lst in by_digits.items() if len(lst) >= 2 for x in lst}
    for lot in lots:
        lot.sku_duplicate_in_sale = lot.id in dup_ids
        lot.sku_text_mismatch = lot_has_sku_text_mismatch(lot)
