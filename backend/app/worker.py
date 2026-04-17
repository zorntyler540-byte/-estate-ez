from __future__ import annotations

import time

from app.db import SessionLocal
from app.models import JobStatus, ProcessingJob
from app.services.processing import process_job


def run_worker_loop(poll_seconds: float = 2.0) -> None:
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
            process_job(db, job)


if __name__ == "__main__":
    run_worker_loop()

