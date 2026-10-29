from __future__ import annotations

import logging
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import update

from app.config import settings
from app.db import SessionLocal
from app.models import JobStatus, Lot, ProcessingJob, Sale
from app.services import job_events
from app.services.mobile_drafts import cataloged_mobile_draft_ids, mobile_draft_id_from_original_name
from app.services.processing import process_job

_LOG = logging.getLogger(__name__)

# Jobs stuck in ``processing`` longer than this may be reconciled as failed (API restart / OOM).
# Shorter values false-fail long Add-one-lot runs on another Railway replica.
PROCESSING_STALE_SECONDS = 3600

# On the dedicated worker only: ``processing`` rows not tracked in this process are orphans
# (worker crash/redeploy). Re-queue so new Resend/Sync jobs are not blocked forever behind
# a dead capacity slot. Do NOT use this on the API process — there ``is_job_running`` is
# always false for worker-owned jobs. Steady-state grace must exceed long Gemini calls
# (heartbeats are not per-token); boot reconcile uses age 0 after restart.
WORKER_ORPHAN_PROCESSING_SECONDS = 20 * 60

# Dedicated worker should claim within a couple of seconds. If a job is still queued after
# this grace (and nothing is processing), the API starts it so field uploads do not sit forever.
WORKER_CLAIM_GRACE_SECONDS = 8
# Dashboard + phones hit GET /jobs often while lots are queued. Each kick opens extra
# sessions; debounce so poll storms cannot stack QueuePool checkouts.
KICK_QUEUE_DEBOUNCE_SECONDS = 5.0
# If Sync dies mid-lot, ``updated_at`` stops advancing — fail those orphans so the UI
# is not blocked and the phone can re-upload cleanly. Active uploads bump ``updated_at``
# on each append (see ``append_files_to_queued_job``). When a later retry already
# cataloged the same SKU/draft, mark the leftover **completed** instead of failed.
RECEIVING_STALE_SECONDS = 30 * 60
# Queued jobs should be claimed within seconds; 10 minutes means the worker is down or wedged.
QUEUED_STALE_SECONDS = 10 * 60

# Dashboard copy + worker fail text for mid-upload / lost ephemeral disk. Gemini and
# Cancelled failures must not match — those still need staff attention.
_ORPHAN_UPLOAD_ERROR_MARKERS = (
    "input folder not found",
    "upload disk was lost",
    "re-sync from the phone",
    "upload never finished",
    "sync stopped mid-lot",
)

_job_cancels: dict[str, threading.Event] = {}
_job_accounts: dict[str, str] = {}
_job_cancels_lock = threading.Lock()
_kick_debounce_lock = threading.Lock()
_last_kick_mono: dict[str, float] = {}
# Dashboard + phones hit GET /jobs often during Sync. Reconcile at most this often per account
# so leftover-job healing cannot stack QueuePool work on every poll.
LIST_RECONCILE_DEBOUNCE_SECONDS = 20.0
_list_reconcile_lock = threading.Lock()
_last_list_reconcile_mono: dict[str, float] = {}


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
    """Drop in-memory slots that are not backed by a DB ``processing`` row."""
    live_processing = _processing_job_ids_in_db(account_id)
    with _job_cancels_lock:
        for job_id in list(_job_accounts.keys()):
            if _job_accounts.get(job_id) != account_id:
                continue
            if job_id not in live_processing:
                _job_cancels.pop(job_id, None)
                _job_accounts.pop(job_id, None)


def effective_running_count(account_id: str) -> int:
    reconcile_memory_threads_for_account(account_id)
    return len(_processing_job_ids_in_db(account_id))


def can_start_job_for_account(account_id: str) -> bool:
    cap = max(1, settings.catalog_job_max_concurrent)
    return effective_running_count(account_id) < cap


def _claim_queued_job(job_id: str, account_id: str) -> bool:
    """Atomically mark a queued job as processing before the worker thread starts."""
    with SessionLocal() as db:
        result = db.execute(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job_id,
                ProcessingJob.account_id == account_id,
                ProcessingJob.status == JobStatus.queued,
            )
            .values(
                status=JobStatus.processing,
                error_message=None,
                cancel_requested=False,
            )
        )
        if (result.rowcount or 0) != 1:
            db.rollback()
            return False
        db.commit()
        return True


