"""Owner-facing per-person catalog activity for a time window.

Counts surviving ``photos`` / ``lots`` / ``processing_jobs`` rows stamped with
``user_id`` and ``created_at``. Deleted photos drop off. Credit is the login
that uploaded or started the job, not the person holding the camera.

Posted / sold / unsold / live are lots that login created and that went to
CT Bids in the window (``ctseller_posted_at``, or ``created_at`` when the
item id exists but the post stamp is missing). Outcomes use the current
CT Seller item status, same mapping as the dashboard chips.

Active hours (for photos/hr and lots/hr) use photo and lot timestamps only.
Each UTC calendar day with activity is the span from that person's first
event to their last that day, floored at one hour so a short burst does
not look like an impossible pace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.models import Lot, Photo, ProcessingJob, Sale, User
from app.services.photo_captured_at import captured_at_from_filename

MAX_ACTIVITY_DAYS = 366
MIN_ACTIVE_HOURS_PER_DAY = 1.0
# Photos more than this apart on the same lot are a later add-on, not one shoot.
CAPTURE_SESSION_GAP = timedelta(minutes=15)
MAX_ACTIVITY_LOTS_PER_MEMBER = 80
MAX_SOLD_DOCKET = 400
# Server ingest of a whole lot is often a few seconds. Do not report that as shoot time.
UPLOAD_BURST_MAX = timedelta(seconds=45)


class ActivityWindowError(ValueError):
    """Invalid since/until range for team activity."""


def aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_activity_window(
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    since_days: int = 7,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Half-open window ``[start, end)`` in UTC."""
    now_utc = aware_utc(now or datetime.now(timezone.utc))
    end = aware_utc(until) if until is not None else now_utc
    if since is not None:
        start = aware_utc(since)
    else:
        days = max(1, min(int(since_days), MAX_ACTIVITY_DAYS))
        start = end - timedelta(days=days)
    if start >= end:
        raise ActivityWindowError("Start must be before end")
    if end - start > timedelta(days=MAX_ACTIVITY_DAYS):
        raise ActivityWindowError(f"Range cannot exceed {MAX_ACTIVITY_DAYS} days")
    return start, end


def _counts_by_user(
    db: Session,
    model: Any,
    account_id: str,
    start: datetime,
    end: datetime,
) -> dict[str, int]:
    rows = (
        db.query(model.user_id, func.count())
        .filter(
            model.account_id == account_id,
            model.created_at >= start,
            model.created_at < end,
        )
        .group_by(model.user_id)
        .all()
    )
    return {str(uid): int(n or 0) for uid, n in rows}


def _created_at_bounds_by_user(
    db: Session,
    model: Any,
    account_id: str,
    start: datetime,
    end: datetime,
) -> dict[str, tuple[datetime, datetime]]:
    rows = (
        db.query(model.user_id, func.min(model.created_at), func.max(model.created_at))
        .filter(
            model.account_id == account_id,
            model.created_at >= start,
            model.created_at < end,
        )
        .group_by(model.user_id)
        .all()
    )
    out: dict[str, tuple[datetime, datetime]] = {}
    for uid, first_ts, last_ts in rows:
        if not uid or first_ts is None or last_ts is None:
            continue
        out[str(uid)] = (aware_utc(first_ts), aware_utc(last_ts))
    return out


def _event_times_by_user(
    db: Session,
    account_id: str,
    start: datetime,
    end: datetime,
) -> dict[str, list[datetime]]:
    """Photo + lot ``created_at`` values in the window, grouped by login."""
    out: dict[str, list[datetime]] = {}
    for model in (Photo, Lot):
        rows = (
            db.query(model.user_id, model.created_at)
            .filter(
                model.account_id == account_id,
                model.created_at >= start,
                model.created_at < end,
            )
            .all()
        )
        for uid, ts in rows:
            if not uid or ts is None:
                continue
            out.setdefault(str(uid), []).append(ts)
    return out


