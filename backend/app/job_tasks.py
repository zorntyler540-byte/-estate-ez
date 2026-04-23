from __future__ import annotations

import threading

from app.db import SessionLocal
from app.models import JobStatus, ProcessingJob
from app.services.processing import process_job

_job_cancels: dict[str, threading.Event] = {}
_job_cancels_lock = threading.Lock()


def is_job_running(job_id: str) -> bool:
    """Return true when this API process currently tracks a live background job thread."""
    with _job_cancels_lock:
        return job_id in _job_cancels


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
            process_job(db, job, cancel_event=cancel_ev)
    finally:
        _unregister(job_id)


def start_job_in_background(job_id: str, account_id: str) -> None:
    with _job_cancels_lock:
        if job_id in _job_cancels:
            raise RuntimeError("job_already_running")
        cancel_ev = threading.Event()
        _job_cancels[job_id] = cancel_ev
    threading.Thread(
        target=_background_runner,
        args=(job_id, account_id, cancel_ev),
        daemon=True,
        name=f"job-{job_id}",
    ).start()
