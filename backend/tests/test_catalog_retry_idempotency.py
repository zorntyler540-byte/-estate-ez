"""Retries must skip cataloged lots and reuse leftover Photo rows (no double Gemini / cap).

Only stable upload names (``mobile-…``, ``append-…``, ``web-<batch>-NNNN…``) identify a photo
across jobs on a sale. Camera names (``IMG_1234.JPG``, ``image.jpg``) repeat, so they only
match a job's own rows (``job_photo_id``) and never drop photos from a different upload.
"""

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
    _single_lot_batch_already_cataloged,
    _unlinked_job_photos_by_name,
    job_photo_id,
    job_photos_already_cataloged_on_sale,
)

WEB_A = ["web-0123456789ab-0001-IMG_1234.jpg", "web-0123456789ab-0002-IMG_1235.jpg"]
WEB_B = ["web-0123456789ab-0003.jpg", "web-0123456789ab-0004.jpg"]


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'idem.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def _job(
    db,
    *,
    job_id: str,
    created_at: datetime,
    single_lot: bool = False,
    input_folder: str = "/tmp/batch",
) -> ProcessingJob:
    row = ProcessingJob(
        id=job_id,
        account_id="acct-1",
        user_id="user-1",
        sale_id="sale-1",
        input_folder=input_folder,
        single_lot_mode=single_lot,
        status=JobStatus.processing,
        created_at=created_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _lot(db, *, lot_id: str, created_at: datetime) -> None:
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
    db.commit()


def _lot_with_names(db, *, lot_id: str, names: list[str], created_at: datetime) -> None:
    _lot(db, lot_id=lot_id, created_at=created_at)
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


def _fake_persist(_account_id, source, *, photo_id=None):
    pid = photo_id or f"rand-{source.name}"
    return pid, f"/catalog/{pid}.jpg"


def test_batch_retry_skips_when_all_stable_names_already_on_sale_lots(tmp_path: Path) -> None:
    db = _session(tmp_path)
    t0 = datetime.now(timezone.utc) - timedelta(hours=1)
    t1 = datetime.now(timezone.utc)
    _lot_with_names(db, lot_id="lot-a", names=WEB_A, created_at=t0)
    _lot_with_names(db, lot_id="lot-b", names=WEB_B, created_at=t0)
    job = _job(db, job_id="job-retry", created_at=t1, single_lot=False)
    paths = [Path(n) for n in WEB_A + WEB_B]
    assert job_photos_already_cataloged_on_sale(db, job, paths) is True
    assert _batch_segment_already_cataloged(db, job, [Path(n) for n in WEB_A]) is True


def test_partial_batch_only_skips_finished_segment(tmp_path: Path) -> None:
    db = _session(tmp_path)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=30)
    t1 = datetime.now(timezone.utc)
    _lot_with_names(db, lot_id="lot-a", names=WEB_A, created_at=t0)
    job = _job(db, job_id="job-partial", created_at=t1, single_lot=False)
    assert job_photos_already_cataloged_on_sale(db, job, [Path(n) for n in WEB_A + WEB_B]) is False
    assert _batch_segment_already_cataloged(db, job, [Path(n) for n in WEB_A]) is True
    assert _batch_segment_already_cataloged(db, job, [Path(n) for n in WEB_B]) is False


def test_camera_names_on_other_lots_never_skip_a_new_upload(tmp_path: Path) -> None:
    """P0: a second phone/picker session reusing IMG_1234.JPG / image.jpg must still catalog."""
    db = _session(tmp_path)
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    names = ["IMG_1234.JPG", "image.jpg", "image_2.jpg"]
    _lot_with_names(db, lot_id="lot-earlier", names=names, created_at=t0)
    batch = _job(db, job_id="job-batch", created_at=datetime.now(timezone.utc))
    single = _job(db, job_id="job-single", created_at=datetime.now(timezone.utc), single_lot=True)
    paths = [Path(n) for n in names]

    assert job_photos_already_cataloged_on_sale(db, batch, paths) is False
    assert _batch_segment_already_cataloged(db, batch, paths) is False
    assert _single_lot_batch_already_cataloged(db, single, paths, same_sale=True) is False
    assert _single_lot_batch_already_cataloged(db, single, paths) is False


def test_camera_names_on_lots_created_after_the_job_do_not_match(tmp_path: Path) -> None:
    """Another upload finishing mid-run with the same camera names is not this job's lot."""
    db = _session(tmp_path)
    job = _job(db, job_id="job-slow", created_at=datetime.now(timezone.utc) - timedelta(minutes=5), single_lot=True)
    _lot_with_names(db, lot_id="lot-other", names=["image.jpg"], created_at=datetime.now(timezone.utc))
    assert _single_lot_batch_already_cataloged(db, job, [Path("image.jpg")]) is False


