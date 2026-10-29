"""Stuck-job thresholds for the dashboard banner / GET /jobs/stuck."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import JobStatus, ProcessingJob
from app import job_tasks


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'stuck.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def _add_job(
    db,
    *,
    job_id: str,
    status: JobStatus,
    age: timedelta,
    error: str | None = None,
    sku: str | None = "LV101",
    account_id: str = "acct-1",
    sale_id: str = "sale-1",
) -> ProcessingJob:
    when = datetime.now(timezone.utc) - age
    row = ProcessingJob(
        id=job_id,
        account_id=account_id,
        user_id="user-1",
        sale_id=sale_id,
        status=status,
        input_folder="/tmp/missing-batch",
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


def test_receiving_stuck_after_threshold(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(job_tasks.settings, "job_stuck_receiving_seconds", 30 * 60)
    db = _session(tmp_path)
    stale = _add_job(db, job_id="recv-old", status=JobStatus.receiving, age=timedelta(minutes=31))
    fresh = _add_job(db, job_id="recv-new", status=JobStatus.receiving, age=timedelta(minutes=5))
    assert job_tasks.is_stuck_job(stale) is True
    assert job_tasks.is_stuck_job(fresh) is False
    stuck = job_tasks.list_stuck_jobs(db, "acct-1")
    assert [j.id for j in stuck] == ["recv-old"]


def test_queued_stuck_after_ten_minutes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(job_tasks.settings, "job_stuck_queued_seconds", 10 * 60)
    db = _session(tmp_path)
    stale = _add_job(db, job_id="q-old", status=JobStatus.queued, age=timedelta(minutes=11))
    fresh = _add_job(db, job_id="q-new", status=JobStatus.queued, age=timedelta(minutes=2))
    assert job_tasks.is_stuck_job(stale) is True
    assert job_tasks.is_stuck_job(fresh) is False


def test_processing_uses_updated_at(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(job_tasks.settings, "job_stuck_processing_seconds", 60 * 60)
    db = _session(tmp_path)
    stale = _add_job(db, job_id="p-old", status=JobStatus.processing, age=timedelta(minutes=61))
    fresh = _add_job(db, job_id="p-new", status=JobStatus.processing, age=timedelta(minutes=10))
    # Heartbeat: created long ago, progress just updated.
    heartbeat = _add_job(db, job_id="p-beat", status=JobStatus.processing, age=timedelta(hours=3))
    heartbeat.updated_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    db.commit()
    db.refresh(heartbeat)
    assert job_tasks.is_stuck_job(stale) is True
    assert job_tasks.is_stuck_job(fresh) is False
    assert job_tasks.is_stuck_job(heartbeat) is False


def test_failed_orphan_wording_is_stuck_gemini_is_not(tmp_path: Path) -> None:
    db = _session(tmp_path)
    orphan = _add_job(
        db,
        job_id="fail-orphan",
        status=JobStatus.failed,
        age=timedelta(minutes=1),
        error="Upload never finished (phone Sync stopped mid-lot).",
    )
    lost = _add_job(
        db,
        job_id="fail-disk",
        status=JobStatus.failed,
        age=timedelta(minutes=1),
        error="Input folder not found (upload disk was lost — re-sync from the phone)",
    )
    gemini = _add_job(
        db,
        job_id="fail-ai",
        status=JobStatus.failed,
        age=timedelta(hours=2),
        error="Gemini 500: overloaded",
    )
    cancelled = _add_job(
        db,
        job_id="fail-cancel",
        status=JobStatus.failed,
        age=timedelta(hours=2),
        error="Cancelled",
    )
    assert job_tasks.is_stuck_job(orphan) is True
    assert job_tasks.is_stuck_job(lost) is True
    assert job_tasks.is_stuck_job(gemini) is False
    assert job_tasks.is_stuck_job(cancelled) is False


def test_completed_jobs_are_never_stuck(tmp_path: Path) -> None:
    db = _session(tmp_path)
    done = _add_job(db, job_id="done", status=JobStatus.completed, age=timedelta(hours=5))
    assert job_tasks.is_stuck_job(done) is False
    assert job_tasks.list_stuck_jobs(db, "acct-1") == []


def test_stuck_query_is_account_scoped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(job_tasks.settings, "job_stuck_queued_seconds", 10 * 60)
    db = _session(tmp_path)
    _add_job(
        db,
        job_id="ours",
        status=JobStatus.queued,
        age=timedelta(minutes=20),
        account_id="acct-1",
    )
    _add_job(
        db,
        job_id="theirs",
        status=JobStatus.queued,
        age=timedelta(minutes=20),
        account_id="acct-other",
    )
    stuck = job_tasks.list_stuck_jobs(db, "acct-1")
    assert [j.id for j in stuck] == ["ours"]


def test_stuck_thresholds_come_from_settings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(job_tasks.settings, "job_stuck_queued_seconds", 60)
    db = _session(tmp_path)
    row = _add_job(db, job_id="q-90s", status=JobStatus.queued, age=timedelta(seconds=90))
    assert job_tasks.is_stuck_job(row) is True
    monkeypatch.setattr(job_tasks.settings, "job_stuck_queued_seconds", 10 * 60)
    assert job_tasks.is_stuck_job(row) is False
