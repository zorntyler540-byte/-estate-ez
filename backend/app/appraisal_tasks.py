from __future__ import annotations

import threading

from app.db import SessionLocal
from app.models import AppraisalCheck, JobStatus
from app.services.appraisal import run_appraisal_for_check

_active: set[str] = set()
_lock = threading.Lock()


def is_appraisal_check_running(check_id: str) -> bool:
    with _lock:
        return check_id in _active


def _runner(check_id: str, account_id: str) -> None:
    try:
        with SessionLocal() as db:
            row = (
                db.query(AppraisalCheck)
                .filter(AppraisalCheck.id == check_id, AppraisalCheck.account_id == account_id)
                .first()
            )
            if row is None or row.status != JobStatus.queued:
                return
            run_appraisal_for_check(db, check_id, account_id)
    finally:
        with _lock:
            _active.discard(check_id)


def start_appraisal_check(check_id: str, account_id: str) -> None:
    with _lock:
        if check_id in _active:
            raise RuntimeError("appraisal_check_already_running")
        _active.add(check_id)
    threading.Thread(
        target=_runner,
        args=(check_id, account_id),
        daemon=True,
        name=f"appraisal-{check_id}",
    ).start()
