"""Fair catalog-job claiming (round-robin across accounts, FOR UPDATE SKIP LOCKED on Postgres)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, update
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
    return max(1, int(settings.catalog_job_max_concurrent))


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


def fair_queued_job_query(db: Session, *, skip_account_ids: set[str] | None = None):
    """Oldest queued job per fairness order, excluding accounts already at the per-account cap.

    Order: fewest currently-processing jobs, then least-recently served account, then oldest row.
    That round-robins across franchises instead of draining one account's FIFO backlog.
    """
    cap = account_concurrency_cap()
    running = _running_count_subquery()
    last_served = _last_served_subquery()
    q = (
        db.query(ProcessingJob)
        .filter(ProcessingJob.status == JobStatus.queued)
        .filter(running < cap)
        .order_by(running.asc(), func.coalesce(last_served, _EPOCH).asc(), ProcessingJob.created_at.asc())
    )
    if skip_account_ids:
        q = q.filter(~ProcessingJob.account_id.in_(tuple(skip_account_ids)))
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
    """Claim the next fairly scheduled queued job. None if the queue is empty or every account is at cap."""
    skip_accounts: set[str] = set()
    cap = account_concurrency_cap()
    for _ in range(12):
        with SessionLocal() as db:
            row = fair_queued_job_query(db, skip_account_ids=skip_accounts).first()
            if row is None:
                db.rollback()
                return None
            job_id = str(row.id)
            account_id = str(row.account_id)
            running_n = (
                db.query(func.count())
                .select_from(ProcessingJob)
                .filter(
                    ProcessingJob.account_id == account_id,
                    ProcessingJob.status == JobStatus.processing,
                )
                .scalar()
                or 0
            )
            if int(running_n) >= cap:
                skip_accounts.add(account_id)
                db.rollback()
                continue
            if not _mark_processing(db, job_id, account_id):
                db.rollback()
                continue
            db.commit()
            _LOG.info("Claimed catalog job job_id=%s account_id=%s", job_id, account_id)
            return job_id, account_id
    return None
