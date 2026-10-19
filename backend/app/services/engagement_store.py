"""Persist Watcher engagement snapshots and build the Push queue."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import EngagementSnapshot, Lot, Photo
from app.schemas import (
    EngagementIngestOut,
    EngagementItemIn,
    EngagementOut,
    EngagementQueueItem,
    EngagementSaleOut,
    EngagementSaleSummary,
    EngagementSnapshotIn,
    SocialCalendarPost,
)
from app.services.engagement import (
    assign_push_ranks,
    build_social_calendar,
    display_bucket,
    reading_dict,
    score_item,
)
from app.services.photo_order import listing_photo_sort_key
from app.services.photo_roles import photos_for_ctbids_listing
from app.services.sale_zip_export import pick_lot_hero_photo


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def engagement_payload_for_lot(lot: Lot) -> dict | None:
    raw = getattr(lot, "engagement_json", None) or ""
    if not str(raw).strip() or str(raw).strip() == "{}":
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not str(data.get("bucket") or "").strip():
        return None
    data["bucket"] = display_bucket(
        str(data.get("bucket") or ""),
        watchers=int(data.get("watchers") or 0),
        bids=int(data.get("bids") or 0),
    )
    return data


def engagement_out_for_lot(lot: Lot) -> EngagementOut | None:
    data = engagement_payload_for_lot(lot)
    if not data:
        return None
    out = EngagementOut.model_validate(data)
    from sqlalchemy.orm import object_session

    from app.services.click_tracking import click_counts_by_lot

    sess = object_session(lot)
    if sess is not None and lot.id:
        out.facebook_clicks = click_counts_by_lot(sess, lot.account_id, [lot.id]).get(lot.id, 0)
    return out


def _match_lot(
    db: Session,
    *,
    account_id: str,
    item_id: int,
    sku: str,
    ctseller_sale_id: int,
) -> Lot | None:
    by_item = (
        db.query(Lot)
        .filter(Lot.account_id == account_id, Lot.ctseller_item_id == int(item_id))
        .order_by(Lot.updated_at.desc())
        .first()
    )
    if by_item:
        return by_item
    token = (sku or "").strip()
    if not token:
        return None
    candidates = (
        db.query(Lot)
        .filter(Lot.account_id == account_id, func.lower(Lot.sku) == token.lower())
        .all()
    )
    if not candidates:
        return None
    for lot in candidates:
        if lot.ctseller_sale_id == int(ctseller_sale_id):
            return lot
    if len(candidates) == 1:
        return candidates[0]
    return None


def _listing_photo_ids(db: Session, account_id: str, lot_id: str, limit: int = 4) -> list[str]:
    photos = (
        db.query(Photo)
        .filter(Photo.account_id == account_id, Photo.lot_id == lot_id)
        .all()
    )
    if not photos:
        return []
    hero = pick_lot_hero_photo(photos)
    ordered = sorted(photos_for_ctbids_listing(photos), key=listing_photo_sort_key)
    ids: list[str] = []
    if hero is not None and hero.id:
        ids.append(hero.id)
    for photo in ordered:
        if photo.id and photo.id not in ids:
            ids.append(photo.id)
        if len(ids) >= limit:
            break
    return ids[:limit]


def _write_lot_engagement(lot: Lot, row: EngagementSnapshot) -> None:
    payload = {
        "views": row.views,
        "watchers": row.watchers,
        "bids": row.bids,
        "price": row.price,
        "reserve": row.reserve,
        "item_url": row.item_url,
        "captured_at": row.captured_at.isoformat() if row.captured_at else None,
        "bucket": display_bucket(row.bucket or "", watchers=row.watchers, bids=row.bids),
        "engagement_score": row.engagement_score,
        "momentum": row.momentum,
        "notes": row.notes,
        "late_surge": row.late_surge,
        "trajectory": row.trajectory,
        "rescue_candidate": bool(row.rescue_candidate),
        "push_rank": row.push_rank,
        "ctseller_sale_id": row.ctseller_sale_id,
        "ctseller_item_id": row.ctseller_item_id,
    }
    lot.engagement_json = json.dumps(payload)


def _clear_lot_engagement(db: Session, account_id: str, lot_id: str | None) -> None:
    if not lot_id:
        return
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
    if lot is not None:
        lot.engagement_json = "{}"


def apply_engagement_snapshot(
    db: Session,
    *,
    account_id: str,
    payload: EngagementSnapshotIn,
) -> EngagementIngestOut:
    captured = payload.captured_at or _utcnow()
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    sale_id = int(payload.ctseller_sale_id)
    deduped: dict[int, EngagementItemIn] = {}
    for item in payload.items:
        deduped[int(item.ctseller_item_id)] = item
    items = list(deduped.values())
    existing = (
        db.query(EngagementSnapshot)
        .filter(
            EngagementSnapshot.account_id == account_id,
            EngagementSnapshot.ctseller_sale_id == sale_id,
        )
        .all()
    )
    by_item = {int(row.ctseller_item_id): row for row in existing}
    incoming_ids = set(deduped)
    for row in existing:
        if int(row.ctseller_item_id) not in incoming_ids:
            _clear_lot_engagement(db, account_id, row.lot_id)
            db.delete(row)
            by_item.pop(int(row.ctseller_item_id), None)

    scored_items: list[tuple[EngagementItemIn, object]] = []
    readings = []
    for item in items:
        reading = score_item(
            views=item.views,
            watchers=item.watchers,
            bids=item.bids,
            price=item.price,
            reserve=item.reserve,
        )
        scored_items.append((item, reading))
        readings.append(reading)
    ranked = assign_push_ranks(readings)
    matched = 0
    seen_lots: set[str] = set()
    for (item, _reading), reading in zip(scored_items, ranked):
        row = by_item.get(int(item.ctseller_item_id))
        if row is None:
            row = EngagementSnapshot(
                account_id=account_id,
                ctseller_sale_id=sale_id,
                ctseller_item_id=int(item.ctseller_item_id),
            )
            db.add(row)
        lot = _match_lot(
            db,
            account_id=account_id,
            item_id=int(item.ctseller_item_id),
            sku=item.sku,
            ctseller_sale_id=sale_id,
        )
        if row.lot_id and (lot is None or lot.id != row.lot_id):
            _clear_lot_engagement(db, account_id, row.lot_id)
        row.sku = (item.sku or "")[:80]
        row.title = (item.title or "")[:500]
        row.item_url = (item.item_url or "")[:600]
        row.item_status = (item.item_status or "")[:40]
        row.views = reading.views
        row.watchers = reading.watchers
        row.bids = reading.bids
        row.price = reading.price
        row.reserve = reading.reserve
        row.bucket = reading.bucket
        row.engagement_score = reading.engagement_score
        row.momentum = reading.momentum
        row.notes = reading.notes
        row.late_surge = reading.late_surge
        row.trajectory = reading.trajectory
        row.rescue_candidate = reading.rescue_candidate
        row.push_rank = reading.push_rank
        row.sale_title = (payload.sale_title or "")[:255]
        row.auction_url = (payload.auction_url or "")[:600]
        row.sale_ends_at = payload.sale_ends_at
        row.captured_at = captured
        row.lot_id = lot.id if lot is not None else None
        if lot is not None and lot.id not in seen_lots:
            _write_lot_engagement(lot, row)
            seen_lots.add(lot.id)
            matched += 1
    db.commit()
    try:
        from app.services.facebook_calendar import sync_facebook_calendar_after_snapshot

        sync_facebook_calendar_after_snapshot(db, account_id, sale_id)
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "Facebook calendar sync after snapshot failed account=%s sale=%s",
            account_id,
            sale_id,
        )
    counts = _bucket_counts(ranked)
    return EngagementIngestOut(
        ctseller_sale_id=sale_id,
        item_count=len(items),
        matched_lots=matched,
        **counts,
    )


def _bucket_counts(rows) -> dict[str, int]:
    counts = {
        "no_interest": 0,
        "needs_eyes": 0,
        "rescue": 0,
        "push": 0,
        "protect": 0,
        "let_ride": 0,
    }
    for row in rows:
        raw = getattr(row, "bucket", None) or (row.get("bucket") if isinstance(row, dict) else "")
        watchers = getattr(row, "watchers", None)
        if watchers is None and isinstance(row, dict):
            watchers = row.get("watchers")
        bids = getattr(row, "bids", None)
        if bids is None and isinstance(row, dict):
            bids = row.get("bids")
        bucket = display_bucket(raw or "", watchers=int(watchers or 0), bids=int(bids or 0))
        if bucket == "No interest":
            counts["no_interest"] += 1
            counts["rescue"] += 1
        elif bucket == "Needs eyes":
            counts["needs_eyes"] += 1
            counts["rescue"] += 1
        elif bucket == "Push":
            counts["push"] += 1
        elif bucket == "Protect":
            counts["protect"] += 1
        elif bucket == "Let Ride":
            counts["let_ride"] += 1
    return counts


def _rows_for_sale(db: Session, account_id: str, ctseller_sale_id: int) -> list[EngagementSnapshot]:
    return (
        db.query(EngagementSnapshot)
        .filter(
            EngagementSnapshot.account_id == account_id,
            EngagementSnapshot.ctseller_sale_id == int(ctseller_sale_id),
        )
        .all()
    )


def list_engagement_sales(db: Session, account_id: str) -> list[EngagementSaleSummary]:
    rows = (
        db.query(EngagementSnapshot)
        .filter(EngagementSnapshot.account_id == account_id)
        .all()
    )
    grouped: dict[int, list[EngagementSnapshot]] = {}
    for row in rows:
        grouped.setdefault(int(row.ctseller_sale_id), []).append(row)
    summaries: list[EngagementSaleSummary] = []
    for sale_id, group in grouped.items():
        latest = max(group, key=lambda row: row.captured_at or datetime.min.replace(tzinfo=timezone.utc))
        counts = _bucket_counts(group)
        summaries.append(
            EngagementSaleSummary(
                ctseller_sale_id=sale_id,
                sale_title=latest.sale_title or "",
                auction_url=latest.auction_url or "",
                sale_ends_at=latest.sale_ends_at,
                captured_at=latest.captured_at,
                item_count=len(group),
                **counts,
            )
        )
    summaries.sort(key=lambda row: row.captured_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return summaries


def _queue_item(
    db: Session,
    account_id: str,
    row: EngagementSnapshot,
    *,
    facebook_clicks: int = 0,
) -> EngagementQueueItem:
    photo_ids = _listing_photo_ids(db, account_id, row.lot_id) if row.lot_id else []
    return EngagementQueueItem(
        sku=row.sku or "",
        title=row.title or "",
        item_status=row.item_status or "",
        lot_id=row.lot_id,
        has_thumbnail=bool(photo_ids),
        photo_ids=photo_ids,
        views=row.views,
        watchers=row.watchers,
        bids=row.bids,
        price=row.price,
        reserve=row.reserve,
        item_url=row.item_url or "",
        captured_at=row.captured_at,
        bucket=display_bucket(row.bucket or "", watchers=row.watchers, bids=row.bids),
        engagement_score=row.engagement_score or "",
        momentum=row.momentum or "",
        notes=row.notes or "",
        late_surge=row.late_surge or "",
        trajectory=row.trajectory or "",
        rescue_candidate=bool(row.rescue_candidate),
        push_rank=row.push_rank,
        ctseller_sale_id=row.ctseller_sale_id,
        ctseller_item_id=row.ctseller_item_id,
        facebook_clicks=int(facebook_clicks or 0),
    )


def engagement_sale_detail(db: Session, account_id: str, ctseller_sale_id: int) -> EngagementSaleOut | None:
    rows = _rows_for_sale(db, account_id, ctseller_sale_id)
    if not rows:
        return None
    latest = max(rows, key=lambda row: row.captured_at or datetime.min.replace(tzinfo=timezone.utc))
    from app.services.click_tracking import click_counts_by_lot

    click_map = click_counts_by_lot(
        db,
        account_id,
        [str(row.lot_id) for row in rows if row.lot_id],
    )
    items = [
        _queue_item(db, account_id, row, facebook_clicks=click_map.get(str(row.lot_id or ""), 0))
        for row in rows
    ]
    items.sort(key=lambda item: (item.push_rank is None, item.push_rank or 0, item.sku))
    counts = _bucket_counts(rows)
    return EngagementSaleOut(
        ctseller_sale_id=int(ctseller_sale_id),
        sale_title=latest.sale_title or "",
        auction_url=latest.auction_url or "",
        sale_ends_at=latest.sale_ends_at,
        captured_at=latest.captured_at,
        item_count=len(rows),
        items=items,
        **counts,
    )


def engagement_calendar(db: Session, account_id: str, ctseller_sale_id: int) -> list[SocialCalendarPost] | None:
    detail = engagement_sale_detail(db, account_id, ctseller_sale_id)
    if detail is None:
        return None
    push_rows = []
    needs_eyes_rows = []
    for item in detail.items:
        payload = {
            "push_rank": item.push_rank,
            "sku": item.sku,
            "title": item.title,
            "price": item.price,
            "watchers": item.watchers,
            "item_url": item.item_url,
            "lot_id": item.lot_id,
            "photo_ids": item.photo_ids,
            "bucket": item.bucket,
        }
        if item.bucket == "Push":
            push_rows.append(payload)
        elif item.bucket == "Needs eyes":
            needs_eyes_rows.append(payload)
    posts = build_social_calendar(
        push_rows,
        needs_eyes_rows=needs_eyes_rows,
        sale_title=detail.sale_title,
        captured_at=detail.captured_at,
        sale_ends_at=detail.sale_ends_at,
        auction_url=detail.auction_url,
        ctseller_sale_id=int(ctseller_sale_id),
    )
    from app.services.facebook_calendar import annotate_social_calendar

    annotated = annotate_social_calendar(db, account_id, int(ctseller_sale_id), posts)
    return [SocialCalendarPost.model_validate(post) for post in annotated]


def rewrite_stored_rescue_buckets(conn) -> int:
    """Split stored ``Rescue`` rows into No interest / Needs eyes. Returns update count."""
    from sqlalchemy import text

    updated = 0
    try:
        rows = conn.execute(
            text(
                "SELECT id, bucket, watchers, bids FROM engagement_snapshots "
                "WHERE bucket = 'Rescue'"
            )
        ).fetchall()
    except Exception:
        rows = []
    for row_id, bucket, watchers, bids in rows:
        mapped = display_bucket(bucket or "", watchers=int(watchers or 0), bids=int(bids or 0))
        if mapped == (bucket or ""):
            continue
        conn.execute(
            text("UPDATE engagement_snapshots SET bucket = :bucket WHERE id = :id"),
            {"bucket": mapped, "id": row_id},
        )
        updated += 1
    try:
        lots = conn.execute(
            text("SELECT id, engagement_json FROM lots WHERE engagement_json LIKE '%Rescue%'")
        ).fetchall()
    except Exception:
        lots = []
    for lot_id, raw in lots:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or str(data.get("bucket") or "") != "Rescue":
            continue
        data["bucket"] = display_bucket(
            "Rescue",
            watchers=int(data.get("watchers") or 0),
            bids=int(data.get("bids") or 0),
        )
        conn.execute(
            text("UPDATE lots SET engagement_json = :payload WHERE id = :id"),
            {"payload": json.dumps(data), "id": lot_id},
        )
        updated += 1
    return updated


def reading_for_tests(item: EngagementItemIn) -> dict:
    """Helper kept for callers that want the scored dict without a database."""
    reading = score_item(
        views=item.views,
        watchers=item.watchers,
        bids=item.bids,
        price=item.price,
        reserve=item.reserve,
    )
    return reading_dict(reading)
