from __future__ import annotations

import logging
import threading
import traceback

from app.config import settings
from app.db import SessionLocal
from app.models import JobStatus, ProcessingJob
from app.services import job_events
from app.services.processing import process_job

_LOG = logging.getLogger(__name__)

_job_cancels: dict[str, threading.Event] = {}
_job_accounts: dict[str, str] = {}
_job_cancels_lock = threading.Lock()


def is_job_running(job_id: str) -> bool:
    """Return true when this API process currently tracks a live background job thread."""
    with _job_cancels_lock:
        return job_id in _job_cancels


def running_count_for_account(account_id: str) -> int:
    with _job_cancels_lock:
        return sum(1 for acc in _job_accounts.values() if acc == account_id)


def _processing_job_ids_in_db(account_id: str) -> set[str]:
    with SessionLocal() as db:
        rows = (
            db.query(ProcessingJob.id)
            .filter(
                ProcessingJob.account_id == account_id,
                ProcessingJob.status == JobStatus.processing,
            )
            .all()
        )
    return {row.id for row in rows}


def reconcile_memory_threads_for_account(account_id: str) -> None:
    """Drop in-memory worker slots whose jobs are no longer active (completed/failed/missing)."""
    with SessionLocal() as db:
        rows = (
            db.query(ProcessingJob.id, ProcessingJob.status)
            .filter(ProcessingJob.account_id == account_id)
            .all()
        )
    status_by_id = {row.id: row.status for row in rows}
    with _job_cancels_lock:
        for job_id in list(_job_accounts.keys()):
            if _job_accounts.get(job_id) != account_id:
                continue
            status = status_by_id.get(job_id)
            if status in (JobStatus.processing, JobStatus.queued):
                continue
            _job_cancels.pop(job_id, None)
            _job_accounts.pop(job_id, None)


def effective_running_count(account_id: str) -> int:
    reconcile_memory_threads_for_account(account_id)
    db_count = len(_processing_job_ids_in_db(account_id))
    mem_count = running_count_for_account(account_id)
    return max(db_count, mem_count)


def can_start_job_for_account(account_id: str) -> bool:
    cap = max(1, settings.catalog_job_max_concurrent)
    return effective_running_count(account_id) < cap


def request_cancel(job_id: str) -> bool:
    with _job_cancels_lock:
        ev = _job_cancels.get(job_id)
    if ev is None:
        return False
    ev.set()
    return True


def _unregister(job_id: str) -> None:
    with _job_cancels_lock:
        _job_cancels.pop(job_id, None)
        _job_accounts.pop(job_id, None)


def _background_runner(job_id: str, account_id: str, cancel_ev: threading.Event) -> None:
    try:
        with SessionLocal() as db:
            job = (
                db.query(ProcessingJob)
                .filter(ProcessingJob.id == job_id, ProcessingJob.account_id == account_id)
                .first()
            )
            if job is None or job.status != JobStatus.queued:
                return
            try:
                process_job(db, job, cancel_event=cancel_ev)
            except Exception as exc:
                tb = traceback.format_exc()
                _LOG.error("process_job crashed job_id=%s\n%s", job_id, tb)
                try:
                    job.status = JobStatus.failed
                    job.error_message = (f"{type(exc).__name__}: {exc}")[:4000]
                    job.cancel_requested = False
                    db.commit()
                    job_events.publish(
                        job.user_id,
                        job.id,
                        {
                            "type": "status",
                            "status": "failed",
                            "error_message": job.error_message,
                            "job_id": job.id,
                        },
                    )
                except Exception:
                    _LOG.exception("failed to persist job failure state job_id=%s", job_id)
    finally:
        _unregister(job_id)
        try_start_next_queued_job(account_id)


def start_job_in_background(job_id: str, account_id: str) -> bool:
    """
    Start a queued job when account capacity allows.
    Returns False when the job should stay queued (another lot is still running).
    """
    if not can_start_job_for_account(account_id):
        return False
    cap = max(1, settings.catalog_job_max_concurrent)
    with _job_cancels_lock:
        if job_id in _job_cancels:
            raise RuntimeError("job_already_running")
        if running_count_for_account(account_id) >= cap:
            return False
        cancel_ev = threading.Event()
        _job_cancels[job_id] = cancel_ev
        _job_accounts[job_id] = account_id
    threading.Thread(
        target=_background_runner,
        args=(job_id, account_id, cancel_ev),
        daemon=True,
        name=f"job-{job_id}",
    ).start()
    return True


def try_start_next_queued_job(account_id: str) -> None:
    """After a job finishes (or the queue was idle), start the oldest queued job for this account."""
    reconcile_memory_threads_for_account(account_id)
    if not can_start_job_for_account(account_id):
        return
    with SessionLocal() as db:
        row = (
            db.query(ProcessingJob)
            .filter(
                ProcessingJob.account_id == account_id,
                ProcessingJob.status == JobStatus.queued,
            )
            .order_by(ProcessingJob.created_at.asc())
            .first()
        )
        if row is None:
            return
        job_id = row.id
    try:
        if not start_job_in_background(job_id, account_id):
            return
    except RuntimeError:
        return
    _LOG.info("Started queued catalog job job_id=%s account_id=%s", job_id, account_id)


def kick_queue_if_idle(account_id: str) -> None:
    """Start waiting queued jobs when nothing is processing (e.g. after API restart or stale reconcile)."""
    reconcile_memory_threads_for_account(account_id)
    if effective_running_count(account_id) > 0:
        return
    with SessionLocal() as db:
        has_queued = (
            db.query(ProcessingJob.id)
            .filter(
                ProcessingJob.account_id == account_id,
                ProcessingJob.status == JobStatus.queued,
            )
            .first()
            is not None
        )
    if has_queued:
        try_start_next_queued_job(account_id)
