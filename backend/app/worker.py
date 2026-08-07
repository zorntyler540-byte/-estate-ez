"""Dedicated catalog worker — claims queued ProcessingJob rows and runs Gemini off the API process.

Run from the ``backend`` working directory (same image as the API):

    python -m app.worker

On Railway: add a second service from ``infra/docker/Dockerfile.worker``, copy the API env
(DATABASE_URL, GOOGLE_API_KEY, R2_*, SECRET_KEY, …), and set on the **API**:

    RUN_CATALOG_JOBS_IN_PROCESS=false

Leave ``RUN_CATALOG_JOBS_IN_PROCESS`` unset/true on the worker (default).
"""

from __future__ import annotations

import logging
import os
import time

from app import job_tasks
from app.config import settings
from app.db import SessionLocal
from app.models import JobStatus, ProcessingJob

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(message)s",
)
_LOG = logging.getLogger("app.worker")


def _poll_seconds() -> float:
    raw = os.environ.get("WORKER_POLL_SECONDS", "2")
    try:
        return max(0.5, min(30.0, float(raw)))
    except ValueError:
        return 2.0


def _next_queued_job() -> tuple[str, str] | None:
    """Oldest queued job whose account is under the concurrency cap."""
    with SessionLocal() as db:
        rows = (
            db.query(ProcessingJob.id, ProcessingJob.account_id)
            .filter(ProcessingJob.status == JobStatus.queued)
            .order_by(ProcessingJob.created_at.asc())
            .limit(40)
            .all()
        )
    for job_id, account_id in rows:
        if job_tasks.can_start_job_for_account(account_id):
            return str(job_id), str(account_id)
    return None


def run_worker_loop(poll_seconds: float | None = None) -> None:
    """Poll Postgres for queued catalog jobs and run them in this process."""
    interval = _poll_seconds() if poll_seconds is None else poll_seconds
    if not settings.run_catalog_jobs_in_process:
        _LOG.error(
            "RUN_CATALOG_JOBS_IN_PROCESS is false — this worker cannot claim jobs. "
            "Set it true (or unset) on the worker service."
        )
        raise SystemExit(2)

    _LOG.info(
        "Catalog worker starting poll=%.1fs max_concurrent=%s gemini_parallelism=%s",
        interval,
        settings.catalog_job_max_concurrent,
        settings.gemini_parallelism,
    )

    while True:
        picked = _next_queued_job()
        if not picked:
            time.sleep(interval)
            continue
        job_id, account_id = picked
        try:
            started = job_tasks.start_job_in_background(job_id, account_id)
        except RuntimeError as exc:
            _LOG.warning("Could not start job_id=%s: %s", job_id, exc)
            time.sleep(interval)
            continue
        if not started:
            time.sleep(interval)
            continue
        _LOG.info("Claimed catalog job job_id=%s account_id=%s", job_id, account_id)
        # Wait until this process finishes the job before claiming another for capacity accounting.
        while job_tasks.is_job_running(job_id):
            time.sleep(0.5)


if __name__ == "__main__":
    run_worker_loop()