def reconcile_stale_processing_jobs(db, account_id: str) -> bool:
    """
    Mark orphan ``processing`` rows failed when they are not running locally **and** the row is stale.

    On multi-instance deploys, ``is_job_running`` is false on every replica except the worker host,
    so we must not fail jobs that were updated recently (another replica may still be cataloging).
    """
    stale_rows = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.account_id == account_id,
            ProcessingJob.status == JobStatus.processing,
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    changed = False
    for row in stale_rows:
        if is_job_running(row.id):
            continue
        updated = row.updated_at
        if updated is not None:
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age_sec = (now - updated).total_seconds()
            if age_sec < PROCESSING_STALE_SECONDS:
                continue
        row.status = JobStatus.failed
        if not row.error_message:
            row.error_message = "Job interrupted after server restart. Re-run to continue."
        row.cancel_requested = False
        changed = True
    return changed


def requeue_stale_processing_for_account(
    db, account_id: str, *, min_age_seconds: int = 10 * 60
) -> int:
    """
    Emergency capacity free for dashboard **Run now** on the API.

    Unlike the worker helper, this does not use ``is_job_running`` (always false on the API
    for worker-owned jobs). Only touches rows older than ``min_age_seconds``.
    """
    rows = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.account_id == account_id,
            ProcessingJob.status == JobStatus.processing,
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    n = 0
    grace = max(0, int(min_age_seconds))
    for row in rows:
        updated = row.updated_at or row.created_at
        if grace > 0 and updated is not None:
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if (now - updated).total_seconds() < grace:
                continue
        row.status = JobStatus.queued
        row.error_message = None
        row.cancel_requested = False
        n += 1
        _LOG.warning(
            "Run-now re-queued stale processing job job_id=%s account_id=%s",
            row.id,
            account_id,
        )
    return n


def reconcile_worker_orphaned_processing_jobs(
    db, *, min_age_seconds: int | None = None
) -> int:
    """
    Re-queue ``processing`` jobs this worker process is not running.

    After a worker crash/redeploy, DB rows can stay ``processing`` while nothing is
    alive. ``can_start_job_for_account`` then thinks capacity is full and never claims
    new ``queued`` Resend jobs. Safe only on the dedicated worker (local ``is_job_running``).

    Use ``min_age_seconds=0`` on worker boot; steady-state defaults to
    ``WORKER_ORPHAN_PROCESSING_SECONDS``.
    """
    grace = (
        WORKER_ORPHAN_PROCESSING_SECONDS if min_age_seconds is None else max(0, int(min_age_seconds))
    )
    rows = (
        db.query(ProcessingJob)
        .filter(ProcessingJob.status == JobStatus.processing)
        .all()
    )
    now = datetime.now(timezone.utc)
    n = 0
    for row in rows:
        if is_job_running(row.id):
            continue
        updated = row.updated_at or row.created_at
        if grace > 0 and updated is not None:
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if (now - updated).total_seconds() < grace:
                continue
        row.status = JobStatus.queued
        row.error_message = None
        row.cancel_requested = False
        n += 1
        _LOG.warning(
            "Re-queued orphan processing job job_id=%s account_id=%s (not running on this worker)",
            row.id,
            row.account_id,
        )
    return n


def _sku_match_keys(raw: str | None) -> set[str]:
    """Comparable SKU tokens (case-fold + alphanumeric compact)."""
    s = (raw or "").strip()
    if not s:
        return set()
    keys = {s.casefold()}
    compact = "".join(ch for ch in s if ch.isalnum()).upper()
    if compact:
        keys.add(compact)
        keys.add(compact.casefold())
    return keys


def _skus_overlap(left: str | None, right: str | None) -> bool:
    a = _sku_match_keys(left)
    b = _sku_match_keys(right)
    return bool(a and b and a & b)


def is_orphan_upload_error(msg: str | None) -> bool:
    text = (msg or "").lower()
    return any(marker in text for marker in _ORPHAN_UPLOAD_ERROR_MARKERS)


def normalize_client_draft_id(raw: str | None) -> str | None:
    """Stable phone draft UUID (lowercase). Empty / whitespace is omitted."""
    s = (raw or "").strip().lower()
    if not s:
        return None
    return s[:36]


def job_activity_at(job: ProcessingJob) -> datetime | None:
    """Last progress/append time when present, else created_at."""
    ts = getattr(job, "updated_at", None) or getattr(job, "created_at", None)
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def job_age_seconds(job: ProcessingJob, now: datetime | None = None) -> float:
    ts = job_activity_at(job)
    if ts is None:
        return 0.0
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return max(0.0, (clock - ts).total_seconds())


