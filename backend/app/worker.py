"""Dedicated catalog worker — claims queued ProcessingJob rows and runs Gemini off the API process.

Run from the ``backend`` working directory (same image as the API):

    python -m app.worker

On Railway: add a second service from ``infra/docker/Dockerfile.worker``, copy the API env
(DATABASE_URL, GOOGLE_API_KEY, R2_*, SECRET_KEY, …), and set on the **API**:

    RUN_CATALOG_JOBS_IN_PROCESS=false

Leave ``RUN_CATALOG_JOBS_IN_PROCESS`` unset/true on the worker (default).

By default each catalog job runs in a **child process** (``WORKER_ISOLATE_JOBS=true``) so
decoded JPEG RAM is released when the child exits. The parent keeps a pool of up to
``WORKER_MAX_CONCURRENT_JOBS`` children (default 3). Cap the worker replica at 4 GB.
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
from app.job_queue import claim_next_fair_job
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


def worker_slot_capacity() -> int:
    return max(1, int(settings.worker_max_concurrent_jobs))


def _requeue_orphaned_processing(*, min_age_seconds: int | None = None) -> int:
    """Unblock the queue after worker crash — see ``reconcile_worker_orphaned_processing_jobs``."""
    with SessionLocal() as db:
        n = job_tasks.reconcile_worker_orphaned_processing_jobs(
            db, min_age_seconds=min_age_seconds
        )
        if n:
            db.commit()
        return n


def _requeue_if_still_processing(job_id: str, account_id: str) -> bool:
    """If a child died while the row is still ``processing``, free the slot for another claim."""
    with SessionLocal() as db:
        row = (
            db.query(ProcessingJob)
            .filter(ProcessingJob.id == job_id, ProcessingJob.account_id == account_id)
            .first()
        )
        if row is None or row.status != JobStatus.processing:
            return False
        row.status = JobStatus.queued
        row.cancel_requested = False
        db.commit()
        _LOG.warning(
            "Re-queued catalog job after isolated child exit job_id=%s account_id=%s",
            job_id,
            account_id,
        )
        return True


def run_idle_maintenance() -> None:
    """Owner-configured Facebook auto-approve plus click-row retention (not a catalogger trigger)."""
    from app.services.click_tracking import purge_old_tracked_clicks
    from app.services.facebook_calendar import sweep_calendar_auto_approve

    with SessionLocal() as db:
        try:
            sweep_calendar_auto_approve(db)
        except Exception:  # noqa: BLE001
            _LOG.exception("facebook auto-approve sweep failed")
            db.rollback()
        try:
            n = purge_old_tracked_clicks(db)
            db.commit()
            if n:
                _LOG.info("Purged %s old tracked-link click row(s)", n)
        except Exception:  # noqa: BLE001
            db.rollback()
            _LOG.exception("tracked-click retention sweep failed")


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
    """Child entry: run one already-claimed job, then exit so the parent RSS stays small."""
    os.environ["WORKER_ISOLATED_CHILD"] = "1"
    try:
        started = job_tasks.start_job_in_background(
            job_id, account_id, already_claimed=True
        )
    except RuntimeError as exc:
        _LOG.warning("Could not start isolated job_id=%s: %s", job_id, exc)
        return 2
    if not started:
        return 3
    _LOG.info("Isolated catalog job running job_id=%s account_id=%s", job_id, account_id)
    while job_tasks.is_job_running(job_id):
        time.sleep(0.5)
    return 0


def _spawn_isolated_job_async(job_id: str, account_id: str) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "app.worker", "--run-job", job_id, account_id]
    _LOG.info("Spawning isolated catalog job job_id=%s", job_id)
    return subprocess.Popen(cmd)


def _reap_isolated_children(children: dict[str, tuple[subprocess.Popen, str]]) -> None:
    done: list[str] = []
    for job_id, (proc, account_id) in children.items():
        code = proc.poll()
        if code is None:
            continue
        done.append(job_id)
        job_tasks.unregister_isolated_child_job(job_id)
        if code != 0:
            _LOG.warning("Isolated job job_id=%s exited %s", job_id, code)
        try:
            _requeue_if_still_processing(job_id, account_id)
        except Exception:  # noqa: BLE001
            _LOG.exception("post-child requeue failed job_id=%s", job_id)
    for job_id in done:
        children.pop(job_id, None)


def _in_process_running_count() -> int:
    with job_tasks._job_cancels_lock:
        return len(job_tasks._job_cancels)


def _maybe_idle_maintenance(idle_sweeps: int, interval: float) -> int:
    if idle_sweeps < max(1, int(60 / interval)):
        return idle_sweeps
    try:
        swept = _reconcile_stale_receiving_globally()
        if swept:
            _LOG.info("Reconciled stale receiving jobs for %d account(s)", swept)
    except Exception:  # noqa: BLE001
        _LOG.exception("stale receiving reconcile failed")
    try:
        run_idle_maintenance()
    except Exception:  # noqa: BLE001
        _LOG.exception("idle maintenance failed")
    return 0


def _fill_worker_slots(
    *,
    isolate: bool,
    children: dict[str, tuple[subprocess.Popen, str]],
    cap: int,
) -> bool:
    """Claim and start jobs until this process is at ``cap``. Returns True if any job started."""
    started_any = False
    while True:
        if isolate:
            in_use = len(children)
        else:
            in_use = _in_process_running_count()
        if in_use >= cap:
            return started_any
        claimed = claim_next_fair_job()
        if not claimed:
            return started_any
        job_id, account_id = claimed
        if isolate:
            job_tasks.register_isolated_child_job(job_id, account_id)
            try:
                proc = _spawn_isolated_job_async(job_id, account_id)
            except Exception:  # noqa: BLE001
                job_tasks.unregister_isolated_child_job(job_id)
                _requeue_if_still_processing(job_id, account_id)
                _LOG.exception("Failed to spawn isolated job job_id=%s", job_id)
                return started_any
            children[job_id] = (proc, account_id)
            started_any = True
            continue
        try:
            started = job_tasks.start_job_in_background(
                job_id, account_id, already_claimed=True
            )
        except RuntimeError as exc:
            _LOG.warning("Could not start job_id=%s: %s", job_id, exc)
            _requeue_if_still_processing(job_id, account_id)
            return started_any
        if not started:
            _requeue_if_still_processing(job_id, account_id)
            return started_any
        _LOG.info("Claimed catalog job job_id=%s account_id=%s", job_id, account_id)
        started_any = True


def run_worker_loop(poll_seconds: float | None = None) -> None:
    """Poll Postgres for queued catalog jobs and run up to N of them in this process."""
    os.environ["WORKER_POLL_LOOP"] = "1"
    interval = _poll_seconds() if poll_seconds is None else poll_seconds
    if not settings.run_catalog_jobs_in_process:
        _LOG.error(
            "RUN_CATALOG_JOBS_IN_PROCESS is false — this worker cannot claim jobs. "
            "Set it true (or unset) on the worker service."
        )
        raise SystemExit(2)

    isolate = bool(settings.worker_isolate_jobs)
    cap = worker_slot_capacity()
    _LOG.info(
        "Catalog worker starting poll=%.1fs worker_max_concurrent=%s account_max_concurrent=%s "
        "gemini_global_concurrency=%s gemini_parallelism=%s isolate_jobs=%s",
        interval,
        cap,
        settings.catalog_job_max_concurrent,
        settings.gemini_global_concurrency,
        settings.gemini_parallelism,
        isolate,
    )

    # Previous container life may have left rows in ``processing`` with no live thread.
    # Advisory-lock probe (Postgres) avoids stealing in-flight jobs from other replicas.
    try:
        boot_freed = _requeue_orphaned_processing(min_age_seconds=30)
        if boot_freed:
            _LOG.info("Boot re-queued %d orphan processing job(s)", boot_freed)
    except Exception:  # noqa: BLE001
        _LOG.exception("boot orphan processing reconcile failed")

    children: dict[str, tuple[subprocess.Popen, str]] = {}
    idle_sweeps = 0
    while True:
        if isolate:
            _reap_isolated_children(children)
        try:
            freed = _requeue_orphaned_processing()
            if freed:
                _LOG.info("Re-queued %d orphan processing job(s) before claim", freed)
        except Exception:  # noqa: BLE001
            _LOG.exception("orphan processing reconcile failed")
        started = _fill_worker_slots(isolate=isolate, children=children, cap=cap)
        if isolate:
            busy = bool(children)
        else:
            busy = _in_process_running_count() > 0
        if started or busy:
            idle_sweeps = 0
            time.sleep(0.4 if isolate else 0.5)
            continue
        idle_sweeps += 1
        idle_sweeps = _maybe_idle_maintenance(idle_sweeps, interval)
        time.sleep(interval)


def main(argv: list[str] | None = None) -> None:
    isolated = parse_worker_argv(argv if argv is not None else sys.argv)
    if isolated is not None:
        job_id, account_id = isolated
        raise SystemExit(run_isolated_job(job_id, account_id))
    run_worker_loop()


if __name__ == "__main__":
    main()
