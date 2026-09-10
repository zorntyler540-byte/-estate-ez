from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Account, JobStatus, Lot, Photo, ProcessingJob, Sale, User
from app.services.team_activity import (
    ActivityWindowError,
    active_hours_for_timestamps,
    per_active_hour,
    resolve_activity_window,
    summarize_team_activity,
)

UTC = timezone.utc
T0 = datetime(2026, 9, 5, 16, 0, tzinfo=UTC)


def test_resolve_activity_window_defaults_to_since_days() -> None:
    start, end = resolve_activity_window(since_days=7, now=T0)
    assert end == T0
    assert start == T0 - timedelta(days=7)


def test_resolve_activity_window_explicit_bounds() -> None:
    start, end = resolve_activity_window(
        since=datetime(2026, 9, 1, 7, 0),
        until=datetime(2026, 9, 1, 17, 0),
    )
    assert start == datetime(2026, 9, 1, 7, 0, tzinfo=UTC)
    assert end == datetime(2026, 9, 1, 17, 0, tzinfo=UTC)


def test_active_hours_empty_and_single_event() -> None:
    assert active_hours_for_timestamps([]) == 0.0
    assert active_hours_for_timestamps([T0]) == 1.0


def test_active_hours_same_day_span() -> None:
    start = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
    end = datetime(2026, 9, 5, 14, 0, tzinfo=UTC)
    assert active_hours_for_timestamps([start, end]) == 4.0


def test_active_hours_sums_days_with_one_hour_floor() -> None:
    day1_start = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
    day1_end = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    day2_only = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)
    assert active_hours_for_timestamps([day1_start, day1_end, day2_only]) == 3.0


def test_per_active_hour() -> None:
    assert per_active_hour(8, 4) == 2.0
    assert per_active_hour(0, 4) == 0.0
    assert per_active_hour(8, 0) == 0.0


def test_resolve_activity_window_rejects_inverted_and_too_long() -> None:
    with pytest.raises(ActivityWindowError, match="before end"):
        resolve_activity_window(since=T0, until=T0)
    with pytest.raises(ActivityWindowError, match="366"):
        resolve_activity_window(since=T0 - timedelta(days=400), until=T0)


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'team-activity.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def _user(db, *, user_id: str, account_id: str, email: str, role: str = "catalogger") -> User:
    row = User(
        id=user_id,
        account_id=account_id,
        email=email,
        password_hash="x",
        role=role,
    )
    db.add(row)
    return row


def test_summarize_counts_photos_lots_jobs_in_window(tmp_path: Path) -> None:
    db = _session(tmp_path)
    db.add(Account(id="acct-a", name="CT LV"))
    db.add(Account(id="acct-b", name="Other"))
    owner = _user(db, user_id="owner-a", account_id="acct-a", email="owner@ct.test", role="owner")
    maria = _user(db, user_id="maria-a", account_id="acct-a", email="maria@ct.test")
    _user(db, user_id="idle-a", account_id="acct-a", email="idle@ct.test")
    _user(db, user_id="other-b", account_id="acct-b", email="other@ct.test")
    db.add(Sale(id="sale-a", account_id="acct-a", user_id="owner-a", name="Sale A"))
    db.flush()

    inside = T0 - timedelta(hours=2)
    outside = T0 - timedelta(days=10)
    window_end = T0

    db.add_all(
        [
            Photo(
                id="p-maria-1",
                account_id="acct-a",
                user_id=maria.id,
                lot_id=None,
                original_name="a.jpg",
                storage_path="/a.jpg",
                created_at=inside,
            ),
            Photo(
                id="p-maria-2",
                account_id="acct-a",
                user_id=maria.id,
                lot_id=None,
                original_name="b.jpg",
                storage_path="/b.jpg",
                created_at=inside + timedelta(minutes=5),
            ),
            Photo(
                id="p-maria-old",
                account_id="acct-a",
                user_id=maria.id,
                lot_id=None,
                original_name="old.jpg",
                storage_path="/old.jpg",
                created_at=outside,
            ),
            Photo(
                id="p-owner",
                account_id="acct-a",
                user_id=owner.id,
                lot_id=None,
                original_name="o.jpg",
                storage_path="/o.jpg",
                created_at=inside,
            ),
            Photo(
                id="p-other-acct",
                account_id="acct-b",
                user_id="other-b",
                lot_id=None,
                original_name="x.jpg",
                storage_path="/x.jpg",
                created_at=inside,
            ),
            Lot(
                id="lot-maria",
                account_id="acct-a",
                user_id=maria.id,
                sale_id="sale-a",
                auction_title="Lamp",
                created_at=inside,
            ),
            Lot(
                id="lot-old",
                account_id="acct-a",
                user_id=maria.id,
                sale_id="sale-a",
                auction_title="Old",
                created_at=outside,
            ),
            ProcessingJob(
                id="job-maria",
                account_id="acct-a",
                user_id=maria.id,
                sale_id="sale-a",
                status=JobStatus.completed,
                input_folder="/tmp/in",
                created_at=inside,
            ),
            ProcessingJob(
                id="job-end-excluded",
                account_id="acct-a",
                user_id=owner.id,
                sale_id="sale-a",
                status=JobStatus.queued,
                input_folder="/tmp/end",
                created_at=window_end,
            ),
        ]
    )
    db.commit()

    summary = summarize_team_activity(db, "acct-a", T0 - timedelta(days=1), window_end)
    by_email = {m.email: m for m in summary.members}
    assert set(by_email) == {"owner@ct.test", "maria@ct.test", "idle@ct.test"}
    assert by_email["maria@ct.test"].photos_uploaded == 2
    assert by_email["maria@ct.test"].lots_created == 1
    assert by_email["maria@ct.test"].jobs_started == 1
    assert by_email["maria@ct.test"].active_hours == 1.0
    assert by_email["maria@ct.test"].photos_per_hour == 2.0
    assert by_email["maria@ct.test"].lots_per_hour == 1.0
    assert by_email["owner@ct.test"].photos_uploaded == 1
    assert by_email["owner@ct.test"].jobs_started == 0
    assert by_email["owner@ct.test"].active_hours == 1.0
    assert by_email["owner@ct.test"].photos_per_hour == 1.0
    assert by_email["owner@ct.test"].lots_per_hour == 0.0
    assert by_email["idle@ct.test"].photos_uploaded == 0
    assert by_email["maria@ct.test"].first_photo_at == inside
    assert by_email["owner@ct.test"].first_photo_at == inside
    assert by_email["idle@ct.test"].first_photo_at is None
    assert by_email["idle@ct.test"].active_hours == 0.0
    assert by_email["idle@ct.test"].photos_per_hour == 0.0
    assert summary.photos_uploaded == 3
    assert summary.lots_created == 1
    assert summary.jobs_started == 1
    assert summary.active_hours == 2.0
    assert summary.photos_per_hour == 1.5
    assert summary.lots_per_hour == 0.5
    assert summary.members[0].email == "maria@ct.test"


