from __future__ import annotations

import time

from app import job_tasks
from app.db import SessionLocal
from app.models import JobStatus, ProcessingJob


def run_worker_loop(poll_seconds: float = 2.0) -> None:
    """Legacy standalone worker — use the same claim/queue path as the API."""
    while True:
        with SessionLocal() as db:
            job = (
                db.query(ProcessingJob)
                .filter(ProcessingJob.status == JobStatus.queued)
                .order_by(ProcessingJob.created_at.asc())
                .first()
            )
            if not job:
                time.sleep(poll_seconds)
                continue
            job_id = job.id
            account_id = job.account_id
        try:
            job_tasks.start_job_in_background(job_id, account_id)
        except RuntimeError:
            pass
        time.sleep(poll_seconds)


if __name__ == "__main__":
    run_worker_loop()
