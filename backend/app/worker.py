"""Dedicated catalog worker — claims queued ProcessingJob rows and runs Gemini off the API process.

Run from the ``backend`` working directory (same image as the API):

    python -m app.worker

On Railway: add a second service from ``infra/docker/Dockerfile.worker``, copy the API env
(DATABASE_URL, GOOGLE_API_KEY, R2_*, SECRET_KEY, …), and set on the **API**:

    RUN_CATALOG_JOBS_IN_PROCESS=false

Leave ``RUN_CATALOG_JOBS_IN_PROCESS`` unset/true on the worker (default).

By default each catalog job runs in a **child process** (``WORKER_ISOLATE_JOBS=true``) so
decoded JPEG RAM is released when the child exits. Cap the worker replica at 4 GB.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
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


def parse_worker_argv(argv: list[str]) -> tuple[str, str] | None:
    """Return ``(job_id, account_id)`` when invoked as ``--run-job <id> <account>``."""
    if len(argv) >= 4 and argv[1] == "--run-job":
        job_id = (argv[2] or "").strip()
        account_id = (argv[3] or "").strip()
        if job_id and account_id:
            return job_id, account_id
    return None


def _poll_seconds() -> float:
    raw = os.environ.get("WORKER_POLL_SECONDS", "2")
    try:
        return max(0.5, min(30.0, float(raw)))
    except ValueError:
        return 2.0


def _requeue_orphaned_processing(*, min_age_seconds: int | None = None) -> int:
    """Unblock the queue after worker crash — see ``reconcile_worker_orphaned_processing_jobs``."""
    with SessionLocal() as db:
        n = job_tasks.reconcile_worker_orphaned_processing_jobs(
            db, min_age_seconds=min_age_seconds
        )
        if n:
            db.commit()
        return n


def _next_queued_job() -> tuple[str, str] | None:
    """Oldest queued job whose account is under the concurrency cap."""
    # Free capacity slots held by dead ``processing`` rows before we decide nothing can start.
    try:
        freed = _requeue_orphaned_processing()
        if freed:
            _LOG.info("Re-queued %d orphan processing job(s) before claim", freed)
    except Exception:  # noqa: BLE001
        _LOG.exception("orphan processing reconcile failed")

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


def _reconcile_stale_receiving_globally() -> int:
    """Close orphan ``receiving`` jobs and leftover FAILED mid-upload cards across accounts."""
    n = 0
    with SessionLocal() as db:
        account_ids = [
            str(r[0])
            for r in db.query(ProcessingJob.account_id)
            .filter(ProcessingJob.status.in_((JobStatus.receiving, JobStatus.failed)))
            .distinct()
            .all()
        ]
        for account_id in account_ids:
            changed = job_tasks.reconcile_stale_receiving_jobs(db, account_id)
            changed = job_tasks.reconcile_superseded_failed_jobs(db, account_id) or changed
            if changed:
                db.commit()
                n += 1
    return n


def run_isolated_job(job_id: str, account_id: str) -> int:
    """Child entry: claim and run one job, then exit so the parent RSS stays small."""
    os.environ["WORKER_ISOLATED_CHILD"] = "1"
    try:
        started = job_tasks.start_job_in_background(job_id, account_id)
    except RuntimeError as exc:
        _LOG.warning("Could not start isolated job_id=%s: %s", job_id, exc)
        return 2
    if not started:
        return 3
    _LOG.info("Isolated catalog job running job_id=%s account_id=%s", job_id, account_id)
    while job_tasks.is_job_running(job_id):
        time.sleep(0.5)
    return 0


def _spawn_isolated_job(job_id: str, account_id: str) -> int:
    cmd = [sys.executable, "-m", "app.worker", "--run-job", job_id, account_id]
    _LOG.info("Spawning isolated catalog job job_id=%s", job_id)
    completed = subprocess.run(cmd, check=False)
    return int(completed.returncode or 0)


def run_worker_loop(poll_seconds: float | None = None) -> None:
    """Poll Postgres for queued catalog jobs and run them in this process."""
    interval = _poll_seconds() if poll_seconds is None else poll_seconds
    if not settings.run_catalog_jobs_in_process:
        _LOG.error(
            "RUN_CATALOG_JOBS_IN_PROCESS is false — this worker cannot claim jobs. "
            "Set it true (or unset) on the worker service."
        )
        raise SystemExit(2)

    isolate = bool(settings.worker_isolate_jobs)
    _LOG.info(
        "Catalog worker starting poll=%.1fs max_concurrent=%s gemini_parallelism=%s isolate_jobs=%s",
        interval,
        settings.catalog_job_max_concurrent,
        settings.gemini_parallelism,
        isolate,
    )

    # Previous container life may have left rows in ``processing`` with no live thread —
    # that blocks all new queued Resend jobs until capacity clears.
    try:
        boot_freed = _requeue_orphaned_processing(min_age_seconds=0)
        if boot_freed:
            _LOG.info("Boot re-queued %d orphan processing job(s)", boot_freed)
    except Exception:  # noqa: BLE001
        _LOG.exception("boot orphan processing reconcile failed")

    idle_sweeps = 0
    while True:
        picked = _next_queued_job()
        if not picked:
            idle_sweeps += 1
            # Every ~minute of idle polling, clear orphan mid-upload jobs.
            if idle_sweeps >= max(1, int(60 / interval)):
                idle_sweeps = 0
                try:
                    swept = _reconcile_stale_receiving_globally()
                    if swept:
                        _LOG.info("Reconciled stale receiving jobs for %d account(s)", swept)
                except Exception:  # noqa: BLE001
                    _LOG.exception("stale receiving reconcile failed")
            time.sleep(interval)
            continue
        idle_sweeps = 0
        job_id, account_id = picked
        if isolate:
            code = _spawn_isolated_job(job_id, account_id)
            if code != 0:
                _LOG.warning("Isolated job job_id=%s exited %s", job_id, code)
                time.sleep(interval)
            continue
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


def main(argv: list[str] | None = None) -> None:
    isolated = parse_worker_argv(argv if argv is not None else sys.argv)
    if isolated is not None:
        job_id, account_id = isolated
        raise SystemExit(run_isolated_job(job_id, account_id))
    run_worker_loop()


if __name__ == "__main__":
    main()