def stuck_receiving_seconds() -> int:
    return int(getattr(settings, "job_stuck_receiving_seconds", RECEIVING_STALE_SECONDS) or RECEIVING_STALE_SECONDS)


def stuck_queued_seconds() -> int:
    return int(getattr(settings, "job_stuck_queued_seconds", QUEUED_STALE_SECONDS) or QUEUED_STALE_SECONDS)


def stuck_processing_seconds() -> int:
    return int(
        getattr(settings, "job_stuck_processing_seconds", PROCESSING_STALE_SECONDS) or PROCESSING_STALE_SECONDS
    )


def is_stuck_job(job: ProcessingJob, now: datetime | None = None) -> bool:
    """True when a catalog ProcessingJob looks wedged for the dashboard / phone banner.

    Thresholds (settings, with the same defaults as stale reconcile where those exist):
    receiving > 30 min, queued > 10 min, processing > 60 min (last updated/progress),
    or failed with leftover mid-upload / lost-disk wording.
    """
    status = job.status
    if status == JobStatus.failed:
        return is_orphan_upload_error(job.error_message)
    if status == JobStatus.receiving:
        return job_age_seconds(job, now) > stuck_receiving_seconds()
    if status == JobStatus.queued:
        return job_age_seconds(job, now) > stuck_queued_seconds()
    if status == JobStatus.processing:
        return job_age_seconds(job, now) > stuck_processing_seconds()
    return False


def _job_folder_mobile_draft_ids(job: ProcessingJob) -> set[str]:
    """Draft ids from leftover local filenames — never hits R2 (GET /jobs must stay cheap)."""
    folder_s = getattr(job, "input_folder", None)
    if not folder_s:
        return set()
    try:
        folder = Path(folder_s)
        if not folder.is_dir():
            return set()
        found: set[str] = set()
        for path in folder.iterdir():
            if not path.is_file():
                continue
            draft_id = mobile_draft_id_from_original_name(path.name)
            if draft_id:
                found.add(draft_id)
        return found
    except OSError:
        return set()


def job_mobile_draft_ids(job: ProcessingJob) -> set[str]:
    """Phone draft ids for this job: stored ``client_draft_id`` first, then filename fallback."""
    found: set[str] = set()
    stored = normalize_client_draft_id(getattr(job, "client_draft_id", None))
    if stored:
        found.add(stored)
    found |= _job_folder_mobile_draft_ids(job)
    return found


def _mark_job_superseded_complete(row: ProcessingJob) -> None:
    row.status = JobStatus.completed
    row.error_message = None
    row.cancel_requested = False
    _LOG.info(
        "Leftover job marked completed — lot already on sale job_id=%s sale_id=%s sku=%s",
        row.id,
        getattr(row, "sale_id", None),
        getattr(row, "single_lot_manual_sku", None),
    )


def _sale_outcome_index(
    db, account_id: str, sale_id: str
) -> tuple[set[str], set[str], set[str]]:
    """One-shot SKU + cataloged-draft lookup for leftover-job healing (avoid N+1 on GET /jobs)."""
    lot_keys: set[str] = set()
    for (sku,) in (
        db.query(Lot.sku)
        .filter(
            Lot.account_id == account_id,
            Lot.sale_id == sale_id,
            Lot.sku.isnot(None),
        )
        .all()
    ):
        lot_keys |= _sku_match_keys(sku)
    completed_keys: set[str] = set()
    for (sku,) in (
        db.query(ProcessingJob.single_lot_manual_sku)
        .filter(
            ProcessingJob.account_id == account_id,
            ProcessingJob.sale_id == sale_id,
            ProcessingJob.status == JobStatus.completed,
            ProcessingJob.single_lot_manual_sku.isnot(None),
        )
        .all()
    ):
        completed_keys |= _sku_match_keys(sku)
    drafts = set(cataloged_mobile_draft_ids(db, str(account_id), str(sale_id)))
    return lot_keys, completed_keys, drafts


