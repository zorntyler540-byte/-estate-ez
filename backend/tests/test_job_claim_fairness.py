"""Fair round-robin claiming and concurrent queued→processing updates."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.job_queue import claim_next_fair_job, claim_queued_job, fair_queued_job_query
from app.models import JobStatus, ProcessingJob


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'claim.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _add_job(
    db,
    *,
    job_id: str,
    account_id: str,
    status: JobStatus = JobStatus.queued,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> ProcessingJob:
    when = created_at or datetime.now(timezone.utc)
    row = ProcessingJob(
        id=job_id,
        account_id=account_id,
        user_id="user-1",
        sale_id="sale-1",
        status=status,
        input_folder="/tmp/batch",
        created_at=when,
        updated_at=updated_at or when,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_fairness_prefers_idle_account_over_older_backlog(tmp_path: Path, monkeypatch) -> None:
    Session = _session(tmp_path)
    monkeypatch.setattr("app.job_queue.SessionLocal", Session)
    monkeypatch.setattr("app.config.settings.catalog_job_max_concurrent", 1)
    now = datetime.now(timezone.utc)
    with Session() as db:
        _add_job(db, job_id="a-old", account_id="acct-a", created_at=now - timedelta(minutes=10))
        _add_job(db, job_id="a-2", account_id="acct-a", created_at=now - timedelta(minutes=9))
        _add_job(db, job_id="a-3", account_id="acct-a", created_at=now - timedelta(minutes=8))
        _add_job(db, job_id="b-new", account_id="acct-b", created_at=now - timedelta(minutes=1))

    first = claim_next_fair_job()
    assert first == ("a-old", "acct-a")
    second = claim_next_fair_job()
    assert second == ("b-new", "acct-b")
    with Session() as db:
        row = db.query(ProcessingJob).filter(ProcessingJob.id == "a-old").one()
        row.status = JobStatus.completed
        db.commit()
    third = claim_next_fair_job()
    assert third == ("a-2", "acct-a")


def test_fair_query_orders_idle_account_first(tmp_path: Path, monkeypatch) -> None:
    Session = _session(tmp_path)
    monkeypatch.setattr("app.config.settings.catalog_job_max_concurrent", 1)
    now = datetime.now(timezone.utc)
    with Session() as db:
        _add_job(
            db,
            job_id="a-run",
            account_id="acct-a",
            status=JobStatus.processing,
            created_at=now - timedelta(minutes=5),
        )
        _add_job(db, job_id="a-wait", account_id="acct-a", created_at=now - timedelta(minutes=4))
        _add_job(db, job_id="b-wait", account_id="acct-b", created_at=now - timedelta(minutes=1))
        row = fair_queued_job_query(db).first()
        assert row is not None
        assert row.id == "b-wait"


def test_concurrent_claim_of_same_job_only_one_wins(tmp_path: Path, monkeypatch) -> None:
    Session = _session(tmp_path)
    monkeypatch.setattr("app.job_queue.SessionLocal", Session)
    with Session() as db:
        _add_job(db, job_id="job-race", account_id="acct-1")

    results: list[bool] = []
    barrier = threading.Barrier(2)

    def _attempt() -> None:
        barrier.wait(timeout=5)
        results.append(claim_queued_job("job-race", "acct-1"))

    threads = [threading.Thread(target=_attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert results.count(True) == 1
    assert results.count(False) == 1
    with Session() as db:
        row = db.query(ProcessingJob).filter(ProcessingJob.id == "job-race").one()
        assert row.status == JobStatus.processing


def test_claim_specific_job_skip_already_processing(tmp_path: Path, monkeypatch) -> None:
    Session = _session(tmp_path)
    monkeypatch.setattr("app.job_queue.SessionLocal", Session)
    with Session() as db:
        _add_job(db, job_id="job-done", account_id="acct-1", status=JobStatus.processing)
    assert claim_queued_job("job-done", "acct-1") is False


def test_postgres_skip_locked_sql_is_emitted() -> None:
    stmt = (
        select(ProcessingJob)
        .where(ProcessingJob.status == JobStatus.queued)
        .with_for_update(skip_locked=True)
    )
    sql = str(stmt.compile(dialect=postgresql.dialect())).upper()
    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" in sql
