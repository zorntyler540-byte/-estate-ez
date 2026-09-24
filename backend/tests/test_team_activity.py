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
    capture_minutes_for_timestamps,
    lot_ctbids_outcome,
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


def test_capture_minutes_needs_two_photos() -> None:
    assert capture_minutes_for_timestamps([]) is None
    assert capture_minutes_for_timestamps([T0]) is None


def test_capture_minutes_same_session_span() -> None:
    start = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
    end = datetime(2026, 9, 5, 10, 6, tzinfo=UTC)
    assert capture_minutes_for_timestamps([start, end]) == 6.0


def test_capture_minutes_ignores_later_add_photos_gap() -> None:
    shoot = [
        datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
        datetime(2026, 9, 5, 10, 2, tzinfo=UTC),
        datetime(2026, 9, 5, 10, 4, tzinfo=UTC),
    ]
    later = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    assert capture_minutes_for_timestamps(shoot + [later]) == 4.0


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
                ctseller_item_id=101,
                ctseller_posted_at=inside,
                ctseller_item_status="Sold",
                ctseller_sold_price=42.5,
            ),
            Lot(
                id="lot-maria-later",
                account_id="acct-a",
                user_id=maria.id,
                sale_id="sale-a",
                auction_title="Chair",
                created_at=inside + timedelta(minutes=8),
                ctseller_item_id=102,
                ctseller_posted_at=inside + timedelta(minutes=9),
                ctseller_item_status="Pending",
            ),
            Lot(
                id="lot-old",
                account_id="acct-a",
                user_id=maria.id,
                sale_id="sale-a",
                auction_title="Old",
                created_at=outside,
                ctseller_item_id=99,
                ctseller_posted_at=outside,
                ctseller_item_status="Sold",
                ctseller_sold_price=9.0,
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
    assert by_email["maria@ct.test"].lots_created == 2
    assert by_email["maria@ct.test"].jobs_started == 1
    assert by_email["maria@ct.test"].active_hours == 1.0
    assert by_email["maria@ct.test"].photos_per_hour == 2.0
    assert by_email["maria@ct.test"].lots_per_hour == 2.0
    assert by_email["maria@ct.test"].lots_posted == 2
    assert by_email["maria@ct.test"].lots_sold == 1
    assert by_email["maria@ct.test"].lots_live == 1
    assert by_email["maria@ct.test"].lots_unsold == 0
    assert by_email["maria@ct.test"].sold_revenue == 42.5
    assert by_email["owner@ct.test"].photos_uploaded == 1
    assert by_email["owner@ct.test"].jobs_started == 0
    assert by_email["owner@ct.test"].active_hours == 1.0
    assert by_email["owner@ct.test"].photos_per_hour == 1.0
    assert by_email["owner@ct.test"].lots_per_hour == 0.0
    assert by_email["idle@ct.test"].photos_uploaded == 0
    assert by_email["maria@ct.test"].first_photo_at == inside
    assert by_email["maria@ct.test"].last_photo_at == inside + timedelta(minutes=5)
    assert by_email["maria@ct.test"].last_lot_at == inside + timedelta(minutes=8)
    assert by_email["owner@ct.test"].first_photo_at == inside
    assert by_email["owner@ct.test"].last_photo_at == inside
    assert by_email["owner@ct.test"].last_lot_at is None
    assert by_email["idle@ct.test"].first_photo_at is None
    assert by_email["idle@ct.test"].last_photo_at is None
    assert by_email["idle@ct.test"].last_lot_at is None
    assert by_email["idle@ct.test"].active_hours == 0.0
    assert by_email["idle@ct.test"].photos_per_hour == 0.0
    assert summary.photos_uploaded == 3
    assert summary.lots_created == 2
    assert summary.jobs_started == 1
    assert summary.active_hours == 2.0
    assert summary.photos_per_hour == 1.5
    assert summary.lots_per_hour == 1.0
    assert summary.lots_posted == 2
    assert summary.lots_sold == 1
    assert summary.lots_live == 1
    assert summary.sold_revenue == 42.5
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
    assert row.first_photo_at == first
    assert row.last_photo_at == last
    assert row.last_lot_at == first + timedelta(hours=3)


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


def test_lot_ctbids_outcome_sold_unsold_live() -> None:
    sold = SimpleNamespace(
        ctseller_item_id=1,
        ctseller_posted_at=T0,
        ctseller_item_status="Sold",
        ctseller_sold_price=12,
    )
    unsold = SimpleNamespace(
        ctseller_item_id=2,
        ctseller_posted_at=T0,
        ctseller_item_status="Unsold",
        ctseller_sold_price=None,
    )
    live = SimpleNamespace(
        ctseller_item_id=3,
        ctseller_posted_at=T0,
        ctseller_item_status="Ready",
        ctseller_sold_price=None,
    )
    unposted = SimpleNamespace(
        ctseller_item_id=None,
        ctseller_posted_at=None,
        ctseller_item_status=None,
        ctseller_sold_price=None,
    )
    assert lot_ctbids_outcome(sold) == "sold"
    assert lot_ctbids_outcome(unsold) == "unsold"
    assert lot_ctbids_outcome(live) == "live"
    assert lot_ctbids_outcome(unposted) == "unposted"


def test_legacy_item_id_without_posted_at_counts_in_created_window(tmp_path: Path) -> None:
    db = _session(tmp_path)
    db.add(Account(id="acct-a", name="CT LV"))
    owner = _user(db, user_id="owner-a", account_id="acct-a", email="owner@ct.test", role="owner")
    db.add(Sale(id="sale-a", account_id="acct-a", user_id=owner.id, name="Sale A"))
    db.add(
        Lot(
            id="lot-legacy",
            account_id="acct-a",
            user_id=owner.id,
            sale_id="sale-a",
            auction_title="Legacy",
            created_at=T0 - timedelta(hours=1),
            ctseller_item_id=55,
            ctseller_posted_at=None,
            ctseller_item_status="Unsold",
        )
    )
    db.commit()
    summary = summarize_team_activity(db, "acct-a", T0 - timedelta(days=1), T0)
    row = summary.members[0]
    assert row.lots_posted == 1
    assert row.lots_unsold == 1
    assert row.lots_sold == 0


def test_summarize_photographed_lots_and_capture_minutes(tmp_path: Path) -> None:
    db = _session(tmp_path)
    db.add(Account(id="acct-a", name="CT LV"))
    maria = _user(db, user_id="maria-a", account_id="acct-a", email="maria@ct.test")
    db.add(Sale(id="sale-a", account_id="acct-a", user_id=maria.id, name="Sale A"))
    db.add(
        Lot(
            id="lot-lamp",
            account_id="acct-a",
            user_id=maria.id,
            sale_id="sale-a",
            sku="T100",
            auction_title="Brass lamp",
            created_at=T0 - timedelta(hours=1),
        )
    )
    first = T0 - timedelta(hours=3)
    db.add_all(
        [
            Photo(
                id="p1",
                account_id="acct-a",
                user_id=maria.id,
                lot_id="lot-lamp",
                original_name="1.jpg",
                storage_path="/1.jpg",
                created_at=first,
            ),
            Photo(
                id="p2",
                account_id="acct-a",
                user_id=maria.id,
                lot_id="lot-lamp",
                original_name="2.jpg",
                storage_path="/2.jpg",
                created_at=first + timedelta(minutes=3),
            ),
            Photo(
                id="p3",
                account_id="acct-a",
                user_id=maria.id,
                lot_id="lot-lamp",
                original_name="3.jpg",
                storage_path="/3.jpg",
                created_at=first + timedelta(minutes=8),
            ),
            Photo(
                id="p-later",
                account_id="acct-a",
                user_id=maria.id,
                lot_id="lot-lamp",
                original_name="later.jpg",
                storage_path="/later.jpg",
                created_at=first + timedelta(hours=2),
            ),
        ]
    )
    db.commit()
    summary = summarize_team_activity(db, "acct-a", T0 - timedelta(days=1), T0)
    row = next(m for m in summary.members if m.email == "maria@ct.test")
    assert row.lots_photographed == 1
    assert row.median_minutes_per_lot == 8.0
    assert len(row.lots) == 1
    assert row.lots[0].sku == "T100"
    assert row.lots[0].photo_count == 4
    assert row.lots[0].capture_minutes == 8.0
    assert row.lots[0].has_thumbnail is True
    hidden = summarize_team_activity(
        db, "acct-a", T0 - timedelta(days=1), T0, include_lots=False
    )
    hidden_row = next(m for m in hidden.members if m.email == "maria@ct.test")
    assert hidden_row.lots == []
    assert hidden_row.median_minutes_per_lot == 8.0


def test_upload_burst_is_not_reported_as_shoot_time(tmp_path: Path) -> None:
    db = _session(tmp_path)
    db.add(Account(id="acct-a", name="CT LV"))
    maria = _user(db, user_id="maria-a", account_id="acct-a", email="maria@ct.test")
    db.add(Sale(id="sale-a", account_id="acct-a", user_id=maria.id, name="Sale A"))
    db.add(
        Lot(
            id="lot-fast",
            account_id="acct-a",
            user_id=maria.id,
            sale_id="sale-a",
            sku="T4S",
            auction_title="Fast upload",
            created_at=T0 - timedelta(minutes=5),
        )
    )
    start = T0 - timedelta(minutes=2)
    db.add_all(
        [
            Photo(
                id=f"pb-{i}",
                account_id="acct-a",
                user_id=maria.id,
                lot_id="lot-fast",
                original_name=f"{i}.jpg",
                storage_path=f"/{i}.jpg",
                created_at=start + timedelta(seconds=i),
            )
            for i in range(8)
        ]
    )
    db.commit()
    summary = summarize_team_activity(db, "acct-a", T0 - timedelta(days=1), T0)
    row = next(m for m in summary.members if m.email == "maria@ct.test")
    assert row.lots[0].photo_count == 8
    assert row.lots[0].capture_minutes == 0.12
    assert row.lots[0].timing_source == "upload_burst"
    assert row.median_minutes_per_lot is None
    assert len(row.lots[0].photo_times) == 8
    assert row.lots[0].photo_times[0].source == "upload"


def test_camera_captured_at_beats_upload_burst(tmp_path: Path) -> None:
    db = _session(tmp_path)
    db.add(Account(id="acct-a", name="CT LV"))
    maria = _user(db, user_id="maria-a", account_id="acct-a", email="maria@ct.test")
    db.add(Sale(id="sale-a", account_id="acct-a", user_id=maria.id, name="Sale A"))
    db.add(
        Lot(
            id="lot-cam",
            account_id="acct-a",
            user_id=maria.id,
            sale_id="sale-a",
            sku="CAM1",
            auction_title="Real shoot",
            created_at=T0 - timedelta(minutes=1),
        )
    )
    upload = T0 - timedelta(seconds=10)
    shoot = T0 - timedelta(minutes=20)
    db.add_all(
        [
            Photo(
                id="pc-1",
                account_id="acct-a",
                user_id=maria.id,
                lot_id="lot-cam",
                original_name="1.jpg",
                storage_path="/1.jpg",
                created_at=upload,
                captured_at=shoot,
            ),
            Photo(
                id="pc-2",
                account_id="acct-a",
                user_id=maria.id,
                lot_id="lot-cam",
                original_name="2.jpg",
                storage_path="/2.jpg",
                created_at=upload + timedelta(seconds=2),
                captured_at=shoot + timedelta(minutes=6),
            ),
        ]
    )
    db.commit()
    summary = summarize_team_activity(db, "acct-a", T0 - timedelta(days=1), T0)
    row = next(m for m in summary.members if m.email == "maria@ct.test")
    assert row.lots[0].capture_minutes == 6.0
    assert row.lots[0].timing_source == "camera"
    assert row.median_minutes_per_lot == 6.0
    assert [pt.source for pt in row.lots[0].photo_times] == ["camera", "camera"]
    assert row.lots[0].photo_times[0].at == shoot


def test_epoch_filename_counts_as_camera_shutter(tmp_path: Path) -> None:
    db = _session(tmp_path)
    db.add(Account(id="acct-a", name="CT LV"))
    maria = _user(db, user_id="maria-a", account_id="acct-a", email="maria@ct.test")
    db.add(Sale(id="sale-a", account_id="acct-a", user_id=maria.id, name="Sale A"))
    db.add(
        Lot(
            id="lot-fn",
            account_id="acct-a",
            user_id=maria.id,
            sale_id="sale-a",
            sku="FN1",
            auction_title="Named files",
            created_at=T0 - timedelta(minutes=1),
        )
    )
    first_ms = int((T0 - timedelta(minutes=8)).timestamp() * 1000)
    second_ms = int((T0 - timedelta(minutes=3)).timestamp() * 1000)
    upload = T0 - timedelta(seconds=5)
    db.add_all(
        [
            Photo(
                id="pfn-1",
                account_id="acct-a",
                user_id=maria.id,
                lot_id="lot-fn",
                original_name=f"{first_ms}.jpg",
                storage_path="/fn1.jpg",
                created_at=upload,
            ),
            Photo(
                id="pfn-2",
                account_id="acct-a",
                user_id=maria.id,
                lot_id="lot-fn",
                original_name=f"{second_ms}.jpg",
                storage_path="/fn2.jpg",
                created_at=upload + timedelta(seconds=1),
            ),
        ]
    )
    db.commit()
    summary = summarize_team_activity(db, "acct-a", T0 - timedelta(days=1), T0)
    row = next(m for m in summary.members if m.email == "maria@ct.test")
    assert row.lots[0].timing_source == "camera"
    assert row.lots[0].capture_minutes == 5.0
    assert [pt.source for pt in row.lots[0].photo_times] == ["camera", "camera"]


def test_team_activity_http_includes_posted_sold_fields(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.auth import get_current_user
    from app.db import get_db
    from app.main import app

    engine = create_engine(f"sqlite:///{tmp_path / 'team-activity-http.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = Session()
    db.add(Account(id="acct-a", name="CT LV"))
    owner = _user(db, user_id="owner-a", account_id="acct-a", email="owner@example.com", role="owner")
    db.add(Sale(id="sale-a", account_id="acct-a", user_id=owner.id, name="Sale A"))
    db.add(
        Lot(
            id="lot-sold",
            account_id="acct-a",
            user_id=owner.id,
            sale_id="sale-a",
            auction_title="Lamp",
            sku="JL0101",
            created_at=T0 - timedelta(hours=1),
            ctseller_item_id=7,
            ctseller_posted_at=T0 - timedelta(hours=1),
            ctseller_item_status="Sold",
            ctseller_sold_price=25,
        )
    )
    db.add_all(
        [
            Photo(
                id="p-sold-1",
                account_id="acct-a",
                user_id=owner.id,
                lot_id="lot-sold",
                original_name="a.jpg",
                storage_path="/a.jpg",
                created_at=T0 - timedelta(minutes=12),
            ),
            Photo(
                id="p-sold-2",
                account_id="acct-a",
                user_id=owner.id,
                lot_id="lot-sold",
                original_name="b.jpg",
                storage_path="/b.jpg",
                created_at=T0 - timedelta(minutes=7),
            ),
        ]
    )
    db.commit()

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    client = TestClient(app)
    try:
        res = client.get(
            "/team/activity",
            params={"since": (T0 - timedelta(days=1)).isoformat(), "until": T0.isoformat()},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["totals"]["lots_posted"] == 1
        assert body["totals"]["lots_sold"] == 1
        assert body["totals"]["sold_revenue"] == 25
        member = next(m for m in body["members"] if m["email"] == "owner@example.com")
        assert member["lots_posted"] == 1
        assert member["lots_sold"] == 1
        assert member["lots_photographed"] == 1
        assert member["median_minutes_per_lot"] == 5.0
        assert member["lots"][0]["sku"] == "JL0101"
        assert member["lots"][0]["photo_count"] == 2
        assert member["lots"][0]["capture_minutes"] == 5.0
        assert member["lots"][0]["timing_source"] == "upload"
        assert member["lots"][0]["has_thumbnail"] is True
        assert len(member["lots"][0]["photo_times"]) == 2
        assert member["lots"][0]["photo_times"][0]["source"] == "upload"
    finally:
        app.dependency_overrides.clear()
        db.close()
