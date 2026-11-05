"""Fair catalog-job claiming (round-robin across accounts, FOR UPDATE SKIP LOCKED on Postgres)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.config import settings
from app.db import SessionLocal
from app.models import JobStatus, ProcessingJob

_LOG = logging.getLogger(__name__)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _is_postgres(db: Session) -> bool:
    bind = db.get_bind()
    return bool(bind is not None and bind.dialect.name == "postgresql")


def account_concurrency_cap() -> int:
    """Concurrent single-lot (phone / Add one lot) jobs per account."""
    return max(1, int(settings.catalog_job_max_concurrent))


def account_bulk_concurrency_cap() -> int:
    """Concurrent website bulk jobs per account (batch triage + the per-lot jobs it splits into)."""
    return max(1, int(settings.catalog_bulk_max_concurrent))


def job_is_bulk_lane(job: ProcessingJob) -> bool:
    return (not bool(getattr(job, "single_lot_mode", False))) or bool(getattr(job, "parent_job_id", None))


def lane_cap_for(job: ProcessingJob) -> int:
    return account_bulk_concurrency_cap() if job_is_bulk_lane(job) else account_concurrency_cap()


def _bulk_lane(job_cls):
    """1 for bulk work, 0 for single-lot uploads — SQL twin of ``job_is_bulk_lane``."""
    return case(
        (or_(job_cls.single_lot_mode.is_(False), job_cls.parent_job_id.isnot(None)), 1),
        else_=0,
    )


def _running_count_subquery():
    running = aliased(ProcessingJob)
    return (
        select(func.count())
        .where(
            running.account_id == ProcessingJob.account_id,
            running.status == JobStatus.processing,
        )
        .correlate(ProcessingJob)
        .scalar_subquery()
    )


def _running_same_lane_subquery():
    running = aliased(ProcessingJob)
    return (
        select(func.count())
        .where(
            running.account_id == ProcessingJob.account_id,
            running.status == JobStatus.processing,
            _bulk_lane(running) == _bulk_lane(ProcessingJob),
        )
        .correlate(ProcessingJob)
        .scalar_subquery()
    )


def running_in_lane(db: Session, job: ProcessingJob) -> int:
    bulk = job_is_bulk_lane(job)
    return int(
        db.query(func.count())
        .select_from(ProcessingJob)
        .filter(
            ProcessingJob.account_id == job.account_id,
            ProcessingJob.status == JobStatus.processing,
            _bulk_lane(ProcessingJob) == (1 if bulk else 0),
        )
        .scalar()
        or 0
    )


def _last_served_subquery():
    served = aliased(ProcessingJob)
    return (
        select(func.max(served.updated_at))
        .where(
            served.account_id == ProcessingJob.account_id,
            served.status.in_((JobStatus.processing, JobStatus.completed)),
        )
        .correlate(ProcessingJob)
        .scalar_subquery()
    )


def fair_queued_job_query(db: Session, *, skip_job_ids: set[str] | None = None):
    """Oldest queued job per fairness order, excluding jobs whose account lane is at its cap.

    Order: fewest currently-processing jobs, then least-recently served account, then oldest row.
    That round-robins across franchises instead of draining one account's FIFO backlog.

    Each account has two lanes: single-lot uploads (``CATALOG_JOB_MAX_CONCURRENT``) and website
    bulk work (``CATALOG_BULK_MAX_CONCURRENT``). A big batch split into per-lot jobs fills only
    the bulk lane, so the franchise's phone lots are never stuck behind it.
    """
    running = _running_count_subquery()
    same_lane = _running_same_lane_subquery()
    lane_cap = case(
        (_bulk_lane(ProcessingJob) == 1, account_bulk_concurrency_cap()),
        else_=account_concurrency_cap(),
    )
    last_served = _last_served_subquery()
    q = (
        db.query(ProcessingJob)
        .filter(ProcessingJob.status == JobStatus.queued)
        .filter(same_lane < lane_cap)
        .order_by(running.asc(), func.coalesce(last_served, _EPOCH).asc(), ProcessingJob.created_at.asc())
    )
    if skip_job_ids:
        q = q.filter(~ProcessingJob.id.in_(tuple(skip_job_ids)))
    if _is_postgres(db):
        q = q.with_for_update(skip_locked=True)
    return q


def _mark_processing(db: Session, job_id: str, account_id: str) -> bool:
    result = db.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.account_id == account_id,
            ProcessingJob.status == JobStatus.queued,
        )
        .values(
            status=JobStatus.processing,
            cancel_requested=False,
        )
    )
    return (result.rowcount or 0) == 1


def claim_queued_job(job_id: str, account_id: str) -> bool:
    """Atomically mark one queued job processing. Uses SKIP LOCKED on Postgres so replicas do not double-claim."""
    with SessionLocal() as db:
        q = db.query(ProcessingJob).filter(
            ProcessingJob.id == job_id,
            ProcessingJob.account_id == account_id,
            ProcessingJob.status == JobStatus.queued,
        )
        if _is_postgres(db):
            q = q.with_for_update(skip_locked=True)
        row = q.first()
        if row is None:
            db.rollback()
            return False
        if not _mark_processing(db, job_id, account_id):
            db.rollback()
            return False
        db.commit()
        return True


def claim_next_fair_job() -> tuple[str, str] | None:
    """Claim the next fairly scheduled queued job. None if the queue is empty or every lane is at cap."""
    skip_jobs: set[str] = set()
    for _ in range(12):
        with SessionLocal() as db:
            row = fair_queued_job_query(db, skip_job_ids=skip_jobs).first()
            if row is None:
                db.rollback()
                return None
            job_id = str(row.id)
            account_id = str(row.account_id)
            if running_in_lane(db, row) >= lane_cap_for(row):
                skip_jobs.add(job_id)
                db.rollback()
                continue
            if not _mark_processing(db, job_id, account_id):
                db.rollback()
                continue
            db.commit()
            _LOG.info(
                "Claimed catalog job job_id=%s account_id=%s lane=%s",
                job_id,
                account_id,
                "bulk" if job_is_bulk_lane(row) else "single",
            )
            return job_id, account_id
    return None
