"""Sale-wide photo-name match must skip a second Sync job before Gemini."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import JobStatus, Lot, Photo, ProcessingJob  # noqa: F401
from app.services.processing import _single_lot_batch_already_cataloged


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dedupe.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def _job(db, *, job_id: str, created_at: datetime) -> ProcessingJob:
    row = ProcessingJob(
        id=job_id,
        account_id="acct-1",
        user_id="user-1",
        sale_id="sale-1",
        input_folder="/tmp/batch",
        single_lot_mode=True,
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
            )
        )
    db.commit()


def test_same_sale_detects_prior_job_photos(tmp_path: Path) -> None:
    db = _session(tmp_path)
    t0 = datetime.now(timezone.utc) - timedelta(hours=1)
    t1 = datetime.now(timezone.utc)
    names = [f"mobile-draft-aa-{i:02d}.jpg" for i in range(1, 4)]
    _lot_with_names(db, lot_id="lot-old", names=names, created_at=t0)
    new_job = _job(db, job_id="job-retry", created_at=t1)
    paths = [Path(n) for n in names]

    assert _single_lot_batch_already_cataloged(db, new_job, paths) is False
    assert _single_lot_batch_already_cataloged(db, new_job, paths, same_sale=True) is True


def test_partial_name_overlap_is_not_a_match(tmp_path: Path) -> None:
    db = _session(tmp_path)
    t0 = datetime.now(timezone.utc) - timedelta(hours=1)
    _lot_with_names(db, lot_id="lot-old", names=["mobile-draft-aa-01.jpg"], created_at=t0)
    new_job = _job(db, job_id="job-2", created_at=datetime.now(timezone.utc))
    paths = [Path("mobile-draft-aa-01.jpg"), Path("mobile-draft-aa-02.jpg")]
    assert _single_lot_batch_already_cataloged(db, new_job, paths, same_sale=True) is False
