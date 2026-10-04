"""Photos uploaded onto a lot well after its first frames."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Lot, Photo, User
from app.schemas import PhotoAddLotOut, PhotoAddsOut

# Original catalog bursts land within minutes. A later add is a separate upload.
LATE_PHOTO_ADD_GAP = timedelta(hours=2)
PHOTO_ADD_WINDOW_DAYS = 14


def as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = datetime.fromisoformat(text.replace(" ", "T", 1))
        value = parsed
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def added_later_photo_ids(photos: list) -> set[str]:
    """Ids whose created_at is at least LATE_PHOTO_ADD_GAP after the earliest photo."""
    stamped: list[tuple[str, datetime]] = []
    for photo in photos:
        created = getattr(photo, "created_at", None)
        photo_id = getattr(photo, "id", None)
        if not photo_id or created is None:
            continue
        stamped.append((str(photo_id), as_utc(created)))
    if len(stamped) < 2:
        return set()
    earliest = min(when for _, when in stamped)
    cutoff = earliest + LATE_PHOTO_ADD_GAP
    return {photo_id for photo_id, when in stamped if when >= cutoff}


def recent_photo_adds(
    db: Session,
    account_id: str,
    sale_id: str,
    *,
    now: datetime | None = None,
    window_days: int = PHOTO_ADD_WINDOW_DAYS,
) -> PhotoAddsOut:
    """Lots on this sale that gained photos in the window, after the original set."""
    moment = as_utc(now or datetime.now(timezone.utc))
    window_start = moment - timedelta(days=window_days)
    span_rows = (
        db.query(Photo.lot_id, func.min(Photo.created_at), func.max(Photo.created_at))
        .join(Lot, Lot.id == Photo.lot_id)
        .filter(
            Photo.account_id == account_id,
            Lot.account_id == account_id,
            Lot.sale_id == sale_id,
            Photo.lot_id.isnot(None),
        )
        .group_by(Photo.lot_id)
        .all()
    )
    first_by_lot: dict[str, datetime] = {}
    for lot_id, first_at, last_at in span_rows:
        if not lot_id or first_at is None or last_at is None:
            continue
        first = as_utc(first_at)
        last = as_utc(last_at)
        if last < window_start or last < first + LATE_PHOTO_ADD_GAP:
            continue
        first_by_lot[str(lot_id)] = first
    if not first_by_lot:
        return PhotoAddsOut(window_days=window_days, lots=[])

    photo_rows = (
        db.query(Photo.lot_id, Photo.created_at, Photo.user_id)
        .filter(
            Photo.account_id == account_id,
            Photo.lot_id.in_(list(first_by_lot)),
        )
        .all()
    )
    grouped: dict[str, list[tuple[datetime, str | None]]] = {}
    for lot_id, created_at, user_id in photo_rows:
        if not lot_id or created_at is None:
            continue
        lid = str(lot_id)
        when = as_utc(created_at)
        if when < window_start or when < first_by_lot[lid] + LATE_PHOTO_ADD_GAP:
            continue
        grouped.setdefault(lid, []).append((when, str(user_id) if user_id else None))
    if not grouped:
        return PhotoAddsOut(window_days=window_days, lots=[])

    lots = db.query(Lot).filter(Lot.account_id == account_id, Lot.id.in_(list(grouped))).all()
    lot_by_id = {lot.id: lot for lot in lots}
    user_ids = {uid for rows in grouped.values() for _, uid in rows if uid}
    emails: dict[str, str] = {}
    if user_ids:
        for user in db.query(User).filter(User.id.in_(list(user_ids))).all():
            emails[user.id] = user.email

    out: list[PhotoAddLotOut] = []
    for lid, rows in grouped.items():
        lot = lot_by_id.get(lid)
        if lot is None:
            continue
        counts: dict[str, int] = {}
        latest = rows[0][0]
        for when, uid in rows:
            if when > latest:
                latest = when
            if uid and uid in emails:
                counts[emails[uid]] = counts.get(emails[uid], 0) + 1
        added_by = sorted(counts, key=lambda email: (-counts[email], email.lower()))
        out.append(
            PhotoAddLotOut(
                lot_id=lid,
                sale_id=lot.sale_id,
                sku=lot.sku,
                auction_title=lot.auction_title or "",
                added_count=len(rows),
                latest_added_at=latest,
                added_by=added_by,
            )
        )
    out.sort(key=lambda row: (row.latest_added_at, row.lot_id), reverse=True)
    return PhotoAddsOut(window_days=window_days, lots=out)
