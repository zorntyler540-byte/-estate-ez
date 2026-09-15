"""Which CT Bids auction a posted lot is on, and whether it sold."""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.models import Lot
from app.services.ctseller import CTSellerClient, CTSellerError

OUTCOME_SOLD = "sold"
OUTCOME_UNSOLD = "unsold"
OUTCOME_LIVE = "live"
OUTCOME_PULLED = "pulled"
OUTCOME_UNKNOWN = "unknown"

_SOLD = frozenset(
    {"sold", "soldonline", "sold online", "sold-in-house", "paid", "invoiced", "awarded"}
)
_UNSOLD = frozenset(
    {
        "unsold",
        "no sale",
        "nosale",
        "did not sell",
        "didn't sell",
        "not sold",
        "notsold",
        "reserve not met",
        "passed",
        "passed in",
    }
)
_CLOSED = frozenset({"closed", "complete", "completed", "ended", "finished"})
_PULLED = frozenset({"pulled", "withdrawn", "deleted", "removed", "cancelled", "canceled"})
_LIVE = frozenset({"pending", "ready", "live", "started", "approved", "active", "open", "published"})


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


def parse_item_status(detail: dict[str, Any] | None) -> str | None:
    if not isinstance(detail, dict):
        return None
    raw = detail.get("itemstatus")
    if raw is None:
        raw = detail.get("itemStatus") or detail.get("status")
    text = str(raw or "").strip()
    return text[:40] if text else None


def parse_item_reserve_price(detail: dict[str, Any] | None) -> tuple[bool, float | None]:
    """CT Seller ``reserveprice`` when the field is present. Missing field → ``(False, None)``."""
    if not isinstance(detail, dict):
        return False, None
    raw = None
    present = False
    for key in ("reserveprice", "reservePrice", "reserve_price"):
        if key in detail:
            raw = detail.get(key)
            present = True
            break
    if not present:
        return False, None
    if raw in (None, "", "null", "None"):
        return True, None
    try:
        n = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return True, None
    return True, n if n > 0 else None


def _same_money(left: float | None, right: float | None) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    try:
        return abs(float(left) - float(right)) < 0.0001
    except (TypeError, ValueError):
        return False


def parse_sold_price(detail: dict[str, Any] | None, *, status: str | None = None) -> float | None:
    """Hammer price only when CT Seller reports the lot sold (not a live current bid)."""
    if not isinstance(detail, dict):
        return None
    outcome = normalize_ctseller_outcome(status or parse_item_status(detail), None)
    keys = ("soldprice", "winningbid", "hammerprice")
    if outcome == OUTCOME_SOLD:
        keys = keys + ("currentbid", "highbid")
    for key in keys:
        raw = detail.get(key)
        if raw in (None, "", "null"):
            continue
        try:
            n = float(raw)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return None


def _compact_status_set(values: frozenset[str]) -> set[str]:
    return {x.replace(" ", "") for x in values}


def normalize_ctseller_outcome(status: str | None, sold_price: float | None = None) -> str:
    """Map a CT Seller ``itemstatus`` (+ optional hammer) to sold / unsold / live / pulled."""
    s = " ".join(str(status or "").strip().lower().replace("_", " ").replace("-", " ").split())
    compact = s.replace(" ", "")
    if compact in _compact_status_set(_PULLED):
        return OUTCOME_PULLED
    if compact in _compact_status_set(_SOLD) or s.startswith("sold"):
        return OUTCOME_SOLD
    if compact in _compact_status_set(_UNSOLD) or "unsold" in s or s == "no sale":
        return OUTCOME_UNSOLD
    if sold_price is not None and sold_price > 0:
        return OUTCOME_SOLD
    if compact in _compact_status_set(_CLOSED):
        return OUTCOME_UNSOLD
    if compact in _compact_status_set(_LIVE):
        return OUTCOME_LIVE
    if s:
        return OUTCOME_UNKNOWN
    return OUTCOME_UNKNOWN


