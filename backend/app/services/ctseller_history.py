"""Pull CT Seller sale-history items into Estate-EZ sale analytics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.models import CtSellerHistoryItem, Lot
from app.services.ctseller_posted import (
    apply_item_detail_to_lot,
    normalize_ctseller_outcome,
    parse_item_status,
    parse_sold_price,
)

MAX_ENDED_SALES = 40
MAX_ENDED_SALES_LONG = 120
MAX_ENDED_SALES_LIST = 400
MAX_OPEN_SALES = 8


def _aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class RemoteSale(Protocol):
    id: int
    title: str
    status: str | None
    endtime: str | None


@dataclass(frozen=True)
class HistorySyncResult:
    ended_sales: int
    open_sales: int
    items_upserted: int
    matched_lots: int


def parse_ctseller_datetime(raw: str | None) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[:19] if fmt != "%Y-%m-%d" else text[:10], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware_utc(dt)


def split_history_category(raw: str | None) -> tuple[str, str | None]:
    """CT Seller often sends ``Group#*#Leaf``. Roll up analytics on the group."""
    cat = (raw or "").strip()
    if not cat:
        return "Uncategorized", None
    if "#*#" in cat:
        group, leaf = cat.split("#*#", 1)
        group, leaf = group.strip(), leaf.strip()
        if group and leaf and leaf != group:
            return group[:120], leaf[:120]
        return (leaf or group or "Uncategorized")[:120], None
    return cat[:120], None


def history_item_outcome(row: dict[str, Any], *, sale_open: bool) -> tuple[str, float]:
    status = parse_item_status(row)
    price = parse_sold_price(row, status=status)
    outcome = normalize_ctseller_outcome(status, price)
    hammer = float(price) if price is not None and float(price) > 0 else 0.0
    if outcome == "sold":
        return "sold", hammer
    if not sale_open and outcome in {"live", "unknown"}:
        return "unsold", 0.0
    if outcome == "unposted":
        return "unknown", 0.0
    return outcome, hammer if outcome == "sold" else 0.0


