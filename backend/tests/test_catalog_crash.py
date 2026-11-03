"""Isolated-child crash cap, RSS guard, and /jobs/stuck surfacing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import JobStatus, ProcessingJob
from app.core.catalog_crash import (
    apply_child_crash,
    catalog_crash_exhausted_message,
    format_catalog_crash_message,
    is_catalog_crash_exhausted_error,
    parse_catalog_crash_attempt,
    processing_error_message_preserving_crash,
)
from app import job_tasks
from app import worker


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'crash.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def _job(db, *, error: str | None = None, status: JobStatus = JobStatus.processing) -> ProcessingJob:
    when = datetime.now(timezone.utc)
    row = ProcessingJob(
        id="job-crash",
        account_id="acct-1",
        user_id="user-1",
        sale_id="sale-1",
        status=status,
        input_folder="/tmp/batch",
        error_message=error,
        created_at=when,
        updated_at=when,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_crash_attempt_roundtrip() -> None:
    msg = format_catalog_crash_message(2, 3, "isolated worker child exited")
    assert parse_catalog_crash_attempt(msg) == 2
    assert parse_catalog_crash_attempt(None) == 0
    exhausted = catalog_crash_exhausted_message(3, "SIGKILL")
    assert "Worker child crashed 3 times" in exhausted
    assert "Retry failed" in exhausted
    assert is_catalog_crash_exhausted_error(exhausted) is True
    assert is_catalog_crash_exhausted_error("Gemini 500: overloaded") is False


def test_apply_child_crash_queues_then_fails(monkeypatch) -> None:
    monkeypatch.setattr("app.core.catalog_crash.settings.worker_child_crash_retries", 3)
    row = SimpleNamespace(error_message=None, status=JobStatus.processing, cancel_requested=False)
    assert apply_child_crash(row, reason="child exited") == "queued"
    assert row.status == JobStatus.queued
    assert parse_catalog_crash_attempt(row.error_message) == 1
    assert apply_child_crash(row, reason="child exited") == "queued"
    assert parse_catalog_crash_attempt(row.error_message) == 2
    assert apply_child_crash(row, reason="child exited") == "failed"
    assert row.status == JobStatus.failed
    assert is_catalog_crash_exhausted_error(row.error_message) is True


def test_processing_start_preserves_crash_marker() -> None:
    prior = format_catalog_crash_message(2, 3, "isolated worker child exited")
    kept = processing_error_message_preserving_crash(prior)
    assert parse_catalog_crash_attempt(kept) == 2
    assert processing_error_message_preserving_crash(None) is None
    assert processing_error_message_preserving_crash("Gemini 429") is None


def test_requeue_after_child_death_increments_then_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.core.catalog_crash.settings.worker_child_crash_retries", 2)
    db = _session(tmp_path)
    _job(db)

    class _CM:
        def __enter__(self):
            return db

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(worker, "SessionLocal", lambda: _CM())
    monkeypatch.setattr(worker.job_events, "publish", lambda *a, **k: None)

    assert worker._requeue_if_still_processing("job-crash", "acct-1") is True
    row = db.query(ProcessingJob).filter(ProcessingJob.id == "job-crash").one()
    assert row.status == JobStatus.queued
    assert parse_catalog_crash_attempt(row.error_message) == 1

    row.status = JobStatus.processing
    db.commit()
    assert worker._requeue_if_still_processing("job-crash", "acct-1") is True
    row = db.query(ProcessingJob).filter(ProcessingJob.id == "job-crash").one()
    assert row.status == JobStatus.failed
    assert is_catalog_crash_exhausted_error(row.error_message) is True


def test_crash_exhausted_is_stuck(tmp_path: Path) -> None:
    db = _session(tmp_path)
    row = _job(
        db,
        status=JobStatus.failed,
        error=catalog_crash_exhausted_message(3, "isolated worker child exited"),
    )
    assert job_tasks.is_stuck_job(row) is True
    stuck = job_tasks.list_stuck_jobs(db, "acct-1")
    assert [j.id for j in stuck] == ["job-crash"]


def test_orphan_reconcile_increments_existing_crash_marker(monkeypatch) -> None:
    monkeypatch.setattr("app.core.catalog_crash.settings.worker_child_crash_retries", 3)
    row = MagicMock()
    row.id = "orphan-crash"
    row.status = JobStatus.processing
    row.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    row.created_at = row.updated_at
    row.account_id = "acct-1"
    row.error_message = format_catalog_crash_message(1, 3, "isolated worker child exited")
    row.cancel_requested = False

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [row]

    with monkeypatch.context() as ctx:
        ctx.setattr(job_tasks, "is_job_running", lambda *_a, **_k: False)
        ctx.setattr(job_tasks, "_orphaned_processing_lock_is_free", lambda *_a, **_k: True)
        n = job_tasks.reconcile_worker_orphaned_processing_jobs(db, min_age_seconds=0)

    assert n == 1
    assert row.status == JobStatus.queued
    assert parse_catalog_crash_attempt(row.error_message) == 2


def test_rss_and_as_caps_default_to_3000() -> None:
    from app.config import Settings

    assert Settings.model_fields["worker_child_rss_mb"].default == 3000
    assert Settings.model_fields["worker_child_as_mb"].default == 3000
    assert worker.child_rss_cap_mb() >= 3000
