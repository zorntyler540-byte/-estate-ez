"""Retries must skip cataloged lots and reuse leftover Photo rows (no double Gemini / cap)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import JobStatus, Lot, Photo, ProcessingJob
from app.services.processing import (
    _add_catalog_photo,
    _batch_segment_already_cataloged,
    _process_job_batch_pipeline,
    _unlinked_job_photos_by_name,
    job_photos_already_cataloged_on_sale,
)


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'idem.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def _job(db, *, job_id: str, created_at: datetime, single_lot: bool = False) -> ProcessingJob:
    row = ProcessingJob(
        id=job_id,
        account_id="acct-1",
        user_id="user-1",
        sale_id="sale-1",
        input_folder="/tmp/batch",
        single_lot_mode=single_lot,
        status=JobStatus.processing,
        created_at=created_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _lot_with_names(db, *, lot_id: str, names: list[str], created_at: datetime) -> None:
    db.add(
        Lot(
            id=lot_id,
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            auction_title="Lamp",
            created_at=created_at,
        )
    )
    for i, name in enumerate(names):
        db.add(
            Photo(
                id=f"{lot_id}-p{i}",
                account_id="acct-1",
                user_id="user-1",
                lot_id=lot_id,
                original_name=name,
                storage_path=f"/catalog/{name}",
                sort_order=i,
                created_at=created_at,
            )
        )
    db.commit()


def _unlinked(db, *, photo_id: str, name: str, created_at: datetime) -> Photo:
    row = Photo(
        id=photo_id,
        account_id="acct-1",
        user_id="user-1",
        lot_id=None,
        original_name=name,
        storage_path=f"/catalog/{name}",
        sort_order=0,
        created_at=created_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_batch_retry_skips_when_all_names_already_on_sale_lots(tmp_path: Path) -> None:
    db = _session(tmp_path)
    t0 = datetime.now(timezone.utc) - timedelta(hours=1)
    t1 = datetime.now(timezone.utc)
    a = ["lot-a-01.jpg", "lot-a-02.jpg"]
    b = ["lot-b-01.jpg", "lot-b-02.jpg"]
    _lot_with_names(db, lot_id="lot-a", names=a, created_at=t0)
    _lot_with_names(db, lot_id="lot-b", names=b, created_at=t0)
    job = _job(db, job_id="job-retry", created_at=t1, single_lot=False)
    paths = [Path(n) for n in a + b]
    assert job_photos_already_cataloged_on_sale(db, job, paths) is True
    assert _batch_segment_already_cataloged(db, job, [Path(n) for n in a]) is True


def test_partial_batch_only_skips_finished_segment(tmp_path: Path) -> None:
    db = _session(tmp_path)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=30)
    t1 = datetime.now(timezone.utc)
    done = ["lot-a-01.jpg", "lot-a-02.jpg"]
    leftover = ["lot-b-01.jpg", "lot-b-02.jpg"]
    _lot_with_names(db, lot_id="lot-a", names=done, created_at=t0)
    job = _job(db, job_id="job-partial", created_at=t1, single_lot=False)
    assert job_photos_already_cataloged_on_sale(db, job, [Path(n) for n in done + leftover]) is False
    assert _batch_segment_already_cataloged(db, job, [Path(n) for n in done]) is True
    assert _batch_segment_already_cataloged(db, job, [Path(n) for n in leftover]) is False


def test_unlinked_leftovers_are_reused_not_inserted(tmp_path: Path) -> None:
    db = _session(tmp_path)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    job = _job(db, job_id="job-mid-persist", created_at=t0, single_lot=True)
    existing = _unlinked(db, photo_id="photo-old", name="mobile-draft-aa-01.jpg", created_at=t0)
    found = _unlinked_job_photos_by_name(db, job, ["mobile-draft-aa-01.jpg"])
    assert found["mobile-draft-aa-01.jpg"].id == existing.id

    with patch("app.services.processing.persist_catalog_photo") as persist:
        reused = _add_catalog_photo(db, job, Path("mobile-draft-aa-01.jpg"))
        persist.assert_not_called()
    assert reused.id == existing.id
    count = db.execute(select(func.count()).select_from(Photo)).scalar()
    assert count == 1


def test_reuse_does_not_increase_daily_photo_cap_count(tmp_path: Path) -> None:
    db = _session(tmp_path)
    t0 = datetime.now(timezone.utc)
    job = _job(db, job_id="job-cap", created_at=t0)
    _unlinked(db, photo_id="p1", name="a.jpg", created_at=t0)
    before = db.execute(
        select(func.count()).select_from(Photo).where(Photo.account_id == "acct-1")
    ).scalar()
    with patch("app.services.processing.persist_catalog_photo"):
        _add_catalog_photo(db, job, Path("a.jpg"))
        _add_catalog_photo(db, job, Path("a.jpg"))
    after = db.execute(
        select(func.count()).select_from(Photo).where(Photo.account_id == "acct-1")
    ).scalar()
    assert before == 1
    assert after == 1


def test_unlinked_photos_are_not_treated_as_cataloged_lots(tmp_path: Path) -> None:
    db = _session(tmp_path)
    t0 = datetime.now(timezone.utc)
    job = _job(db, job_id="job-unlinked", created_at=t0)
    _unlinked(db, photo_id="p1", name="a.jpg", created_at=t0)
    assert job_photos_already_cataloged_on_sale(db, job, [Path("a.jpg")]) is False


def test_batch_pipeline_does_not_call_gemini_when_sale_already_has_photos(
    tmp_path: Path, monkeypatch
) -> None:
    db = _session(tmp_path)
    t0 = datetime.now(timezone.utc) - timedelta(hours=1)
    names = ["lot-a-01.jpg", "lot-a-02.jpg"]
    _lot_with_names(db, lot_id="lot-a", names=names, created_at=t0)
    job = _job(db, job_id="job-done", created_at=datetime.now(timezone.utc), single_lot=False)

    monkeypatch.setattr(
        "app.services.processing.triage_ordered_photo_batch",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("triage should be skipped")),
    )
    monkeypatch.setattr(
        "app.services.processing.run_single_lot_pipeline",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Gemini should be skipped")),
    )
    monkeypatch.setattr("app.services.processing._emit_job", lambda *a, **k: None)

    _process_job_batch_pipeline(db, job, [Path(n) for n in names], tmp_path)
    db.refresh(job)
    assert job.status == JobStatus.completed
