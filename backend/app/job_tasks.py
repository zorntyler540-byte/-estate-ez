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


def _running_count_for_account_unlocked(account_id: str) -> int:
    return sum(1 for acc in _job_accounts.values() if acc == account_id)


def running_count_for_account(account_id: str) -> int:
    with _job_cancels_lock:
        return _running_count_for_account_unlocked(account_id)


def can_start_job_for_account(account_id: str) -> bool:
    cap = max(1, settings.catalog_job_max_concurrent)
    with _job_cancels_lock:
        return _running_count_for_account_unlocked(account_id) < cap


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
    with _job_cancels_lock:
        if job_id in _job_cancels:
            raise RuntimeError("job_already_running")
        cap = max(1, settings.catalog_job_max_concurrent)
        if _running_count_for_account_unlocked(account_id) >= cap:
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
    """After a job finishes, start the oldest queued job for this account if capacity allows."""
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
