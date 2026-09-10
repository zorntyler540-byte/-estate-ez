"""Owner-facing per-person catalog activity for a time window.

Counts surviving ``photos`` / ``lots`` / ``processing_jobs`` rows stamped with
``user_id`` and ``created_at``. Deleted photos drop off. Credit is the login
that uploaded or started the job, not the person holding the camera.

Active hours (for photos/hr and lots/hr) use photo and lot timestamps only.
Each UTC calendar day with activity is the span from that person's first
event to their last that day, floored at one hour so a short burst does
not look like an impossible pace.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Lot, Photo, ProcessingJob, User

MAX_ACTIVITY_DAYS = 366
MIN_ACTIVE_HOURS_PER_DAY = 1.0


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
    first_photo_at: datetime | None = None
    last_photo_at: datetime | None = None
    last_lot_at: datetime | None = None


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


def summarize_team_activity(
    db: Session,
    account_id: str,
    start: datetime,
    end: datetime,
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
    times = _event_times_by_user(db, account_id, start, end)
    photo_bounds = _created_at_bounds_by_user(db, Photo, account_id, start, end)
    lot_bounds = _created_at_bounds_by_user(db, Lot, account_id, start, end)
    members = []
    for row in users:
        hours = active_hours_for_timestamps(times.get(row.id, []))
        photo_n = photos.get(row.id, 0)
        lot_n = lots.get(row.id, 0)
        photo_first, photo_last = photo_bounds.get(row.id, (None, None))
        _, lot_last = lot_bounds.get(row.id, (None, None))
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
                first_photo_at=photo_first,
                last_photo_at=photo_last,
                last_lot_at=lot_last,
            )
        )
    members.sort(
        key=lambda m: (-m.photos_uploaded, -m.lots_created, -m.jobs_started, m.email.lower())
    )
    photo_total = sum(m.photos_uploaded for m in members)
    lot_total = sum(m.lots_created for m in members)
    hour_total = round(sum(m.active_hours for m in members), 2)
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
    )
