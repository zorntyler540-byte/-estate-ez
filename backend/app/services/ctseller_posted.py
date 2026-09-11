"""Which CT Bids auction a posted Estate-EZ lot actually lives on."""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.models import Lot
from app.services.ctseller import CTSellerClient, CTSellerError


def parse_item_sale_id(detail: dict[str, Any] | None) -> int | None:
    if not isinstance(detail, dict):
        return None
    raw = detail.get("saleid")
    if raw is None:
        raw = detail.get("saleId") or detail.get("sale_id")
    try:
        n = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def refresh_lot_ctseller_sale_ids(
    db: Session,
    lots: list[Lot],
    client: CTSellerClient,
) -> tuple[int, int]:
    """Fill ``Lot.ctseller_sale_id`` from CT Seller ``item/detail`` when missing."""
    looked_up = 0
    failed = 0
    dirty = False
    for lot in lots:
        if not lot.ctseller_item_id:
            continue
        if getattr(lot, "ctseller_sale_id", None):
            continue
        try:
            detail = client.item_detail(int(lot.ctseller_item_id))
        except CTSellerError:
            failed += 1
            continue
        sid = parse_item_sale_id(detail)
        if sid is None:
            failed += 1
            continue
        lot.ctseller_sale_id = sid
        looked_up += 1
        dirty = True
    if dirty:
        db.commit()
    return looked_up, failed


def posted_auction_counts(lots: list[Lot]) -> tuple[list[tuple[int, int]], int]:
    """Return ``(sale_id, lot_count)`` pairs plus posted lots with unknown auction."""
    counts: Counter[int] = Counter()
    unknown = 0
    for lot in lots:
        if not lot.ctseller_item_id and not lot.ctseller_posted_at:
            continue
        sid = getattr(lot, "ctseller_sale_id", None)
        try:
            n = int(sid) if sid is not None else 0
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            counts[n] += 1
        else:
            unknown += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked, unknown