def active_hours_for_timestamps(times: list[datetime]) -> float:
    """Sum first-to-last span per UTC day; at least one hour on each active day."""
    if not times:
        return 0.0
    by_day: dict[date, list[datetime]] = {}
    for raw in times:
        ts = aware_utc(raw)
        by_day.setdefault(ts.date(), []).append(ts)
    total = 0.0
    for day_times in by_day.values():
        span_hours = (max(day_times) - min(day_times)).total_seconds() / 3600.0
        total += max(span_hours, MIN_ACTIVE_HOURS_PER_DAY)
    return round(total, 2)


def per_active_hour(count: int, hours: float) -> float:
    if hours <= 0 or count <= 0:
        return 0.0
    return round(count / hours, 2)


def capture_minutes_for_timestamps(
    times: list[datetime],
    gap: timedelta = CAPTURE_SESSION_GAP,
) -> float | None:
    """Minutes from first to last photo in the densest shoot on one lot.

    Prefer camera ``captured_at`` when the caller supplies those stamps.
    A single photo cannot measure duration. Clusters split when the gap
    exceeds ``gap`` so a later ``Add photos`` pass is not one shoot.
    """
    if len(times) < 2:
        return None
    ordered = sorted(aware_utc(t) for t in times)
    clusters: list[list[datetime]] = []
    current = [ordered[0]]
    for ts in ordered[1:]:
        if ts - current[-1] > gap:
            clusters.append(current)
            current = [ts]
        else:
            current.append(ts)
    clusters.append(current)
    densest = max(clusters, key=lambda c: (len(c), (c[-1] - c[0]).total_seconds()))
    if len(densest) < 2:
        return None
    return round((densest[-1] - densest[0]).total_seconds() / 60.0, 2)


def median_minutes(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(median(values)), 2)