def upsert_ctseller_history_items(
    db: Session,
    account_id: str,
    *,
    sale: RemoteSale,
    items: list[dict[str, Any]],
    sale_open: bool,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Write one auction's items. Returns (upserted, matched EZ lots)."""
    try:
        sale_id = int(sale.id)
    except (TypeError, ValueError):
        return 0, 0
    if sale_id <= 0:
        return 0, 0
    now_utc = _aware_utc(now or datetime.now(timezone.utc))
    ended_at = parse_ctseller_datetime(getattr(sale, "endtime", None))
    sale_name = (getattr(sale, "title", None) or f"Sale {sale_id}").strip()[:255]
    parsed: list[tuple[int, dict[str, Any]]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        try:
            iid = int(row.get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        if iid > 0:
            parsed.append((iid, row))
    if not parsed:
        return 0, 0

    item_ids = [iid for iid, _ in parsed]
    existing = {
        int(row.ctseller_item_id): row
        for row in db.query(CtSellerHistoryItem)
        .filter(
            CtSellerHistoryItem.account_id == account_id,
            CtSellerHistoryItem.ctseller_item_id.in_(item_ids),
        )
        .all()
    }
    lots = {
        int(lot.ctseller_item_id): lot
        for lot in db.query(Lot)
        .filter(Lot.account_id == account_id, Lot.ctseller_item_id.in_(item_ids))
        .all()
        if lot.ctseller_item_id
    }

    upserted = 0
    matched = 0
    for iid, row in parsed:
        lot = lots.get(iid)
        if lot is not None:
            apply_item_detail_to_lot(lot, row)
            matched += 1
            if not (lot.ct_category or "").strip():
                group, leaf = split_history_category(str(row.get("category") or ""))
                if group and group != "Uncategorized":
                    lot.ct_category = group
                if leaf and not (lot.ct_subcategory or "").strip():
                    lot.ct_subcategory = leaf
        group, leaf = split_history_category(str(row.get("category") or ""))
        status = parse_item_status(row)
        _outcome, hammer = history_item_outcome(row, sale_open=sale_open)
        sku = str(row.get("SKU") or row.get("sku") or "").strip() or None
        title = str(row.get("title") or "")[:500]
        rec = existing.get(iid)
        if rec is None:
            rec = CtSellerHistoryItem(
                account_id=account_id,
                ctseller_item_id=iid,
                ctseller_sale_id=sale_id,
            )
            db.add(rec)
            existing[iid] = rec
        rec.ctseller_sale_id = sale_id
        rec.sale_name = sale_name
        rec.sale_open = sale_open
        rec.sale_ended_at = ended_at
        rec.sku = sku[:80] if sku else None
        rec.title = title
        rec.category = group
        rec.subcategory = leaf
        rec.item_status = (status or None)
        rec.sold_price = hammer if hammer > 0 else None
        rec.lot_id = lot.id if lot is not None else rec.lot_id
        rec.synced_at = now_utc
        upserted += 1
    return upserted, matched


def pull_ctseller_sale_history(
    db: Session,
    account_id: str,
    client: Any,
    start: datetime,
    end: datetime,
) -> HistorySyncResult:
    """Fetch ended (and currently open) CT Seller auctions and store item outcomes."""
    start_utc = _aware_utc(start)
    end_utc = _aware_utc(end)
    span_days = max(0, (end_utc - start_utc).days)
    fetch_cap = MAX_ENDED_SALES_LONG if span_days > 120 else MAX_ENDED_SALES
    ended = list(
        client.list_ended_sales(
            ended_after=start_utc, ended_before=end_utc, limit=MAX_ENDED_SALES_LIST
        )
    )
    already = {
        int(sid)
        for (sid,) in db.query(CtSellerHistoryItem.ctseller_sale_id)
        .filter(CtSellerHistoryItem.account_id == account_id)
        .distinct()
        .all()
        if sid
    }
    pending_ended = []
    for sale in ended:
        try:
            sid = int(sale.id)
        except (TypeError, ValueError, AttributeError):
            continue
        if sid > 0 and sid not in already:
            pending_ended.append(sale)
    pending_ended = pending_ended[:fetch_cap]
    open_sales = list(client.list_open_sales())[:MAX_OPEN_SALES]
    seen: set[int] = set()
    ended_n = 0
    open_n = 0
    upserted = 0
    matched = 0

    def walk(rows: list[Any], *, sale_open: bool) -> None:
        nonlocal upserted, matched, ended_n, open_n
        for sale in rows:
            try:
                sid = int(sale.id)
            except (TypeError, ValueError, AttributeError):
                continue
            if sid <= 0 or sid in seen:
                continue
            seen.add(sid)
            try:
                items = client.list_sale_items(sid)
            except Exception:
                continue
            n, m = upsert_ctseller_history_items(
                db,
                account_id,
                sale=sale,
                items=items if isinstance(items, list) else [],
                sale_open=sale_open,
            )
            upserted += n
            matched += m
            if sale_open:
                open_n += 1
            else:
                ended_n += 1

    walk(pending_ended, sale_open=False)
    window_includes_now = end_utc >= datetime.now(timezone.utc) - timedelta(hours=12)
    if window_includes_now:
        walk(open_sales, sale_open=True)
    return HistorySyncResult(
        ended_sales=ended_n,
        open_sales=open_n,
        items_upserted=upserted,
        matched_lots=matched,
    )


def history_rows_in_window(
    db: Session,
    account_id: str,
    start: datetime,
    end: datetime,
) -> list[CtSellerHistoryItem]:
    start_utc = _aware_utc(start)
    end_utc = _aware_utc(end)
    rows = (
        db.query(CtSellerHistoryItem)
        .filter(CtSellerHistoryItem.account_id == account_id)
        .all()
    )
    include_open = end_utc >= datetime.now(timezone.utc) - timedelta(hours=12)
    out: list[CtSellerHistoryItem] = []
    for row in rows:
        ended = _aware_utc(row.sale_ended_at) if row.sale_ended_at is not None else None
        if ended is not None and start_utc <= ended < end_utc:
            out.append(row)
            continue
        if include_open and row.sale_open:
            out.append(row)
    return out