def job_outcome_already_on_sale(
    db, job: ProcessingJob, *, index_cache: dict[tuple[str, str], tuple[set[str], set[str], set[str]]] | None = None
) -> bool:
    """True when this upload's SKU or phone draft already produced a catalog lot on the sale.

    Phone Sync often starts a new job after a mid-lot kill; the abandoned row must not keep
    showing FAILED once the retry (or an earlier run) already listed the item.
    """
    sale_id = getattr(job, "sale_id", None)
    account_id = getattr(job, "account_id", None)
    if not sale_id or not account_id:
        return False
    cache_key = (str(account_id), str(sale_id))
    if index_cache is not None and cache_key in index_cache:
        lot_keys, completed_keys, cataloged_drafts = index_cache[cache_key]
    else:
        lot_keys, completed_keys, cataloged_drafts = _sale_outcome_index(
            db, str(account_id), str(sale_id)
        )
        if index_cache is not None:
            index_cache[cache_key] = (lot_keys, completed_keys, cataloged_drafts)

    job_sku_keys = _sku_match_keys(getattr(job, "single_lot_manual_sku", None))
    if job_sku_keys and (job_sku_keys & lot_keys or job_sku_keys & completed_keys):
        return True

    drafts = job_mobile_draft_ids(job)
    if drafts and drafts & cataloged_drafts:
        return True
    return False


def reconcile_stale_receiving_jobs(db, account_id: str) -> bool:
    """
    Close ``receiving`` jobs with no recent append activity (orphaned mid-upload).

    Live uploads touch ``updated_at`` on each photo append. Stale rows are phone/Sync
    kills. If a later retry already cataloged the lot, mark **completed**; otherwise
    mark failed so staff can delete and the phone can Sync the full lot again.
    """
    rows = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.account_id == account_id,
            ProcessingJob.status == JobStatus.receiving,
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    changed = False
    index_cache: dict[tuple[str, str], tuple[set[str], set[str], set[str]]] = {}
    for row in rows:
        updated = row.updated_at or row.created_at
        if updated is not None:
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age_sec = (now - updated).total_seconds()
            if age_sec < RECEIVING_STALE_SECONDS:
                continue
        if job_outcome_already_on_sale(db, row, index_cache=index_cache):
            _mark_job_superseded_complete(row)
        else:
            row.status = JobStatus.failed
            row.error_message = (
                "Upload never finished (phone Sync stopped mid-lot). "
                "Delete this job, then Sync/Resend the lot from the phone."
            )
            row.cancel_requested = False
        changed = True
    return changed


def reconcile_superseded_failed_jobs(db, account_id: str) -> bool:
    """Complete leftover FAILED orphan-upload rows whose lot is already on the sale.

    Does not touch Gemini / Cancelled / restart failures — only the mid-Sync messages
    that remain after a successful retry already listed the SKU.
    """
    rows = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.account_id == account_id,
            ProcessingJob.status == JobStatus.failed,
        )
        .all()
    )
    changed = False
    index_cache: dict[tuple[str, str], tuple[set[str], set[str], set[str]]] = {}
    for row in rows:
        if not is_orphan_upload_error(row.error_message):
            continue
        if not job_outcome_already_on_sale(db, row, index_cache=index_cache):
            continue
        _mark_job_superseded_complete(row)
        changed = True
    return changed


def maybe_reconcile_jobs_for_list(db, account_id: str) -> bool:
    """Run stale/leftover job healing at most once per account per debounce window.

    ``GET /jobs`` is polled by the dashboard and by phone Sync. Doing full reconcile
    (including leftover FAILED SKU matching) on every poll exhausted QueuePool during
    field uploads and froze the website on “Loading dashboard…”.
    """
    now = time.monotonic()
    with _list_reconcile_lock:
        last = _last_list_reconcile_mono.get(account_id, 0.0)
        if now - last < LIST_RECONCILE_DEBOUNCE_SECONDS:
            return False
        _last_list_reconcile_mono[account_id] = now
    changed = reconcile_stale_processing_jobs(db, account_id)
    changed = reconcile_stale_receiving_jobs(db, account_id) or changed
    changed = reconcile_superseded_failed_jobs(db, account_id) or changed
    return changed


def list_stuck_jobs(
    db, account_id: str, sale_id: str | None = None, now: datetime | None = None
) -> list[ProcessingJob]:
    """Account-scoped stuck catalog jobs (same tenancy as ``GET /jobs``). Oldest activity last."""
    q = db.query(ProcessingJob).filter(ProcessingJob.account_id == account_id)
    if sale_id:
        q = q.filter(ProcessingJob.sale_id == sale_id)
    rows = q.order_by(ProcessingJob.created_at.desc()).all()
    stuck = [row for row in rows if is_stuck_job(row, now=now)]
    stuck.sort(key=lambda row: job_age_seconds(row, now), reverse=True)
    return stuck