def apply_item_detail_to_lot(lot: Lot, detail: dict[str, Any]) -> bool:
    """Copy sale id / status / hammer / reserve from a CT Seller item row. Returns True if changed."""
    changed = False
    sid = parse_item_sale_id(detail)
    if sid and getattr(lot, "ctseller_sale_id", None) != sid:
        lot.ctseller_sale_id = sid
        changed = True
    status = parse_item_status(detail)
    if status and getattr(lot, "ctseller_item_status", None) != status:
        lot.ctseller_item_status = status
        changed = True
    price = parse_sold_price(detail, status=status)
    if price is not None and getattr(lot, "ctseller_sold_price", None) != price:
        lot.ctseller_sold_price = price
        changed = True
    if price is None and normalize_ctseller_outcome(status, None) == OUTCOME_UNSOLD:
        if getattr(lot, "ctseller_sold_price", None) is not None:
            lot.ctseller_sold_price = None
            changed = True
    reserve_present, reserve = parse_item_reserve_price(detail)
    if reserve_present:
        current = getattr(lot, "reserve_price", None)
        try:
            current_n = float(current) if current is not None else None
        except (TypeError, ValueError):
            current_n = None
        if current_n is not None and current_n <= 0:
            current_n = None
        if not _same_money(current_n, reserve):
            lot.reserve_price = reserve
            changed = True
    return changed


def _item_id(lot: Lot) -> int | None:
    try:
        n = int(lot.ctseller_item_id) if lot.ctseller_item_id is not None else 0
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def refresh_lot_ctseller_sale_ids(
    db: Session,
    lots: list[Lot],
    client: CTSellerClient,
    extra_sale_ids: list[int] | None = None,
) -> tuple[int, int]:
    """Refresh CT Bids auction id, item status, sold price, and reserve for posted lots."""
    posted = [lot for lot in lots if _item_id(lot)]
    if not posted:
        return 0, 0
    by_item: dict[int, dict[str, Any]] = {}
    sale_ids = {
        int(sid)
        for lot in posted
        if (sid := getattr(lot, "ctseller_sale_id", None)) not in (None, 0, "")
    }
    for raw in extra_sale_ids or []:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n > 0:
            sale_ids.add(n)
    for sid in sale_ids:
        try:
            for row in client.list_sale_items(int(sid)):
                try:
                    iid = int(row.get("id"))
                except (TypeError, ValueError, AttributeError):
                    continue
                if iid > 0:
                    by_item[iid] = row
        except CTSellerError:
            continue

    listed_includes_reserve = any(parse_item_reserve_price(row)[0] for row in by_item.values())

    looked_up = 0
    failed = 0
    dirty = False
    for lot in posted:
        iid = _item_id(lot)
        if iid is None:
            continue
        detail = by_item.get(iid)
        if detail is None or not listed_includes_reserve:
            try:
                fetched = client.item_detail(iid)
                if isinstance(fetched, dict):
                    detail = {**(detail or {}), **fetched}
            except CTSellerError:
                if detail is None:
                    failed += 1
                    continue
        if not detail:
            failed += 1
            continue
        if apply_item_detail_to_lot(lot, detail):
            dirty = True
        looked_up += 1
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


def posted_outcome_counts(lots: list[Lot]) -> dict[str, int]:
    tallies = {OUTCOME_SOLD: 0, OUTCOME_UNSOLD: 0, OUTCOME_LIVE: 0, OUTCOME_PULLED: 0, OUTCOME_UNKNOWN: 0}
    for lot in lots:
        if not lot.ctseller_item_id and not lot.ctseller_posted_at:
            continue
        key = normalize_ctseller_outcome(
            getattr(lot, "ctseller_item_status", None),
            getattr(lot, "ctseller_sold_price", None),
        )
        tallies[key] = tallies.get(key, 0) + 1
    return tallies