def test_summarize_active_rate_uses_first_to_last_same_day(tmp_path: Path) -> None:
    db = _session(tmp_path)
    db.add(Account(id="acct-a", name="CT LV"))
    owner = _user(db, user_id="owner-a", account_id="acct-a", email="owner@ct.test", role="owner")
    db.add(Sale(id="sale-a", account_id="acct-a", user_id=owner.id, name="Sale A"))
    first = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
    last = datetime(2026, 9, 5, 14, 0, tzinfo=UTC)
    photos = [
        Photo(
            id=f"p-{i}",
            account_id="acct-a",
            user_id=owner.id,
            lot_id=None,
            original_name=f"{i}.jpg",
            storage_path=f"/{i}.jpg",
            created_at=first + timedelta(minutes=30 * i),
        )
        for i in range(8)
    ]
    lots = [
        Lot(
            id=f"lot-{i}",
            account_id="acct-a",
            user_id=owner.id,
            sale_id="sale-a",
            auction_title=f"Lot {i}",
            created_at=first + timedelta(hours=i),
        )
        for i in range(4)
    ]
    photos[-1].created_at = last
    db.add_all(photos + lots)
    db.commit()
    summary = summarize_team_activity(db, "acct-a", first - timedelta(hours=1), last + timedelta(hours=1))
    row = summary.members[0]
    assert row.active_hours == 4.0
    assert row.photos_uploaded == 8
    assert row.lots_created == 4
    assert row.photos_per_hour == 2.0
    assert row.lots_per_hour == 1.0


def test_summarize_excludes_photos_at_until_bound(tmp_path: Path) -> None:
    db = _session(tmp_path)
    db.add(Account(id="acct-a", name="CT LV"))
    _user(db, user_id="owner-a", account_id="acct-a", email="owner@ct.test", role="owner")
    db.add(
        Photo(
            id="p-edge",
            account_id="acct-a",
            user_id="owner-a",
            lot_id=None,
            original_name="edge.jpg",
            storage_path="/edge.jpg",
            created_at=T0,
        )
    )
    db.commit()
    summary = summarize_team_activity(db, "acct-a", T0 - timedelta(hours=1), T0)
    assert summary.photos_uploaded == 0


def test_team_activity_endpoint_rejects_catalogger() -> None:
    from app.main import team_activity

    with pytest.raises(HTTPException) as ei:
        team_activity(
            since=None,
            until=None,
            since_days=7,
            current=SimpleNamespace(role="catalogger", account_id="acct-a", id="c1"),
            db=None,
        )
    assert ei.value.status_code == 403


def test_team_activity_endpoint_rejects_inverted_window() -> None:
    from app.main import team_activity

    with pytest.raises(HTTPException) as ei:
        team_activity(
            since=T0,
            until=T0,
            since_days=7,
            current=SimpleNamespace(role="owner", account_id="acct-a", id="o1"),
            db=None,
        )
    assert ei.value.status_code == 400