def sale_job_status_counts(
    db, account_id: str, sale_id: str
) -> dict[str, int]:
    """Counts of catalog jobs on one sale, keyed by ``JobStatus.value``."""
    counts = {status.value: 0 for status in JobStatus}
    rows = (
        db.query(ProcessingJob.status)
        .filter(ProcessingJob.account_id == account_id, ProcessingJob.sale_id == sale_id)
        .all()
    )
    for (status,) in rows:
        key = status.value if isinstance(status, JobStatus) else str(status)
        if key in counts:
            counts[key] += 1
        else:
            counts[key] = counts.get(key, 0) + 1
    return counts


def jobs_with_client_draft_id(db, account_id: str, sale_id: str) -> list[ProcessingJob]:
    return (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.account_id == account_id,
            ProcessingJob.sale_id == sale_id,
            ProcessingJob.client_draft_id.isnot(None),
        )
        .order_by(ProcessingJob.created_at.desc())
        .all()
    )


def sale_names_for_ids(db, sale_ids: list[str]) -> dict[str, str]:
    if not sale_ids:
        return {}
    rows = db.query(Sale.id, Sale.name).filter(Sale.id.in_(sale_ids)).all()
    return {str(sid): str(name or "") for sid, name in rows}


def _release_claimed_job(job_id: str, account_id: str) -> None:
    with SessionLocal() as db:
        row = (
            db.query(ProcessingJob)
            .filter(
                ProcessingJob.id == job_id,
                ProcessingJob.account_id == account_id,
                ProcessingJob.status == JobStatus.processing,
            )
            .first()
        )
        if row is None:
            return
        row.status = JobStatus.queued
        db.commit()


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


def _clear_stale_registration(job_id: str, account_id: str) -> None:
    if job_id in _processing_job_ids_in_db(account_id):
        return
    with _job_cancels_lock:
        if _job_accounts.get(job_id) == account_id:
            _job_cancels.pop(job_id, None)
            _job_accounts.pop(job_id, None)


def _stage_job_folder_from_r2(job_id: str, folder_s: str, account_id: str | None = None) -> None:
    """Restore/mirror the batch without holding a QueuePool connection."""
    try:
        from app.job_batch_storage import ensure_job_input_folder, mirror_folder_to_r2

        folder = Path(folder_s)
        if folder.is_dir():
            mirror_folder_to_r2(folder, account_id=account_id)
        ensure_job_input_folder(folder, account_id=account_id)
    except Exception:
        _LOG.exception("job-batch R2 ensure failed job_id=%s", job_id)


def _background_runner(job_id: str, account_id: str, cancel_ev: threading.Event) -> None:
    try:
        folder_s: str | None = None
        with SessionLocal() as db:
            job = (
                db.query(ProcessingJob)
                .filter(ProcessingJob.id == job_id, ProcessingJob.account_id == account_id)
                .first()
            )
            if job is None or job.status not in (JobStatus.queued, JobStatus.processing):
                return
            folder_s = job.input_folder
        if folder_s:
            _stage_job_folder_from_r2(job_id, folder_s, account_id=account_id)
        with SessionLocal() as db:
            job = (
                db.query(ProcessingJob)
                .filter(ProcessingJob.id == job_id, ProcessingJob.account_id == account_id)
                .first()
            )
            if job is None or job.status not in (JobStatus.queued, JobStatus.processing):
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
                        job.account_id,
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
        from app.services.memory_trim import trim_process_memory

        trim_process_memory()
        if os.environ.get("WORKER_ISOLATED_CHILD") == "1":
            return
        try_start_next_queued_job(account_id)


def catalog_jobs_run_in_process() -> bool:
    """False on the API when a dedicated worker service owns Gemini catalog jobs."""
    return bool(settings.run_catalog_jobs_in_process)