def mean_minutes(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def lot_is_posted_to_ctbids(lot: Lot) -> bool:
    return bool(lot.ctseller_item_id or lot.ctseller_posted_at)


def lot_ctbids_outcome(lot: Lot) -> str:
    """Match dashboard sold/unsold/live chips: current CT Seller item status."""
    if not lot_is_posted_to_ctbids(lot):
        return "unposted"
    raw = (lot.ctseller_item_status or "").strip().lower()
    s = " ".join(raw.replace("_", " ").replace("-", " ").split())
    compact = s.replace(" ", "")
    if compact in {"pulled", "withdrawn", "deleted", "removed", "cancelled", "canceled"}:
        return "pulled"
    if compact.startswith("sold") or compact in {"paid", "invoiced", "awarded"}:
        return "sold"
    if (
        "unsold" in compact
        or compact in {"nosale", "notsold"}
        or s in {"no sale", "not sold"}
        or "did not sell" in s
        or "didn't sell" in s
        or "reserve not met" in s
    ):
        return "unsold"
    price = lot.ctseller_sold_price
    if price is not None and float(price) > 0:
        return "sold"
    if compact in {"closed", "complete", "completed", "ended", "finished"}:
        return "unsold"
    if compact in {"pending", "ready", "live", "started", "approved", "active", "open", "published"}:
        return "live"
    return "unknown"


@dataclass(frozen=True)
class PostedLotStats:
    posted: int = 0
    sold: int = 0
    unsold: int = 0
    live: int = 0
    pulled: int = 0
    sold_revenue: float = 0.0


def _empty_posted_stats() -> PostedLotStats:
    return PostedLotStats()


def _posted_lots_in_window(
    db: Session,
    account_id: str,
    start: datetime,
    end: datetime,
) -> list[Lot]:
    """Lots posted to CT Bids in ``[start, end)``.

    Prefer ``ctseller_posted_at``. Legacy rows with an item id but no stamp
    use ``created_at`` so they still show in the window they were cataloged.
    """
    return (
        db.query(Lot)
        .filter(
            Lot.account_id == account_id,
            or_(
                and_(Lot.ctseller_posted_at >= start, Lot.ctseller_posted_at < end),
                and_(
                    Lot.ctseller_posted_at.is_(None),
                    Lot.ctseller_item_id.isnot(None),
                    Lot.created_at >= start,
                    Lot.created_at < end,
                ),
            ),
        )
        .all()
    )


def summarize_posted_lots(lots: list[Lot]) -> PostedLotStats:
    posted = sold = unsold = live = pulled = 0
    revenue = 0.0
    for lot in lots:
        if not lot_is_posted_to_ctbids(lot):
            continue
        posted += 1
        outcome = lot_ctbids_outcome(lot)
        if outcome == "sold":
            sold += 1
            price = lot.ctseller_sold_price
            if price is not None and float(price) > 0:
                revenue += float(price)
        elif outcome == "unsold":
            unsold += 1
        elif outcome == "live":
            live += 1
        elif outcome == "pulled":
            pulled += 1
    return PostedLotStats(
        posted=posted,
        sold=sold,
        unsold=unsold,
        live=live,
        pulled=pulled,
        sold_revenue=round(revenue, 2),
    )


def posted_lot_stats_by_user(
    db: Session,
    account_id: str,
    start: datetime,
    end: datetime,
) -> dict[str, PostedLotStats]:
    out: dict[str, list[Lot]] = {}
    for lot in _posted_lots_in_window(db, account_id, start, end):
        uid = str(lot.user_id) if lot.user_id else ""
        if not uid:
            continue
        out.setdefault(uid, []).append(lot)
    return {uid: summarize_posted_lots(rows) for uid, rows in out.items()}


def posted_lot_stats_for_account(
    db: Session,
    account_id: str,
    start: datetime,
    end: datetime,
) -> PostedLotStats:
    return summarize_posted_lots(_posted_lots_in_window(db, account_id, start, end))


@dataclass(frozen=True)
class SoldDocketItem:
    lot_id: str
    sale_id: str
    sale_name: str
    sku: str | None
    auction_title: str
    category: str
    subcategory: str | None
    outcome: str
    sold_price: float
    posted_at: datetime | None
    user_email: str


@dataclass(frozen=True)
class SoldCategoryRollup:
    category: str
    posted_count: int
    sold_count: int
    unsold_count: int
    live_count: int
    revenue: float
    avg_price: float
    min_price: float
    max_price: float
    sell_through: float


_OUTCOME_RANK = {"sold": 0, "unsold": 1, "live": 2, "pulled": 3, "unknown": 4, "unposted": 5}


def _lot_sold_price(lot: Lot) -> float:
    raw = lot.ctseller_sold_price
    if raw is None:
        return 0.0
    try:
        return round(float(raw), 2)
    except (TypeError, ValueError):
        return 0.0


def build_sold_docket(
    db: Session,
    account_id: str,
    start: datetime,
    end: datetime,
) -> tuple[list[SoldDocketItem], list[SoldCategoryRollup]]:
    """Posted lots in the window with current sold/unsold/live outcome, hammer, category."""
    posted = [lot for lot in _posted_lots_in_window(db, account_id, start, end) if lot_is_posted_to_ctbids(lot)]
    if not posted:
        return [], []

    sale_ids = {lot.sale_id for lot in posted if lot.sale_id}
    sales = (
        db.query(Sale.id, Sale.name).filter(Sale.account_id == account_id, Sale.id.in_(sale_ids)).all()
        if sale_ids
        else []
    )
    sale_name = {str(sid): (name or "").strip() or "Sale" for sid, name in sales}

    user_ids = {str(lot.user_id) for lot in posted if lot.user_id}
    emails = (
        {
            str(uid): email
            for uid, email in db.query(User.id, User.email)
            .filter(User.account_id == account_id, User.id.in_(user_ids))
            .all()
        }
        if user_ids
        else {}
    )

    items: list[SoldDocketItem] = []
    for lot in posted:
        outcome = lot_ctbids_outcome(lot)
        if outcome == "unposted":
            continue
        cat = (lot.ct_category or "").strip() or "Uncategorized"
        sub = (lot.ct_subcategory or "").strip() or None
        posted_at = lot.ctseller_posted_at or lot.created_at
        price = _lot_sold_price(lot) if outcome == "sold" else 0.0
        items.append(
            SoldDocketItem(
                lot_id=lot.id,
                sale_id=lot.sale_id,
                sale_name=sale_name.get(str(lot.sale_id), "Sale"),
                sku=(lot.sku or None),
                auction_title=(lot.auction_title or "")[:160],
                category=cat,
                subcategory=sub,
                outcome=outcome,
                sold_price=price,
                posted_at=aware_utc(posted_at) if posted_at is not None else None,
                user_email=emails.get(str(lot.user_id or ""), ""),
            )
        )
    items.sort(
        key=lambda row: (
            _OUTCOME_RANK.get(row.outcome, 9),
            -row.sold_price,
            (row.sku or "").lower(),
            row.lot_id,
        )
    )

    buckets: dict[str, list[SoldDocketItem]] = {}
    for row in items:
        buckets.setdefault(row.category, []).append(row)
    categories: list[SoldCategoryRollup] = []
    for cat, rows in buckets.items():
        sold_prices = [r.sold_price for r in rows if r.outcome == "sold"]
        sold_n = len(sold_prices)
        posted_n = len(rows)
        revenue = round(sum(sold_prices), 2)
        categories.append(
            SoldCategoryRollup(
                category=cat,
                posted_count=posted_n,
                sold_count=sold_n,
                unsold_count=sum(1 for r in rows if r.outcome == "unsold"),
                live_count=sum(1 for r in rows if r.outcome == "live"),
                revenue=revenue,
                avg_price=round(revenue / sold_n, 2) if sold_n else 0.0,
                min_price=round(min(sold_prices), 2) if sold_prices else 0.0,
                max_price=round(max(sold_prices), 2) if sold_prices else 0.0,
                sell_through=round(sold_n / posted_n, 4) if posted_n else 0.0,
            )
        )
    categories.sort(key=lambda row: (-row.revenue, -row.sold_count, row.category.lower()))
    return items[:MAX_SOLD_DOCKET], categories


@dataclass(frozen=True)
class TeamActivityPhotoTime:
    at: datetime
    source: str


@dataclass(frozen=True)
class TeamActivityLot:
    lot_id: str
    sale_id: str
    sku: str | None
    auction_title: str
    photo_count: int
    capture_minutes: float | None
    first_photo_at: datetime
    last_photo_at: datetime
    has_thumbnail: bool
    timing_source: str = "upload"
    photo_times: tuple[TeamActivityPhotoTime, ...] = ()


def _photographed_lots_by_user(
    db: Session,
    account_id: str,
    start: datetime,
    end: datetime,
    *,
    include_lots: bool,
) -> dict[str, tuple[list[TeamActivityLot], list[float], int]]:
    """Lots this login actually photographed in the window.

    Returns user_id -> (lots slice for the UI, all capture-minute samples, total lot count).
    """
    rows = (
        db.query(
            Photo.user_id,
            Photo.lot_id,
            Photo.created_at,
            Photo.captured_at,
            Photo.original_name,
        )
        .filter(
            Photo.account_id == account_id,
            Photo.lot_id.isnot(None),
            Photo.created_at >= start,
            Photo.created_at < end,
        )
        .all()
    )
    times_by_user_lot: dict[tuple[str, str], list[tuple[datetime, datetime | None, str]]] = {}
    for uid, lot_id, created, captured, original_name in rows:
        if not uid or not lot_id or created is None:
            continue
        times_by_user_lot.setdefault((str(uid), str(lot_id)), []).append(
            (created, captured, str(original_name or ""))
        )
    if not times_by_user_lot:
        return {}

    lot_ids = {lot_id for _, lot_id in times_by_user_lot}
    lots = (
        db.query(Lot)
        .filter(Lot.account_id == account_id, Lot.id.in_(lot_ids))
        .all()
    )
    lot_by_id = {row.id: row for row in lots}

    grouped: dict[str, list[TeamActivityLot]] = {}
    minutes_by_user: dict[str, list[float]] = {}
    for (uid, lot_id), stamps in times_by_user_lot.items():
        lot = lot_by_id.get(lot_id)
        if lot is None:
            continue
        resolved: list[tuple[datetime, str]] = []
        uploaded = [aware_utc(created) for created, _cap, _name in stamps]
        for created, captured, original_name in stamps:
            if captured is not None:
                resolved.append((aware_utc(captured), "camera"))
                continue
            inferred = captured_at_from_filename(Path(original_name or "x.jpg"))
            if inferred is not None:
                resolved.append((inferred, "camera"))
            else:
                resolved.append((aware_utc(created), "upload"))
        camera = [ts for ts, src in resolved if src == "camera"]
        uploaded = sorted(uploaded)
        if camera:
            ordered = sorted(camera)
            photo_times = tuple(TeamActivityPhotoTime(at=ts, source="camera") for ts in ordered)
            source = "camera"
            mins = capture_minutes_for_timestamps(ordered)
        else:
            ordered = uploaded
            photo_times = ()
            mins = capture_minutes_for_timestamps(uploaded)
            source = "upload"
            if (
                mins is not None
                and len(uploaded) >= 2
                and (uploaded[-1] - uploaded[0]) <= UPLOAD_BURST_MAX
            ):
                source = "upload_burst"
                mins = None
        if mins is not None and source == "camera":
            minutes_by_user.setdefault(uid, []).append(mins)
        grouped.setdefault(uid, []).append(
            TeamActivityLot(
                lot_id=lot.id,
                sale_id=lot.sale_id,
                sku=(lot.sku or None),
                auction_title=(lot.auction_title or "")[:120],
                photo_count=len(stamps),
                capture_minutes=mins,
                first_photo_at=ordered[0],
                last_photo_at=ordered[-1],
                has_thumbnail=True,
                timing_source=source,
                photo_times=photo_times,
            )
        )

    out: dict[str, tuple[list[TeamActivityLot], list[float], int]] = {}
    for uid, lot_rows in grouped.items():
        lot_rows.sort(key=lambda r: r.last_photo_at, reverse=True)
        total = len(lot_rows)
        shown = lot_rows[:MAX_ACTIVITY_LOTS_PER_MEMBER] if include_lots else []
        out[uid] = (shown, minutes_by_user.get(uid, []), total)
    return out


@dataclass(frozen=True)
class TeamActivityMember:
    user_id: str
    email: str
    role: str
    photos_uploaded: int
    lots_created: int
    jobs_started: int
    active_hours: float
    photos_per_hour: float
    lots_per_hour: float
    lots_posted: int = 0
    lots_sold: int = 0
    lots_unsold: int = 0
    lots_live: int = 0
    lots_pulled: int = 0
    sold_revenue: float = 0.0
    first_photo_at: datetime | None = None
    last_photo_at: datetime | None = None
    last_lot_at: datetime | None = None
    lots_photographed: int = 0
    median_minutes_per_lot: float | None = None
    mean_minutes_per_lot: float | None = None
    lots: list[TeamActivityLot] = field(default_factory=list)


@dataclass(frozen=True)
class TeamActivitySummary:
    since: datetime
    until: datetime
    members: list[TeamActivityMember]
    photos_uploaded: int
    lots_created: int
    jobs_started: int
    active_hours: float
    photos_per_hour: float
    lots_per_hour: float
    lots_posted: int = 0
    lots_sold: int = 0
    lots_unsold: int = 0
    lots_live: int = 0
    lots_pulled: int = 0
    sold_revenue: float = 0.0
    lots_photographed: int = 0
    median_minutes_per_lot: float | None = None
    mean_minutes_per_lot: float | None = None
    sold_items: list[SoldDocketItem] = field(default_factory=list)
    sold_by_category: list[SoldCategoryRollup] = field(default_factory=list)


def summarize_team_activity(
    db: Session,
    account_id: str,
    start: datetime,
    end: datetime,
    *,
    include_lots: bool = True,
) -> TeamActivitySummary:
    start = aware_utc(start)
    end = aware_utc(end)
    users = (
        db.query(User)
        .filter(User.account_id == account_id)
        .order_by(User.email.asc())
        .all()
    )
    photos = _counts_by_user(db, Photo, account_id, start, end)
    lots = _counts_by_user(db, Lot, account_id, start, end)
    jobs = _counts_by_user(db, ProcessingJob, account_id, start, end)
    posted = posted_lot_stats_by_user(db, account_id, start, end)
    photographed = _photographed_lots_by_user(
        db, account_id, start, end, include_lots=include_lots
    )
    times = _event_times_by_user(db, account_id, start, end)
    photo_bounds = _created_at_bounds_by_user(db, Photo, account_id, start, end)
    lot_bounds = _created_at_bounds_by_user(db, Lot, account_id, start, end)
    members = []
    for row in users:
        hours = active_hours_for_timestamps(times.get(row.id, []))
        photo_n = photos.get(row.id, 0)
        lot_n = lots.get(row.id, 0)
        post = posted.get(row.id, _empty_posted_stats())
        photo_first, photo_last = photo_bounds.get(row.id, (None, None))
        _, lot_last = lot_bounds.get(row.id, (None, None))
        lot_rows, minute_samples, photographed_n = photographed.get(row.id, ([], [], 0))
        members.append(
            TeamActivityMember(
                user_id=row.id,
                email=row.email,
                role=row.role,
                photos_uploaded=photo_n,
                lots_created=lot_n,
                jobs_started=jobs.get(row.id, 0),
                active_hours=hours,
                photos_per_hour=per_active_hour(photo_n, hours),
                lots_per_hour=per_active_hour(lot_n, hours),
                lots_posted=post.posted,
                lots_sold=post.sold,
                lots_unsold=post.unsold,
                lots_live=post.live,
                lots_pulled=post.pulled,
                sold_revenue=post.sold_revenue,
                first_photo_at=photo_first,
                last_photo_at=photo_last,
                last_lot_at=lot_last,
                lots_photographed=photographed_n,
                median_minutes_per_lot=median_minutes(minute_samples),
                mean_minutes_per_lot=mean_minutes(minute_samples),
                lots=lot_rows,
            )
        )
    members.sort(
        key=lambda m: (-m.photos_uploaded, -m.lots_created, -m.jobs_started, m.email.lower())
    )
    photo_total = sum(m.photos_uploaded for m in members)
    lot_total = sum(m.lots_created for m in members)
    hour_total = round(sum(m.active_hours for m in members), 2)
    sample_minutes: list[float] = []
    photographed_total = 0
    for _shown, samples, total in photographed.values():
        sample_minutes.extend(samples)
        photographed_total += total
    sold_items, sold_by_category = build_sold_docket(db, account_id, start, end)
    return TeamActivitySummary(
        since=start,
        until=end,
        members=members,
        photos_uploaded=photo_total,
        lots_created=lot_total,
        jobs_started=sum(m.jobs_started for m in members),
        active_hours=hour_total,
        photos_per_hour=per_active_hour(photo_total, hour_total),
        lots_per_hour=per_active_hour(lot_total, hour_total),
        lots_posted=sum(m.lots_posted for m in members),
        lots_sold=sum(m.lots_sold for m in members),
        lots_unsold=sum(m.lots_unsold for m in members),
        lots_live=sum(m.lots_live for m in members),
        lots_pulled=sum(m.lots_pulled for m in members),
        sold_revenue=round(sum(m.sold_revenue for m in members), 2),
        lots_photographed=photographed_total,
        median_minutes_per_lot=median_minutes(sample_minutes),
        mean_minutes_per_lot=mean_minutes(sample_minutes),
        sold_items=sold_items,
        sold_by_category=sold_by_category,
    )
