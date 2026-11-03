"""Dedicated catalog worker — claims queued ProcessingJob rows and runs Gemini off the API process.

Run from the ``backend`` working directory (same image as the API):

    python -m app.worker

On Railway: add a second service from ``infra/docker/Dockerfile.worker``, copy the API env
(DATABASE_URL, GOOGLE_API_KEY, R2_*, SECRET_KEY, …), and set on the **API**:

    RUN_CATALOG_JOBS_IN_PROCESS=false

Leave ``RUN_CATALOG_JOBS_IN_PROCESS`` unset/true on the worker (default).

By default each catalog job runs in a **child process** (``WORKER_ISOLATE_JOBS=true``) so
decoded JPEG RAM is released when the child exits. The parent keeps at most
``WORKER_MAX_CONCURRENT_JOBS`` live children (default 3) and must not import the
Gemini pipeline. Cap the worker replica at 4 GB.

Railway's default Metrics **Sum** view adds every replica. Two idle replicas at
100 MB each show as 200 MB. Use **Replica** view to right-size the 4 GB limit.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

# Limit glibc arenas before the interpreter allocates (Railway Linux). Harmless elsewhere.
os.environ.setdefault("MALLOC_ARENA_MAX", "2")

from app import job_tasks
from app.config import settings
from app.core.catalog_crash import apply_child_crash
from app.db import SessionLocal
from app.job_queue import claim_next_fair_job
from app.models import JobStatus, Lot, ProcessingJob
from app.proc_memory import process_rss_mb, process_tree_rss_mb
from app.services import job_events
from app.services.memory_trim import trim_process_memory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(message)s",
)
_LOG = logging.getLogger("app.worker")

_IDLE_MEMORY_LOG_SECONDS = 60.0


@dataclass
class IsolatedChild:
    """One live ``--run-job`` subprocess. Not a reused multiprocessing pool."""

    proc: subprocess.Popen
    account_id: str
    started_mono: float
    peak_rss_mb: float = 0.0
    extra: dict = field(default_factory=dict)


def parse_worker_argv(argv: list[str]) -> tuple[str, str] | None:
    """Return ``(job_id, account_id)`` when invoked as ``--run-job <id> <account>``."""
    if len(argv) >= 4 and argv[1] == "--run-job":
        job_id = (argv[2] or "").strip()
        account_id = (argv[3] or "").strip()
        if job_id and account_id:
            return job_id, account_id
    return None


def parse_self_test_alloc_mb(argv: list[str]) -> int | None:
    """Return MiB to allocate when invoked as ``--self-test-alloc-mb N`` (tests only)."""
    if len(argv) >= 3 and argv[1] == "--self-test-alloc-mb":
        try:
            return max(1, int(argv[2]))
        except ValueError:
            return None
    return None


def parse_self_test_parent_jobs(argv: list[str]) -> tuple[int, int] | None:
    """``--self-test-parent-jobs <count> <alloc-mb>`` — sequential children, then print RSS JSON."""
    if len(argv) >= 4 and argv[1] == "--self-test-parent-jobs":
        try:
            return max(1, int(argv[2])), max(1, int(argv[3]))
        except ValueError:
            return None
    return None


def _poll_seconds() -> float:
    raw = os.environ.get("WORKER_POLL_SECONDS", "2")
    try:
        return max(0.5, min(30.0, float(raw)))
    except ValueError:
        return 2.0


def worker_slot_capacity() -> int:
    return max(1, int(settings.worker_max_concurrent_jobs))


def child_rss_cap_mb() -> int:
    return max(0, int(getattr(settings, "worker_child_rss_mb", 0) or 0))


def _requeue_orphaned_processing(*, min_age_seconds: int | None = None) -> int:
    """Unblock the queue after worker crash — see ``reconcile_worker_orphaned_processing_jobs``."""
    with SessionLocal() as db:
        n = job_tasks.reconcile_worker_orphaned_processing_jobs(
            db, min_age_seconds=min_age_seconds
        )
        if n:
            db.commit()
        return n


def _apply_child_as_limit() -> None:
    """Optional ``RLIMIT_AS`` on the isolated child. RSS monitoring is the primary guard."""
    cap_mb = int(getattr(settings, "worker_child_as_mb", 0) or 0)
    if cap_mb <= 0:
        return
    try:
        import resource

        nbytes = cap_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (nbytes, nbytes))
        _LOG.info("Isolated child RLIMIT_AS=%s MB", cap_mb)
    except Exception:  # noqa: BLE001
        _LOG.warning("Could not set RLIMIT_AS cap_mb=%s", cap_mb, exc_info=True)


def _requeue_if_still_processing(
    job_id: str, account_id: str, *, reason: str = "isolated worker child exited"
) -> bool:
    """If a child died while the row is still ``processing``, re-queue or fail after N crashes."""
    with SessionLocal() as db:
        row = (
            db.query(ProcessingJob)
            .filter(ProcessingJob.id == job_id, ProcessingJob.account_id == account_id)
            .first()
        )
        if row is None or row.status != JobStatus.processing:
            return False
        outcome = apply_child_crash(row, reason=reason)
        db.commit()
        try:
            job_events.publish(
                account_id,
                job_id,
                {
                    "type": "status",
                    "status": outcome,
                    "error_message": row.error_message,
                    "job_id": job_id,
                },
            )
        except Exception:  # noqa: BLE001
            _LOG.debug("crash-status SSE publish failed job_id=%s", job_id, exc_info=True)
        if outcome == "failed":
            _LOG.error(
                "Catalog job failed after child crashes job_id=%s account_id=%s error=%s",
                job_id,
                account_id,
                row.error_message,
            )
        else:
            _LOG.warning(
                "Re-queued catalog job after child crash job_id=%s account_id=%s error=%s",
                job_id,
                account_id,
                row.error_message,
            )
        return True


def _job_finish_stats(job_id: str, account_id: str) -> tuple[str, int, int]:
    """Return ``(status, photos, lots)`` for the just-finished child."""
    try:
        from sqlalchemy import func

        with SessionLocal() as db:
            row = (
                db.query(ProcessingJob)
                .filter(ProcessingJob.id == job_id, ProcessingJob.account_id == account_id)
                .first()
            )
            if row is None:
                return "missing", 0, 0
            status = str(getattr(row.status, "value", row.status) or "unknown")
            photos = int(row.analyzed_photos or 0)
            lots = 0
            if getattr(row, "single_lot_mode", False):
                lots = 1 if status == "completed" else 0
            elif row.sale_id and row.created_at:
                lots = int(
                    db.query(func.count(Lot.id))
                    .filter(
                        Lot.sale_id == row.sale_id,
                        Lot.account_id == account_id,
                        Lot.created_at >= row.created_at,
                    )
                    .scalar()
                    or 0
                )
            return status, photos, lots
    except Exception:  # noqa: BLE001
        _LOG.debug("job finish stats failed job_id=%s", job_id, exc_info=True)
        return "unknown", 0, 0


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
    """Child entry: run one already-claimed job, then return so ``main`` can hard-exit."""
    os.environ["WORKER_ISOLATED_CHILD"] = "1"
    _apply_child_as_limit()
    started = time.monotonic()
    try:
        started_ok = job_tasks.start_job_in_background(
            job_id, account_id, already_claimed=True
        )
    except RuntimeError as exc:
        _LOG.warning("Could not start isolated job_id=%s: %s", job_id, exc)
        return 2
    if not started_ok:
        return 3
    _LOG.info("Isolated catalog job running job_id=%s account_id=%s", job_id, account_id)
    while job_tasks.is_job_running(job_id):
        time.sleep(0.5)
    duration = time.monotonic() - started
    status, photos, lots = _job_finish_stats(job_id, account_id)
    rss = process_rss_mb()
    _LOG.info(
        "Isolated catalog job complete job_id=%s account_id=%s status=%s lots=%s photos=%s "
        "duration=%.1fs child_rss_mb=%.0f",
        job_id,
        account_id,
        status,
        lots,
        photos,
        duration,
        rss if rss is not None else -1,
    )
    return 0


def run_self_test_alloc(mb: int) -> int:
    """Allocate ``mb`` MiB, leave a non-daemon thread pool, then hard-exit (pytest helper)."""
    os.environ["WORKER_ISOLATED_CHILD"] = "1"
    from concurrent.futures import ThreadPoolExecutor

    # Intentionally not shut down — ``exit_isolated_child`` must still kill this process.
    _pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="self-test")
    blob = bytearray(int(mb) * 1024 * 1024)
    for i in range(0, len(blob), 4096):
        blob[i] = 1
    time.sleep(0.25)
    _LOG.info("self-test-alloc-mb done mb=%s rss_mb=%.0f", mb, process_rss_mb() or -1)
    del blob
    del _pool
    return 0


def run_self_test_parent_jobs(count: int, alloc_mb: int) -> int:
    """Parent-only helper: run ``count`` sequential alloc children and print RSS JSON."""
    import json

    trim_process_memory()
    before_parent = process_rss_mb()
    before_tree = process_tree_rss_mb()
    cmd = [sys.executable, "-m", "app.worker", "--self-test-alloc-mb", str(alloc_mb)]
    env = os.environ.copy()
    env.setdefault("MALLOC_ARENA_MAX", "2")
    env.setdefault("PYTHONUNBUFFERED", "1")
    for i in range(count):
        proc = subprocess.Popen(cmd, env=env, start_new_session=True, close_fds=True)
        children = {
            f"self-test-{i}": IsolatedChild(
                proc=proc, account_id="self-test", started_mono=time.monotonic()
            )
        }
        deadline = time.monotonic() + 25.0
        while children and time.monotonic() < deadline:
            _reap_isolated_children(children)
            time.sleep(0.05)
        if children:
            for slot in children.values():
                try:
                    slot.proc.kill()
                except OSError:
                    pass
            print(json.dumps({"error": "child_timeout", "index": i}), flush=True)
            return 2
        if proc.poll() != 0:
            print(json.dumps({"error": "child_exit", "code": proc.poll(), "index": i}), flush=True)
            return 3
    trim_process_memory()
    time.sleep(0.15)
    trim_process_memory()
    payload = {
        "jobs": count,
        "alloc_mb": alloc_mb,
        "before_parent_rss_mb": before_parent,
        "after_parent_rss_mb": process_rss_mb(),
        "before_tree_rss_mb": before_tree,
        "after_tree_rss_mb": process_tree_rss_mb(),
    }
    print(json.dumps(payload), flush=True)
    return 0


def _spawn_isolated_job_async(job_id: str, account_id: str) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "app.worker", "--run-job", job_id, account_id]
    env = os.environ.copy()
    env.setdefault("MALLOC_ARENA_MAX", "2")
    env.setdefault("PYTHONUNBUFFERED", "1")
    _LOG.info("Spawning isolated catalog job job_id=%s", job_id)
    # New session + closed inherited fds so a stuck child cannot hold parent pipes.
    return subprocess.Popen(cmd, env=env, start_new_session=True, close_fds=True)


def _sample_child_rss(job_id: str, slot: IsolatedChild) -> None:
    pid = getattr(slot.proc, "pid", None)
    if not pid:
        return
    rss = process_rss_mb(int(pid))
    if rss is None:
        return
    slot.peak_rss_mb = max(float(slot.peak_rss_mb or 0.0), rss)
    cap = child_rss_cap_mb()
    if cap and rss > cap:
        _LOG.error(
            "Killing catalog child over RSS cap job_id=%s pid=%s rss_mb=%.0f cap_mb=%s",
            job_id,
            pid,
            rss,
            cap,
        )
        try:
            slot.proc.kill()
        except OSError:
            pass


def _reap_isolated_children(children: dict[str, IsolatedChild]) -> None:
    done: list[str] = []
    for job_id, slot in children.items():
        proc = slot.proc
        account_id = slot.account_id
        code = proc.poll()
        if code is None:
            _sample_child_rss(job_id, slot)
            continue
        done.append(job_id)
        # poll() already reaped; wait() is idempotent and closes the handle.
        try:
            proc.wait(timeout=0)
        except Exception:  # noqa: BLE001
            pass
        duration = time.monotonic() - float(slot.started_mono)
        if slot.peak_rss_mb <= 0:
            rss = process_rss_mb(int(proc.pid)) if getattr(proc, "pid", None) else None
            if rss is not None:
                slot.peak_rss_mb = rss
        self_test = job_id.startswith("self-test") or account_id == "self-test"
        if self_test:
            status, photos, lots = "self-test", 0, 0
        else:
            status, photos, lots = _job_finish_stats(job_id, account_id)
        job_tasks.unregister_isolated_child_job(job_id)
        _LOG.info(
            "Isolated catalog child exited job_id=%s account_id=%s code=%s duration=%.1fs "
            "peak_child_rss_mb=%.0f lots=%s photos=%s status=%s",
            job_id,
            account_id,
            code,
            duration,
            slot.peak_rss_mb,
            lots,
            photos,
            status,
        )
        if code != 0:
            _LOG.warning("Isolated job job_id=%s exited %s", job_id, code)
        if self_test:
            continue
        try:
            _requeue_if_still_processing(job_id, account_id)
        except Exception:  # noqa: BLE001
            _LOG.exception("post-child requeue failed job_id=%s", job_id)
    if done:
        for job_id in done:
            children.pop(job_id, None)
        trim_process_memory()


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


def _log_worker_memory(*, label: str, children: dict[str, IsolatedChild]) -> None:
    parent = process_rss_mb()
    tree = process_tree_rss_mb()
    child_bits = []
    for job_id, slot in children.items():
        pid = getattr(slot.proc, "pid", None)
        live = process_rss_mb(int(pid)) if pid else None
        child_bits.append(
            f"{job_id[:8]}={live if live is not None else slot.peak_rss_mb:.0f}MB"
        )
    _LOG.info(
        "%s memory parent_rss_mb=%.0f tree_rss_mb=%.0f children=%s%s",
        label,
        parent if parent is not None else -1,
        tree,
        len(children),
        (" [" + ", ".join(child_bits) + "]") if child_bits else "",
    )


def _fill_worker_slots(
    *,
    isolate: bool,
    children: dict[str, IsolatedChild],
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
            children[job_id] = IsolatedChild(
                proc=proc, account_id=account_id, started_mono=time.monotonic()
            )
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
        "gemini_global_concurrency=%s gemini_parallelism=%s isolate_jobs=%s "
        "child_rss_cap_mb=%s malloc_arena_max=%s",
        interval,
        cap,
        settings.catalog_job_max_concurrent,
        settings.gemini_global_concurrency,
        settings.gemini_parallelism,
        isolate,
        child_rss_cap_mb(),
        os.environ.get("MALLOC_ARENA_MAX", ""),
    )

    # Previous container life may have left rows in ``processing`` with no live thread.
    # Advisory-lock probe (Postgres) avoids stealing in-flight jobs from other replicas.
    try:
        boot_freed = _requeue_orphaned_processing(min_age_seconds=30)
        if boot_freed:
            _LOG.info("Boot re-queued %d orphan processing job(s)", boot_freed)
    except Exception:  # noqa: BLE001
        _LOG.exception("boot orphan processing reconcile failed")

    children: dict[str, IsolatedChild] = {}
    idle_sweeps = 0
    last_memory_log = 0.0
    _log_worker_memory(label="Boot", children=children)
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
        now = time.monotonic()
        if now - last_memory_log >= _IDLE_MEMORY_LOG_SECONDS:
            _log_worker_memory(label="Idle" if not busy else "Busy", children=children)
            last_memory_log = now
            if not busy:
                trim_process_memory()
        if started or busy:
            idle_sweeps = 0
            time.sleep(0.4 if isolate else 0.5)
            continue
        idle_sweeps += 1
        idle_sweeps = _maybe_idle_maintenance(idle_sweeps, interval)
        time.sleep(interval)


def main(argv: list[str] | None = None) -> None:
    from app.services.worker_child_lifecycle import exit_isolated_child

    argv = list(argv if argv is not None else sys.argv)
    alloc_mb = parse_self_test_alloc_mb(argv)
    if alloc_mb is not None:
        code = run_self_test_alloc(alloc_mb)
        exit_isolated_child(code)
    parent_jobs = parse_self_test_parent_jobs(argv)
    if parent_jobs is not None:
        raise SystemExit(run_self_test_parent_jobs(*parent_jobs))
    isolated = parse_worker_argv(argv)
    if isolated is not None:
        job_id, account_id = isolated
        code = run_isolated_job(job_id, account_id)
        exit_isolated_child(code)
    run_worker_loop()


if __name__ == "__main__":
    main()