def start_job_in_background(
    job_id: str, account_id: str, *, force_in_process: bool = False
) -> bool:
    """
    Start a queued job when account capacity allows.
    Returns False when the job should stay queued (another lot is still running),
    or when this process is configured not to run catalog jobs (dedicated worker mode).

    ``force_in_process=True`` (dashboard **Run now**) starts on this API process even when
    ``RUN_CATALOG_JOBS_IN_PROCESS=false``, so staff are not stuck if the worker queue is wedged.
    """
    if not force_in_process and not catalog_jobs_run_in_process():
        _LOG.debug(
            "Skipping in-process start job_id=%s (RUN_CATALOG_JOBS_IN_PROCESS=false)",
            job_id,
        )
        return False

    reconcile_memory_threads_for_account(account_id)
    if not can_start_job_for_account(account_id):
        return False

    _clear_stale_registration(job_id, account_id)

    with _job_cancels_lock:
        if job_id in _job_cancels:
            raise RuntimeError("job_already_running")
        cancel_ev = threading.Event()
        _job_cancels[job_id] = cancel_ev
        _job_accounts[job_id] = account_id

    if not _claim_queued_job(job_id, account_id):
        with _job_cancels_lock:
            _job_cancels.pop(job_id, None)
            _job_accounts.pop(job_id, None)
        return False

    try:
        threading.Thread(
            target=_background_runner,
            args=(job_id, account_id, cancel_ev),
            daemon=True,
            name=f"job-{job_id}",
        ).start()
    except Exception:
        _unregister(job_id)
        _release_claimed_job(job_id, account_id)
        raise
    return True


def try_start_next_queued_job(account_id: str, *, force_in_process: bool = False) -> None:
    """After a job finishes (or the queue was idle), start the oldest queued job for this account."""
    if not force_in_process and not catalog_jobs_run_in_process():
        return
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
        if not start_job_in_background(job_id, account_id, force_in_process=force_in_process):
            return
    except RuntimeError:
        _clear_stale_registration(job_id, account_id)
        try:
            if not start_job_in_background(job_id, account_id, force_in_process=force_in_process):
                return
        except RuntimeError:
            return
    _LOG.info("Started queued catalog job job_id=%s account_id=%s", job_id, account_id)


def _oldest_queued_job(account_id: str) -> tuple[str, float] | None:
    """Return ``(job_id, age_seconds)`` for the oldest queued job, if any."""
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
            return None
        updated = row.updated_at or row.created_at
        age = 0.0
        if updated is not None:
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = max(0.0, (datetime.now(timezone.utc) - updated).total_seconds())
        return str(row.id), age


def kick_queue_if_idle(account_id: str) -> None:
    """Start waiting queued jobs when nothing is processing (e.g. after API restart or stale reconcile).

    In dedicated-worker mode the worker normally claims within a couple of seconds. If a job is
    still queued after ``WORKER_CLAIM_GRACE_SECONDS`` with no processing slot in use, start it
    on this API process (same as dashboard Run now) so phone uploads do not sit forever.
    """
    reconcile_memory_threads_for_account(account_id)
    if effective_running_count(account_id) > 0:
        return

    if catalog_jobs_run_in_process():
        queued = _oldest_queued_job(account_id)
        if queued:
            try_start_next_queued_job(account_id)
        return

    queued = _oldest_queued_job(account_id)
    if queued is None:
        return
    job_id, age = queued
    if age < WORKER_CLAIM_GRACE_SECONDS:
        return
    _LOG.warning(
        "Worker did not claim queued job after %.0fs — starting on API job_id=%s account_id=%s",
        age,
        job_id,
        account_id,
    )
    try_start_next_queued_job(account_id, force_in_process=True)


def schedule_worker_claim_fallback(account_id: str, job_id: str) -> None:
    """After phone/dashboard ``/run`` in worker mode, start the job here if the worker never claims it."""
    if catalog_jobs_run_in_process():
        return

    def _wait_then_kick() -> None:
        time.sleep(WORKER_CLAIM_GRACE_SECONDS)
        try:
            kick_queue_if_idle(account_id)
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "worker-claim fallback failed job_id=%s account_id=%s", job_id, account_id
            )

    threading.Thread(
        target=_wait_then_kick,
        daemon=True,
        name=f"wk-fb-{job_id[:8]}",
    ).start()


def kick_queue_if_idle_async(account_id: str) -> None:
    """Non-blocking wrapper for ``list_jobs`` — must not delay dashboard load."""
    now = time.monotonic()
    with _kick_debounce_lock:
        last = _last_kick_mono.get(account_id, 0.0)
        if now - last < KICK_QUEUE_DEBOUNCE_SECONDS:
            return
        _last_kick_mono[account_id] = now
    threading.Thread(
        target=kick_queue_if_idle,
        args=(account_id,),
        daemon=True,
        name=f"kick-q-{account_id[:8]}",
    ).start()
