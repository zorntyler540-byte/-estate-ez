"""Owner-facing per-person catalog activity for a time window.

Counts surviving ``photos`` / ``lots`` / ``processing_jobs`` rows stamped with
``user_id`` and ``created_at``. Deleted photos drop off. Credit is the login
that uploaded or started the job, not the person holding the camera.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Lot, Photo, ProcessingJob, User

MAX_ACTIVITY_DAYS = 366


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


@dataclass(frozen=True)
class TeamActivityMember:
    user_id: str
    email: str
    role: str
    photos_uploaded: int
    lots_created: int
    jobs_started: int


@dataclass(frozen=True)
class TeamActivitySummary:
    since: datetime
    until: datetime
    members: list[TeamActivityMember]
    photos_uploaded: int
    lots_created: int
    jobs_started: int


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
    members = [
        TeamActivityMember(
            user_id=row.id,
            email=row.email,
            role=row.role,
            photos_uploaded=photos.get(row.id, 0),
            lots_created=lots.get(row.id, 0),
            jobs_started=jobs.get(row.id, 0),
        )
        for row in users
    ]
    members.sort(
        key=lambda m: (-m.photos_uploaded, -m.lots_created, -m.jobs_started, m.email.lower())
    )
    return TeamActivitySummary(
        since=start,
        until=end,
        members=members,
        photos_uploaded=sum(m.photos_uploaded for m in members),
        lots_created=sum(m.lots_created for m in members),
        jobs_started=sum(m.jobs_started for m in members),
    )
