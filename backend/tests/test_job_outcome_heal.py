"""Leftover mid-upload jobs complete when the lot is already on the sale."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import JobStatus, Lot, Photo, ProcessingJob
from app import job_tasks


ORPHAN_ERR = (
    "Upload never finished (phone Sync stopped mid-lot). "
    "Delete this job, then Sync/Resend the lot from the phone."
)


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'heal.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def _add_job(
    db,
    *,
    job_id: str,
    status: JobStatus,
    sku: str | None = None,
    error: str | None = None,
    folder: str = "/tmp/missing-batch",
    created_at: datetime | None = None,
) -> ProcessingJob:
    when = created_at or (datetime.now(timezone.utc) - timedelta(hours=1))
    row = ProcessingJob(
        id=job_id,
        account_id="acct-1",
        user_id="user-1",
        sale_id="sale-1",
        status=status,
        input_folder=folder,
        single_lot_mode=True,
        single_lot_manual_sku=sku,
        error_message=error,
        created_at=when,
        updated_at=when,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _add_lot(db, *, lot_id: str, sku: str, names: list[str] | None = None) -> None:
    db.add(
        Lot(
            id=lot_id,
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            sku=sku,
            auction_title="Lamp",
            condition_text="Good",
        )
    )
    for i, name in enumerate(names or []):
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


def test_stale_receiving_completes_when_sku_already_on_sale(tmp_path: Path) -> None:
    db = _session(tmp_path)
    _add_lot(db, lot_id="lot-1", sku="JZ5126AD")
    job = _add_job(db, job_id="recv-1", status=JobStatus.receiving, sku="jz5126ad")

    changed = job_tasks.reconcile_stale_receiving_jobs(db, "acct-1")
    db.commit()
    db.refresh(job)

    assert changed is True
    assert job.status == JobStatus.completed
    assert job.error_message is None


def test_stale_receiving_fails_when_sku_not_on_sale(tmp_path: Path) -> None:
    db = _session(tmp_path)
    job = _add_job(db, job_id="recv-2", status=JobStatus.receiving, sku="JZ9999ZZ")

    changed = job_tasks.reconcile_stale_receiving_jobs(db, "acct-1")
    db.commit()
    db.refresh(job)

    assert changed is True
    assert job.status == JobStatus.failed
    assert "never finished" in (job.error_message or "").lower()


def test_failed_orphan_heals_when_sku_matches(tmp_path: Path) -> None:
    db = _session(tmp_path)
    _add_lot(db, lot_id="lot-1", sku="JZ5126AD")
    leftover = _add_job(
        db,
        job_id="fail-1",
        status=JobStatus.failed,
        sku="JZ5126AD",
        error=ORPHAN_ERR,
    )
    _add_job(
        db,
        job_id="fail-dup",
        status=JobStatus.failed,
        sku="jz 5126 ad",
        error="Input folder not found — re-sync from the phone.",
    )

    changed = job_tasks.reconcile_superseded_failed_jobs(db, "acct-1")
    db.commit()
    db.refresh(leftover)

    assert changed is True
    assert leftover.status == JobStatus.completed
    other = db.get(ProcessingJob, "fail-dup")
    assert other is not None
    assert other.status == JobStatus.completed


def test_failed_gemini_stays_failed_even_if_sku_exists(tmp_path: Path) -> None:
    db = _session(tmp_path)
    _add_lot(db, lot_id="lot-1", sku="JZ5126AD")
    job = _add_job(
        db,
        job_id="fail-ai",
        status=JobStatus.failed,
        sku="JZ5126AD",
        error="Gemini 500: overloaded",
    )

    changed = job_tasks.reconcile_superseded_failed_jobs(db, "acct-1")
    db.commit()
    db.refresh(job)

    assert changed is False
    assert job.status == JobStatus.failed
    assert "Gemini" in (job.error_message or "")


def test_later_completed_job_same_sku_heals_orphan(tmp_path: Path) -> None:
    db = _session(tmp_path)
    _add_job(
        db,
        job_id="done-1",
        status=JobStatus.completed,
        sku="JZ5125AD",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    leftover = _add_job(
        db,
        job_id="fail-old",
        status=JobStatus.failed,
        sku="JZ5125AD",
        error=ORPHAN_ERR,
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )

    changed = job_tasks.reconcile_superseded_failed_jobs(db, "acct-1")
    db.commit()
    db.refresh(leftover)

    assert changed is True
    assert leftover.status == JobStatus.completed


def test_folder_mobile_draft_match_completes_stale_receiving(tmp_path: Path) -> None:
    db = _session(tmp_path)
    draft = "1c86c230-a1c1-4433-b64e-5c2957e79027"
    folder = tmp_path / "batch"
    folder.mkdir()
    (folder / f"mobile-{draft}-01.jpg").write_bytes(b"fake")
    _add_lot(
        db,
        lot_id="lot-draft",
        sku="OTHER1",
        names=[f"mobile-{draft}-01.jpg", f"mobile-{draft}-02.jpg"],
    )
    job = _add_job(
        db,
        job_id="recv-draft",
        status=JobStatus.receiving,
        sku=None,
        folder=str(folder),
    )

    changed = job_tasks.reconcile_stale_receiving_jobs(db, "acct-1")
    db.commit()
    db.refresh(job)

    assert changed is True
    assert job.status == JobStatus.completed
    assert job.error_message is None