def test_job_retry_skips_its_own_photos_even_with_camera_names(tmp_path: Path) -> None:
    db = _session(tmp_path)
    folder = tmp_path / "batch"
    (folder / "lot1").mkdir(parents=True)
    (folder / "lot2").mkdir(parents=True)
    job = _job(
        db,
        job_id="job-own",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        input_folder=str(folder),
    )
    done = [folder / "lot1" / "IMG_0001.JPG", folder / "lot1" / "IMG_0002.JPG"]
    # Same leaf names in another subfolder are different photos of the same job.
    leftover = [folder / "lot2" / "IMG_0001.JPG", folder / "lot2" / "IMG_0002.JPG"]
    _lot(db, lot_id="lot-1", created_at=datetime.now(timezone.utc))
    for i, path in enumerate(done):
        db.add(
            Photo(
                id=job_photo_id(job, path),
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-1",
                original_name=path.name,
                storage_path="/catalog/x.jpg",
                sort_order=i,
            )
        )
    db.commit()

    assert _batch_segment_already_cataloged(db, job, done) is True
    assert _batch_segment_already_cataloged(db, job, leftover) is False
    assert job_photos_already_cataloged_on_sale(db, job, done + leftover) is False
    assert job_photos_already_cataloged_on_sale(db, job, done) is True


def test_job_photo_id_is_stable_per_job_and_path(tmp_path: Path) -> None:
    db = _session(tmp_path)
    folder = tmp_path / "batch"
    a = _job(db, job_id="job-a", created_at=datetime.now(timezone.utc), input_folder=str(folder))
    b = _job(db, job_id="job-b", created_at=datetime.now(timezone.utc), input_folder=str(folder))
    p = folder / "sub" / "image.jpg"
    assert job_photo_id(a, p) == job_photo_id(a, p)
    assert job_photo_id(a, p) != job_photo_id(b, p)
    assert job_photo_id(a, p) != job_photo_id(a, folder / "other" / "image.jpg")


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


def test_retry_reuses_own_row_and_does_not_increase_daily_photo_cap_count(tmp_path: Path) -> None:
    db = _session(tmp_path)
    t0 = datetime.now(timezone.utc)
    job = _job(db, job_id="job-cap", created_at=t0)
    with patch("app.services.processing.persist_catalog_photo", side_effect=_fake_persist) as persist:
        first = _add_catalog_photo(db, job, Path("a.jpg"))
        db.commit()
        second = _add_catalog_photo(db, job, Path("a.jpg"))
    assert persist.call_count == 1
    assert first.id == second.id == job_photo_id(job, Path("a.jpg"))
    after = db.execute(
        select(func.count()).select_from(Photo).where(Photo.account_id == "acct-1")
    ).scalar()
    assert after == 1


def test_camera_name_leftover_from_another_upload_is_not_stolen(tmp_path: Path) -> None:
    db = _session(tmp_path)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=1)
    job = _job(db, job_id="job-mine", created_at=t0)
    other = _unlinked(db, photo_id="other-upload", name="image.jpg", created_at=datetime.now(timezone.utc))
    with patch("app.services.processing.persist_catalog_photo", side_effect=_fake_persist):
        mine = _add_catalog_photo(db, job, Path("image.jpg"))
    assert mine.id != other.id
    assert mine.id == job_photo_id(job, Path("image.jpg"))


def test_linked_own_row_is_never_moved_to_a_new_lot(tmp_path: Path) -> None:
    db = _session(tmp_path)
    job = _job(db, job_id="job-linked", created_at=datetime.now(timezone.utc))
    _lot(db, lot_id="lot-kept", created_at=datetime.now(timezone.utc))
    pid = job_photo_id(job, Path("image.jpg"))
    db.add(
        Photo(
            id=pid,
            account_id="acct-1",
            user_id="user-1",
            lot_id="lot-kept",
            original_name="image.jpg",
            storage_path="/catalog/x.jpg",
        )
    )
    db.commit()
    with patch("app.services.processing.persist_catalog_photo", side_effect=_fake_persist):
        row = _add_catalog_photo(db, job, Path("image.jpg"))
    assert row.id != pid
    assert db.get(Photo, pid).lot_id == "lot-kept"


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
    _lot_with_names(db, lot_id="lot-a", names=WEB_A, created_at=t0)
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

    _process_job_batch_pipeline(db, job, [Path(n) for n in WEB_A], tmp_path)
    db.refresh(job)
    assert job.status == JobStatus.completed


def test_batch_pipeline_keeps_photos_whose_camera_names_exist_on_sale(
    tmp_path: Path, monkeypatch
) -> None:
    """P0: IMG_1234.JPG already on an earlier lot must still be triaged in a new batch."""
    db = _session(tmp_path)
    names = ["IMG_1234.JPG", "IMG_1235.JPG"]
    _lot_with_names(db, lot_id="lot-a", names=names, created_at=datetime.now(timezone.utc) - timedelta(days=1))
    job = _job(db, job_id="job-new", created_at=datetime.now(timezone.utc), single_lot=False)
    triaged: list[list[str]] = []

    class _Stop(Exception):
        pass

    def _triage(photos, **_k):
        triaged.append([p.name for p in photos])
        raise _Stop()

    monkeypatch.setattr("app.services.processing.triage_ordered_photo_batch", _triage)
    monkeypatch.setattr("app.services.processing._emit_job", lambda *a, **k: None)
    try:
        _process_job_batch_pipeline(db, job, [Path(n) for n in names], tmp_path)
    except _Stop:
        pass
    assert triaged == [names]
