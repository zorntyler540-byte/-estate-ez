from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, text, update
from sqlalchemy.orm import Session

from app.config import google_api_key, settings
from app.core.catalog_crash import processing_error_message_preserving_crash
from app.core.gemini import verify_tape_sku
from app.job_lock import job_advisory_lock_key
from app.core.lotting import (
    LotOutput,
    folder_token_as_inherited_sku,
    normalize_manual_sku_input,
    normalize_tape_sku_input,
    sku_digits,
    tape_marking_canonical,
    tape_sku_opens_new_lot,
    tape_sku_same_marking,
)
from app.models import JobStatus, Lot, Photo, ProcessingJob
from app.services.category_inference import resolve_lot_ct_category
from app.services.ctseller_conditions import canonicalize_ct_condition
from app.services.lot_dividers import find_lot_dividers
from app.services.memory_trim import trim_process_memory
from app.services.pipeline_integration import (
    is_weak_pipeline_lot,
    run_single_lot_pipeline,
    triage_ordered_photo_batch,
    triage_tape_marking_from_row,
)
from app.services.photo_order import reorder_photos_by_filenames, reorder_photos_for_listing
from app.services.photo_roles import (
    PhotoListingAttrs,
    listing_attrs_by_path,
    normalize_catalog_photo_role,
)
from app.services.listing_channels import normalize_listing_channel
from pipeline.pipeline import PipelineCancelled
from app.services.sku_sanity import recompute_sku_sanity_flags_for_sale
from app.services.upload_names import is_stable_upload_name, natural_path_key
from app.catalog_limits import CTBIDS_MAX_PHOTOS_PER_LOT, looks_like_video_file
from app.catalog_storage import (
    catalog_photo_file_on_disk,
    persist_catalog_photo,
    resolve_catalog_storage_path,
)
from app.services.image_preprocess import normalize_job_folder_image
from app.services import job_events

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}

_process_job_thread_locks: dict[str, threading.Lock] = {}
_process_job_thread_locks_guard = threading.Lock()

_LOG = logging.getLogger(__name__)


def _job_lock_key(job_id: str) -> int:
    return job_advisory_lock_key(job_id)


def _thread_job_process_lock(job_id: str) -> threading.Lock:
    with _process_job_thread_locks_guard:
        lock = _process_job_thread_locks.get(job_id)
        if lock is None:
            lock = threading.Lock()
            _process_job_thread_locks[job_id] = lock
        return lock


@contextmanager
def _process_job_execution_lock(db: Session, job_id: str):
    """One catalog worker per job id (Postgres advisory lock, in-process lock elsewhere)."""
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        key = _job_lock_key(job_id)
        acquired = bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())
        if not acquired:
            _LOG.warning("Skipping duplicate process_job — advisory lock held job_id=%s", job_id)
            yield False
            return
        try:
            yield True
        finally:
            db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
    else:
        lock = _thread_job_process_lock(job_id)
        if not lock.acquire(blocking=False):
            _LOG.warning("Skipping duplicate process_job — thread lock held job_id=%s", job_id)
            yield False
            return
        try:
            yield True
        finally:
            lock.release()


def _handle_transient_job_failure(
    db: Session,
    job: ProcessingJob,
    exc: BaseException,
    *,
    prior_attempts: int,
) -> None:
    """Re-queue a catalog job after a retryable Gemini failure, or mark it failed (still retryable)."""
    from app.core.gemini_retry import catalog_retry_exhausted_message, format_catalog_retry_message

    max_n = max(1, int(settings.catalog_job_transient_retries))
    nxt = int(prior_attempts) + 1
    if nxt < max_n:
        job.status = JobStatus.queued
        job.error_message = format_catalog_retry_message(nxt, max_n, exc)
        job.cancel_requested = False
        db.commit()
        _LOG.warning(
            "Re-queued catalog job after Gemini transient error job_id=%s attempt=%s/%s",
            job.id,
            nxt,
            max_n,
        )
        _emit_job(
            job,
            {
                "type": "status",
                "status": "queued",
                "error_message": job.error_message,
                "job_id": job.id,
            },
        )
        return
    job.status = JobStatus.failed
    job.error_message = catalog_retry_exhausted_message(max_n, exc)[:4000]
    job.cancel_requested = False
    db.commit()
    _LOG.error(
        "Catalog job failed after Gemini transient retries job_id=%s attempts=%s",
        job.id,
        max_n,
    )
    _emit_job(
        job,
        {
            "type": "status",
            "status": "failed",
            "error_message": job.error_message,
            "job_id": job.id,
        },
    )


def _heartbeat_job(db: Session, job: ProcessingJob) -> None:
    """Touch ``updated_at`` so multi-instance stale reconcile does not false-fail long runs."""
    job.status = JobStatus.processing
    job.updated_at = datetime.now(timezone.utc)
    db.commit()


def _photo_names(photo_paths: list[Path]) -> list[str]:
    return [p.name for p in photo_paths if p.name]


def _stable_names(names: list[str]) -> list[str]:
    return [n for n in names if is_stable_upload_name(n)]


# Fixed namespace: ``Photo.id`` for a job's input file is uuid5(job_id/relative path), so a
# retry of the same job finds its own rows without trusting camera filenames.
_JOB_PHOTO_NAMESPACE = uuid.UUID("5b7f3e4a-8f0e-4d55-9a57-2c1f0e1d6b42")


def _job_photo_sort_key(folder: Path, photo_path: Path) -> tuple:
    """Pick order for ``web-``/``mobile-`` names, shot order for camera names (natural sort)."""
    try:
        rel = photo_path.resolve().relative_to(folder.resolve()).as_posix()
    except (OSError, ValueError):
        rel = photo_path.name
    return natural_path_key(rel)


def _job_relative_name(job: ProcessingJob, photo_path: Path) -> str:
    try:
        return photo_path.resolve().relative_to(Path(job.input_folder).resolve()).as_posix()
    except (ValueError, OSError, TypeError):
        return photo_path.name


def job_photo_id(job: ProcessingJob, photo_path: Path) -> str:
    """Deterministic ``Photo.id`` for one input file of one job."""
    return str(uuid.uuid5(_JOB_PHOTO_NAMESPACE, f"{job.id}/{_job_relative_name(job, photo_path)}"))


def _job_photos_on_lots(db: Session, job: ProcessingJob, photo_paths: list[Path]) -> set[Path]:
    """Input files this job already attached to a lot (any sale — lots can move)."""
    by_id = {job_photo_id(job, p): p for p in photo_paths}
    if not by_id:
        return set()
    rows = (
        db.query(Photo.id)
        .filter(
            Photo.id.in_(list(by_id)),
            Photo.account_id == job.account_id,
            Photo.lot_id.isnot(None),
        )
        .all()
    )
    return {by_id[str(pid)] for (pid,) in rows if str(pid) in by_id}


def _already_cataloged_paths(db: Session, job: ProcessingJob, photo_paths: list[Path]) -> set[Path]:
    """Inputs that are already listed: by this job, or (stable names only) anywhere on the sale."""
    done = _job_photos_on_lots(db, job, photo_paths)
    on_sale = _sale_lot_photos_by_name(db, job, _photo_names(photo_paths))
    done.update(p for p in photo_paths if p.name in on_sale)
    return done


def _sale_lot_photos_by_name(db: Session, job: ProcessingJob, names: list[str]) -> dict[str, Photo]:
    """Lot-linked photos on this sale whose **stable** upload name matches (first wins).

    Camera names (``IMG_1234.JPG``, ``image.jpg``) are ignored: they repeat across phones and
    pick sessions, and matching on them silently dropped photos and whole lots.
    """
    names = _stable_names(names)
    if not names:
        return {}
    rows = (
        db.query(Photo)
        .join(Lot, Photo.lot_id == Lot.id)
        .filter(
            Lot.sale_id == job.sale_id,
            Lot.account_id == job.account_id,
            Photo.account_id == job.account_id,
            Photo.original_name.in_(names),
        )
        .all()
    )
    found: dict[str, Photo] = {}
    for row in rows:
        if row.original_name and row.original_name not in found:
            found[str(row.original_name)] = row
    return found


def _unlinked_job_photos_by_name(db: Session, job: ProcessingJob, names: list[str]) -> dict[str, Photo]:
    """Unlinked leftovers with the same stable name, created at/after the job.

    Fallback for rows persisted before ``job_photo_id``; camera names never match.
    """
    names = _stable_names(names)
    if not names:
        return {}
    q = db.query(Photo).filter(
        Photo.account_id == job.account_id,
        Photo.lot_id.is_(None),
        Photo.original_name.in_(names),
    )
    created_after = job.created_at
    if created_after is not None:
        if created_after.tzinfo is None:
            created_after = created_after.replace(tzinfo=timezone.utc)
        q = q.filter(Photo.created_at >= created_after)
    found: dict[str, Photo] = {}
    for row in q.order_by(Photo.created_at.asc()).all():
        if row.original_name and row.original_name not in found:
            found[str(row.original_name)] = row
    return found


def _names_already_on_one_lot(
    db: Session,
    job: ProcessingJob,
    names: list[str],
    *,
    same_sale: bool = False,
) -> bool:
    """True when every name is a stable upload name and all of them sit on a single lot in this sale."""
    if not names or len(_stable_names(names)) != len(names):
        return False
    needed = set(names)
    q = (
        db.query(Photo.lot_id, Photo.original_name)
        .join(Lot, Photo.lot_id == Lot.id)
        .filter(
            Lot.sale_id == job.sale_id,
            Lot.account_id == job.account_id,
            Photo.original_name.in_(names),
        )
    )
    if not same_sale:
        created_after = job.created_at
        if created_after is not None and created_after.tzinfo is None:
            created_after = created_after.replace(tzinfo=timezone.utc)
        if created_after is not None:
            q = q.filter(Lot.created_at >= created_after)
    by_lot: dict[str, set[str]] = {}
    for lot_id, original_name in q.all():
        if lot_id and original_name:
            by_lot.setdefault(str(lot_id), set()).add(str(original_name))
    return any(found >= needed for found in by_lot.values())


def job_photos_already_cataloged_on_sale(
    db: Session,
    job: ProcessingJob,
    photo_paths: list[Path],
) -> bool:
    """True when every input is already listed (any number of lots).

    An input counts when this job already attached it to a lot, or when its stable upload name
    (``mobile-…`` / ``append-…`` / ``web-…``) sits on a lot in this sale. Batch retries use this
    so completed lots are not Gemini'd again. Camera names only match this job's own rows.
    """
    db.refresh(job)
    if job.status == JobStatus.completed:
        return True
    paths = [p for p in photo_paths if p.name]
    if not paths:
        return False
    return len(_already_cataloged_paths(db, job, paths)) == len(set(paths))


def _batch_segment_already_cataloged(
    db: Session,
    job: ProcessingJob,
    photo_paths: list[Path],
) -> bool:
    """True when this tape-SKU segment already produced a lot (this job, or same stable names)."""
    paths = [p for p in photo_paths if p.name]
    if not paths:
        return False
    if len(_job_photos_on_lots(db, job, paths)) == len(set(paths)):
        return True
    return _names_already_on_one_lot(db, job, _photo_names(paths), same_sale=True)


def _single_lot_batch_already_cataloged(
    db: Session,
    job: ProcessingJob,
    photo_paths: list[Path],
    *,
    same_sale: bool = False,
) -> bool:
    """True when this Add-one-lot upload already produced catalog rows.

    Default (``same_sale=False``) only looks at lots created at/after this job — used mid-pipeline
    so we do not treat photos just inserted by *this* run as a prior listing.

    ``same_sale=True`` looks at the whole sale. Use that *before* Gemini so a Sync retry / Resend
    of the same ``mobile-<draft>-NN.jpg`` names does not pay for a second catalog pass.

    Name matching only applies to stable upload names; any job also matches its own rows.
    """
    if not bool(getattr(job, "single_lot_mode", False)) or not photo_paths:
        return False
    db.refresh(job)
    if job.status == JobStatus.completed:
        return True
    paths = [p for p in photo_paths if p.name]
    if not paths:
        return False
    if len(_job_photos_on_lots(db, job, paths)) == len(set(paths)):
        return True
    return _names_already_on_one_lot(db, job, _photo_names(paths), same_sale=same_sale)


def _note_isolated_job_persisted(job: ProcessingJob, *, lots: int) -> None:
    """Log the job-complete line and arm the child exit watchdog as soon as the row is saved."""
    if os.environ.get("WORKER_ISOLATED_CHILD") != "1":
        return
    from app.services.worker_child_lifecycle import note_job_persisted

    status = str(getattr(job.status, "value", job.status) or "unknown")
    note_job_persisted(
        job_id=str(job.id),
        account_id=str(job.account_id),
        status=status,
        lots=int(lots),
        photos=int(job.analyzed_photos or 0),
    )


def _finish_job_completed(db: Session, job: ProcessingJob) -> None:
    job.status = JobStatus.completed
    job.cancel_requested = False
    job.error_message = None
    db.commit()
    _note_isolated_job_persisted(job, lots=1 if getattr(job, "single_lot_mode", False) else 0)
    _emit_job(job, {"type": "status", "status": "completed"})


def _apply_listing_attrs_to_photo(
    row: Photo,
    *,
    photo_role: str | None,
    attrs: PhotoListingAttrs | None,
) -> None:
    listing = attrs or PhotoListingAttrs(photo_role=photo_role)
    role = listing.photo_role if listing.photo_role is not None else photo_role
    if role is not None:
        row.photo_role = normalize_catalog_photo_role(role)
    if listing.distinct_item_id is not None:
        row.distinct_item_id = listing.distinct_item_id
    if listing.is_primary_front is not None:
        row.is_primary_front = listing.is_primary_front
    if listing.shows_whole_lot is not None:
        row.shows_whole_lot = listing.shows_whole_lot
    if listing.shows_measuring_scale is not None:
        row.shows_measuring_scale = listing.shows_measuring_scale
    if listing.label_legibility is not None:
        row.label_legibility = listing.label_legibility


def _add_catalog_photo(
    db: Session,
    job: ProcessingJob,
    photo_path: Path,
    *,
    photo_role: str | None = None,
    attrs: PhotoListingAttrs | None = None,
) -> Photo:
    """Register a photo row pointing at a permanent copy under ``catalog/{account_id}/``.

    The row id is ``job_photo_id`` so a retry after a mid-persist crash reuses this job's own
    unlinked leftover instead of inserting a duplicate (or double-counting the daily photo cap).
    """
    wanted_id: str | None = job_photo_id(job, photo_path)
    existing = db.get(Photo, wanted_id)
    if existing is not None and existing.account_id == job.account_id and existing.lot_id is None:
        _apply_listing_attrs_to_photo(existing, photo_role=photo_role, attrs=attrs)
        db.flush()
        return existing
    if existing is not None:
        # Already on a lot (moved/split after an earlier run) — never re-home it; store a copy.
        wanted_id = None
    else:
        legacy = _unlinked_job_photos_by_name(db, job, [photo_path.name]).get(photo_path.name)
        if legacy is not None:
            _apply_listing_attrs_to_photo(legacy, photo_role=photo_role, attrs=attrs)
            db.flush()
            return legacy

    photo_id, storage_path = persist_catalog_photo(job.account_id, photo_path, photo_id=wanted_id)
    listing = attrs or PhotoListingAttrs(photo_role=photo_role)
    from app.services.photo_captured_at import captured_at_from_path

    row = Photo(
        id=photo_id,
        account_id=job.account_id,
        user_id=job.user_id,
        lot_id=None,
        original_name=photo_path.name,
        storage_path=storage_path,
        photo_role=normalize_catalog_photo_role(listing.photo_role if listing.photo_role is not None else photo_role),
        distinct_item_id=listing.distinct_item_id,
        is_primary_front=listing.is_primary_front,
        shows_whole_lot=listing.shows_whole_lot,
        shows_measuring_scale=listing.shows_measuring_scale,
        label_legibility=listing.label_legibility,
        captured_at=captured_at_from_path(photo_path),
    )
    db.add(row)
    db.flush()
    return row


EST_INPUT_TOKENS_PER_PHOTO = 1100
EST_OUTPUT_TOKENS_PER_SUCCESS = 220


def _maker_stamp_evidence_blob(lot: LotOutput, raw_payload: dict | None) -> str:
    """All free text we can use to detect Sheffield / underside stamp false sku (includes raw Gemini keys)."""
    parts: list[str] = [
        lot.auction_title or "",
        lot.professional_description or "",
        lot.condition or "",
    ]
    parts.extend(str(x) for x in (lot.specification_lines or []) if x)
    parts.extend(str(x) for x in (lot.brands or []) if x)
    if raw_payload:
        for key in (
            "AuctionTitle",
            "Title",
            "item_name",
            "ProfessionalDescription",
            "Description",
            "Condition",
            "condition_notes",
            "material",
        ):
            v = raw_payload.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(v)
        for key in ("SpecificationLines", "specification_lines", "LabelsAndTags", "labels_and_tags"):
            v = raw_payload.get(key)
            if isinstance(v, list):
                parts.extend(str(x) for x in v if isinstance(x, str) and x.strip())
            elif isinstance(v, str) and v.strip():
                parts.append(v)
    return " ".join(parts).lower()


_TAPE_PHRASES = (
    "masking tape",
    "painter tape",
    "tape roll",
    "estate tape",
    "sku tape",
    "lot tape",
    "purple tape",
    "lavender tape",
    "blue tape",
    "green tape",
    "yellow tape",
    "neon green",
    "neon yellow",
)


def _blob_suggests_purple_tape_language(blob: str) -> bool:
    """True when prose refers to colored *estate* lot tape (not substrings like ``plate`` in ``silverplate``)."""
    if re.search(r"\b(purple|lavender|blue)\b", blob):
        return True
    if re.search(r"\bneon\s+(?:green|yellow)\b", blob):
        return True
    return any(p in blob for p in _TAPE_PHRASES)


def _auction_title_sheffield_stamp_context(lot: LotOutput) -> bool:
    """Hero titles often include ``Sheffield`` + ``reproduction`` / ``base`` even when body blob omits a field."""
    t = (lot.auction_title or "").lower()
    if "sheffield" not in t:
        return False
    if "reproduction" in t or "hand chased" in t or "hand-chased" in t:
        return True
    if "silverplate" in t or "silver plate" in t:
        return True
    if re.search(r"\bbase\b", t):
        return True
    return False


def _strip_maker_stamp_misread_as_tape_sku(lot: LotOutput, raw_payload: dict | None = None) -> None:
    """
    Detail shots of silverplate often show stamped pattern numbers (e.g. 1420) on the base — not estate tape.

    When the model puts those digits in ``sku``, we would otherwise run tape verify and churn lot boundaries.

    Sheffield reproduction / hand-chased / silverplate base titles: clear **any** sku unless the row text
    uses real tape wording (purple/lavender/green/yellow estate tape phrases), not naive substring
    checks that misfire on ``silverplate``. Other cases: digits-only plus underside hints.
    """
    if not lot.sku:
        return
    blob = _maker_stamp_evidence_blob(lot, raw_payload)
    sheffield_style = "sheffield" in blob and (
        "reproduction" in blob or "hand chased" in blob or "hand-chased" in blob
    )
    sheffield_detail = sheffield_style or _auction_title_sheffield_stamp_context(lot)
    if sheffield_detail and not _blob_suggests_purple_tape_language(blob):
        lot.sku = None
        lot.sku_needs_review = False
        return
    compact = re.sub(r"\s+", "", str(lot.sku).strip())
    if re.fullmatch(r"\d{2,6}", compact) is None:
        return
    base_markings = any(
        k in blob
        for k in (
            "underside",
            "under side",
            "bottom of",
            "base stamp",
            "hallmark",
            "maker mark",
            "makers mark",
            "maker's mark",
            "stamped on",
        )
    )
    silver_context = any(
        k in blob for k in ("silver", "plate", "silverplate", "silver plate", "epns", "hollowware", "holloware")
    )
    if base_markings and silver_context:
        lot.sku = None
        lot.sku_needs_review = False


def _apply_single_lot_manual_sku(job: ProcessingJob, lot: LotOutput | None) -> None:
    """When the job was created with optional tape/SKU text, force that SKU on the merged lot."""
    if not lot or not bool(getattr(job, "single_lot_mode", False)):
        return
    raw = getattr(job, "single_lot_manual_sku", None)
    if not raw:
        return
    s = str(raw).strip()
    if not s:
        return
    # Manual entry wins as typed (JH603SK, MS6000RG). Tape helpers are fallback only.
    normalized = (
        normalize_manual_sku_input(s)
        or tape_marking_canonical(s)
        or normalize_tape_sku_input(s)
        or sku_digits(s)
        or None
    )
    if not normalized:
        return
    digits = sku_digits(normalized)
    if len(digits) < 2 or len(digits) > 12:
        return
    lot.sku = normalized
    lot.sku_needs_review = False


def _apply_inherit_sku_from_subfolder_name(
    job: ProcessingJob, lot: LotOutput | None, folder_batch_key: str | None
) -> None:
    """If the job opted in, set lot SKU from the top-level folder token (e.g. ``035``) and clear review flag."""
    if not lot or not bool(getattr(job, "inherit_sku_from_subfolder", False)):
        return
    sku = folder_token_as_inherited_sku(folder_batch_key)
    if sku:
        lot.sku = sku
        lot.sku_needs_review = False


def _subfolder_batch_key(job_folder: Path, photo_path: Path) -> str:
    """First path segment under the job folder; empty string = file lives directly in job root."""
    try:
        rel = photo_path.resolve().relative_to(job_folder.resolve())
    except ValueError:
        return ""
    parts = rel.parts
    return parts[0] if len(parts) > 1 else ""


def _finalize_open_lot(
    db: Session,
    account_id: str,
    user_id: str,
    current_lot: LotOutput | None,
    pending_photo_ids: list[str],
    sale_id: str,
    *,
    listing_channel: str = "ctseller",
    listing_order: list[str] | None = None,
) -> tuple[LotOutput | None, list[str], Lot | None]:
    """Persist current lot and attach pending photos; return cleared state and persisted row if any."""
    if current_lot is None:
        return None, pending_photo_ids, None
    persisted = _save_lot(
        db, account_id, user_id, current_lot, sale_id, listing_channel=listing_channel
    )
    db.flush()
    _assign_photos_to_lot(db, persisted.id, pending_photo_ids)
    # Shopper gallery: prefer the cheap Flash gallery plan when present, else chapter sort.
    names = [str(n).strip() for n in (listing_order or []) if str(n).strip()]
    if names:
        reorder_photos_by_filenames(db, persisted.id, account_id, names)
    else:
        reorder_photos_for_listing(db, persisted.id, account_id)
    pending_photo_ids.clear()
    return None, pending_photo_ids, persisted


def _assign_photos_to_lot(db: Session, lot_id: str, photo_ids: list[str]) -> None:
    if not photo_ids:
        return
    mx = db.query(func.max(Photo.sort_order)).filter(Photo.lot_id == lot_id).scalar()
    base = (int(mx) if mx is not None else -1) + 1
    for i, pid in enumerate(photo_ids):
        db.execute(
            update(Photo).where(Photo.id == pid).values(lot_id=lot_id, sort_order=base + i)
        )


def _cancel_signaled(
    db: Session, job_id: str, cancel_event: threading.Event | None
) -> bool:
    if cancel_event is not None and cancel_event.is_set():
        return True
    flagged = (
        db.query(ProcessingJob.cancel_requested).filter(ProcessingJob.id == job_id).scalar()
    )
    return bool(flagged)


def _lot_sse_payload(row: Lot) -> dict:
    """Fields aligned with dashboard ``Lot`` rows (live table updates)."""
    created = row.created_at
    return {
        "id": row.id,
        "sale_id": row.sale_id,
        "sku": row.sku,
        "auction_title": row.auction_title or "",
        "ct_category": row.ct_category,
        "ct_subcategory": row.ct_subcategory,
        "is_completed": bool(row.is_completed),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "starting_price": row.starting_price,
        "condition_text": row.condition_text or "",
        "photo_count": row.photo_count,
        "has_thumbnail": row.photo_count > 0,
        "created_at": created.isoformat() if created else "",
        "sku_needs_review": bool(row.sku_needs_review),
        "many_photos_review": False,
        "sku_duplicate_in_sale": bool(getattr(row, "sku_duplicate_in_sale", False)),
        "sku_text_mismatch": bool(getattr(row, "sku_text_mismatch", False)),
    }


def _emit_job(job: ProcessingJob, payload: dict) -> None:
    job_events.publish(job.account_id, job.id, {**payload, "job_id": job.id})


def _emit_job_debug(job: ProcessingJob, message: str) -> None:
    """Mirror debug lines to stderr and SSE so Railway log search is optional."""
    if not settings.job_debug_inventory:
        return
    _LOG.info("%s", message)
    _emit_job(job, {"type": "job_log", "message": message})


def _save_lot(
    db: Session,
    account_id: str,
    user_id: str,
    lot: LotOutput,
    sale_id: str,
    *,
    listing_channel: str = "ctseller",
) -> Lot:
    ct_category, ct_subcategory = resolve_lot_ct_category(lot)
    row = Lot(
        account_id=account_id,
        user_id=user_id,
        sale_id=sale_id,
        sku=lot.sku,
        auction_title=lot.auction_title,
        professional_description=lot.professional_description,
        specification_lines_json=json.dumps(lot.specification_lines, ensure_ascii=True),
        brands_json=json.dumps(lot.brands, ensure_ascii=True),
        labels_and_tags_json=json.dumps(lot.labels_and_tags, ensure_ascii=True),
        search_tags_json=json.dumps(getattr(lot, "search_tags", None) or [], ensure_ascii=True),
        ct_category=ct_category,
        ct_subcategory=ct_subcategory,
        is_completed=False,
        completed_at=None,
        starting_price=lot.starting_price,
        condition_text=canonicalize_ct_condition(lot.condition),
        photo_count=lot.photo_count,
        sku_needs_review=bool(lot.sku_needs_review),
        listing_channel=normalize_listing_channel(listing_channel),
    )
    db.add(row)
    db.flush()
    return row


def _segment_batch_lots_by_tape(
    folder: Path,
    ordered: list[tuple[Path, str, dict]],
    *,
    dividers: list[Path] | None = None,
) -> list[list[tuple[Path, str]]]:
    """
    Split a batch upload into lot segments using estate tape SKU, blank divider frames,
    and subfolder boundaries. Divider frames are left out of every segment (not cataloged);
    they are appended to ``dividers`` when given.
    """
    segments: list[list[tuple[Path, str]]] = []
    current: list[tuple[Path, str]] = []
    open_sku: str | None = None
    prev_batch_key: str | None = None
    divider_set = find_lot_dividers(ordered)

    for path, key, row in ordered:
        batch_key = _subfolder_batch_key(folder, path)
        if prev_batch_key is not None and batch_key != prev_batch_key and current:
            segments.append(current)
            current = []
            open_sku = None

        if path in divider_set:
            if current:
                segments.append(current)
                current = []
            open_sku = None
            prev_batch_key = batch_key
            if dividers is not None:
                dividers.append(path)
            continue

        marking = triage_tape_marking_from_row(row)
        sku_candidate = tape_marking_canonical(marking) if marking else None
        sku_ok = bool(sku_candidate and tape_sku_opens_new_lot(sku_candidate))

        if sku_ok and sku_candidate and not settings.skip_tape_verify:
            verified = verify_tape_sku(path, sku_candidate)
            if verified is False:
                sku_ok = False

        if (
            sku_ok
            and sku_candidate
            and open_sku is not None
            and not tape_sku_same_marking(open_sku, sku_candidate)
        ):
            if current:
                segments.append(current)
                current = []
            open_sku = sku_candidate
        elif sku_ok and sku_candidate and open_sku is None:
            open_sku = sku_candidate

        current.append((path, key))
        prev_batch_key = batch_key

    if current:
        segments.append(current)
    return segments


SPLIT_TRIAGE_SIDECAR = ".split_triage.json"


def cap_lot_segments(
    segments: list[list[tuple[Path, str]]], cap: int | None = CTBIDS_MAX_PHOTOS_PER_LOT
) -> list[tuple[list[tuple[Path, str]], int, int]]:
    """``(entries, part, parts)`` per lot; a segment over the CT Bids cap becomes consecutive parts.

    More than 40 frames between tape markers is almost always a missed boundary, and a lot over
    the cap cannot be posted at all — split in capture order and flag the parts for review.
    """
    out: list[tuple[list[tuple[Path, str]], int, int]] = []
    for seg in segments:
        if not cap or len(seg) <= cap:
            out.append((seg, 1, 1))
            continue
        parts = (len(seg) + cap - 1) // cap
        for i in range(parts):
            out.append((seg[i * cap : (i + 1) * cap], i + 1, parts))
    return out


def _link_or_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def _split_batch_into_lot_jobs(
    db: Session,
    job: ProcessingJob,
    folder: Path,
    lots: list[tuple[list[tuple[Path, str]], int, int]],
    triage_by_key: dict[str, dict],
) -> list[ProcessingJob]:
    """Queue one single-lot job per lot (own folder, triage carried over) and complete ``job``.

    Children are written to disk / R2 first and inserted together with the parent's completion
    in one commit, so a crash mid-split re-runs the batch instead of leaving half the lots queued.
    Each child runs in the account's bulk lane, in parallel with the other lots.
    """
    from app.job_batch_storage import mirror_local_file_to_r2

    token = uuid.uuid4().hex[:6]
    made_dirs: list[Path] = []
    children: list[ProcessingJob] = []
    base_time = datetime.now(timezone.utc)
    try:
        for idx, (entries, part, parts) in enumerate(lots, start=1):
            child_dir = (folder.parent / f"{folder.name}.lot{idx:03d}-{token}").resolve()
            child_dir.mkdir(parents=True, exist_ok=False)
            made_dirs.append(child_dir)
            rows: list[dict] = []
            for path, key in entries:
                try:
                    rel = path.resolve().relative_to(folder.resolve())
                except ValueError:
                    rel = Path(path.name)
                dest = child_dir / rel
                _link_or_copy(path, dest)
                side = Path(str(path) + ".captured_at")
                if side.is_file():
                    _link_or_copy(side, Path(str(dest) + ".captured_at"))
                row = triage_by_key.get(key)
                if row is not None:
                    rows.append({**row, "filename": dest.name})
            (child_dir / SPLIT_TRIAGE_SIDECAR).write_text(
                json.dumps(
                    {"parent_job_id": job.id, "part": part, "parts": parts, "rows": rows},
                    default=str,
                ),
                encoding="utf-8",
            )
            for f in child_dir.rglob("*"):
                if f.is_file():
                    mirror_local_file_to_r2(f, child_dir, job.account_id)
            children.append(
                ProcessingJob(
                    id=str(uuid.uuid4()),
                    account_id=job.account_id,
                    user_id=job.user_id,
                    sale_id=job.sale_id,
                    status=JobStatus.queued,
                    input_folder=str(child_dir),
                    inherit_sku_from_subfolder=bool(job.inherit_sku_from_subfolder),
                    single_lot_mode=True,
                    listing_channel=getattr(job, "listing_channel", "ctseller") or "ctseller",
                    parent_job_id=job.id,
                    # FIFO within the bulk lane keeps lots in capture order.
                    created_at=base_time + timedelta(microseconds=idx),
                    updated_at=base_time,
                )
            )
        for child in children:
            db.add(child)
        job.status = JobStatus.completed
        job.cancel_requested = False
        job.error_message = None
        job.progress_phase = "split"
        job.progress_done = len(children)
        job.progress_total = len(children)
        db.commit()
    except BaseException:
        db.rollback()
        for d in made_dirs:
            shutil.rmtree(d, ignore_errors=True)
        raise
    return children


def _load_split_triage(folder: Path | None) -> dict | None:
    """Triage rows + part info written by ``_split_batch_into_lot_jobs`` (None for normal jobs)."""
    if folder is None:
        return None
    path = folder / SPLIT_TRIAGE_SIDECAR
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _process_job_batch_pipeline(
    db: Session,
    job: ProcessingJob,
    photos: list[Path],
    folder: Path,
    *,
    cancel_event: threading.Event | None = None,
) -> None:
    """Choose photos / folder batch: triage once, split on tape SKU, pipeline each lot."""
    already = _already_cataloged_paths(db, job, photos)
    photos = [p for p in photos if p not in already]
    if not photos:
        _LOG.info(
            "batch pipeline skipped — all photos already cataloged on this sale job_id=%s",
            job.id,
        )
        _finish_job_completed(db, job)
        return

    total_photos = len(photos)
    _emit_job(job, {"type": "batch_start", "total_photos": total_photos, "phase": "pipeline"})

    if _cancel_signaled(db, job.id, cancel_event):
        job.status = JobStatus.failed
        job.error_message = "Cancelled"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return

    def _emit(payload: dict) -> None:
        _emit_job(job, payload)

    def _should_cancel() -> bool:
        return _cancel_signaled(db, job.id, cancel_event)

    try:
        ordered = triage_ordered_photo_batch(
            photos,
            emit=_emit,
            should_cancel=_should_cancel,
        )
    except PipelineCancelled:
        job.status = JobStatus.failed
        job.error_message = "Cancelled"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return

    divider_paths: list[Path] = []
    lots = cap_lot_segments(_segment_batch_lots_by_tape(folder, ordered, dividers=divider_paths))
    if divider_paths:
        _LOG.info(
            "batch divider frames job_id=%s dividers=%d lots=%d",
            job.id,
            len(divider_paths),
            len(lots),
        )
        _emit_job(job, {"type": "batch_dividers", "dividers": len(divider_paths), "lots": len(lots)})
    if not lots:
        job.status = JobStatus.failed
        job.error_message = "No photos to catalog after triage"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return
    segments = [entries for entries, _part, _parts in lots]
    parts_by_seg = {i: (part, parts) for i, (_e, part, parts) in enumerate(lots)}

    if settings.catalog_batch_fanout and len(lots) > 1:
        triage_rows = {key: row for _path, key, row in ordered}
        job.analyzed_photos = int(total_photos)
        job.failed_photos = 0
        job.est_input_tokens = int(total_photos * EST_INPUT_TOKENS_PER_PHOTO)
        job.est_output_tokens = 0
        children = _split_batch_into_lot_jobs(db, job, folder, lots, triage_rows)
        _note_isolated_job_persisted(job, lots=0)
        _LOG.info(
            "batch split into lot jobs job_id=%s photos=%d lots=%d",
            job.id,
            total_photos,
            len(children),
        )
        _emit_job(
            job,
            {"type": "batch_split", "lots": len(children), "job_ids": [c.id for c in children]},
        )
        _emit_job(job, {"type": "status", "status": "completed"})
        return

    job.analyzed_photos = int(total_photos)
    job.failed_photos = 0
    job.est_input_tokens = int(total_photos * EST_INPUT_TOKENS_PER_PHOTO)
    job.est_output_tokens = int(len(segments) * EST_OUTPUT_TOKENS_PER_SUCCESS)
    db.commit()
    _heartbeat_job(db, job)

    _emit_job(job, {"type": "batch_phase", "phase": "inventory", "total_photos": total_photos})

    triage_by_key = {key: row for _path, key, row in ordered}
    lots_saved = 0

    for seg_idx, segment in enumerate(segments):
        if _cancel_signaled(db, job.id, cancel_event):
            job.status = JobStatus.failed
            job.error_message = "Cancelled"
            job.cancel_requested = False
            db.commit()
            _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
            return

        seg_entries = list(segment)
        seg_paths = [p for p, _k in seg_entries]
        seg_triage = [triage_by_key[k] for _p, k in seg_entries if k in triage_by_key]
        if _batch_segment_already_cataloged(db, job, seg_paths):
            _LOG.info(
                "batch segment skipped — already cataloged job_id=%s seg=%s photos=%d",
                job.id,
                seg_idx,
                len(seg_paths),
            )
            lots_saved += 1
            continue

        try:
            lot, pipeline_result = run_single_lot_pipeline(
                seg_entries,
                manifest_tag=f"{job.id}-seg{seg_idx}",
                emit=_emit,
                should_cancel=_should_cancel,
                pre_triage_rows=seg_triage,
            )
        except PipelineCancelled:
            job.status = JobStatus.failed
            job.error_message = "Cancelled"
            job.cancel_requested = False
            db.commit()
            _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
            return

        batch_key = _subfolder_batch_key(folder, seg_paths[0])
        _apply_inherit_sku_from_subfolder_name(job, lot, batch_key)
        _strip_maker_stamp_misread_as_tape_sku(lot, None)
        if parts_by_seg.get(seg_idx, (1, 1))[1] > 1:
            lot.sku_needs_review = True

        # Persist photos in capture order first; _finalize_open_lot then groups by listing role.
        catalog_paths = list(seg_paths)
        attrs_by_path = listing_attrs_by_path(
            catalog_paths,
            list((pipeline_result or {}).get("triage") or seg_triage or []),
            entries=seg_entries,
        )
        pending_photo_ids: list[str] = []
        for i, photo_path in enumerate(catalog_paths):
            _emit_job(
                job,
                {
                    "type": "inventory_step",
                    "index": i,
                    "total": len(catalog_paths),
                    "file_name": photo_path.name,
                },
            )
            row = _add_catalog_photo(
                db, job, photo_path, attrs=attrs_by_path.get(photo_path)
            )
            pending_photo_ids.append(row.id)
            db.commit()

        lot.photo_count = len(catalog_paths)
        _, pending_photo_ids, finalized = _finalize_open_lot(
            db,
            job.account_id,
            job.user_id,
            lot,
            pending_photo_ids,
            job.sale_id,
            listing_channel=getattr(job, "listing_channel", "ctseller"),
            listing_order=list((pipeline_result or {}).get("listing_order") or []),
        )
        db.commit()
        if finalized is not None:
            lots_saved += 1
            _emit_job(job, {"type": "lot_ready", "lot": _lot_sse_payload(finalized)})
        del lot, pipeline_result
        trim_process_memory()

    recompute_sku_sanity_flags_for_sale(db, job.sale_id, job.account_id)
    job.status = JobStatus.completed
    job.cancel_requested = False
    db.commit()
    _note_isolated_job_persisted(job, lots=lots_saved)
    _LOG.info(
        "batch pipeline completed job_id=%s photos=%d lots=%d segments=%d",
        job.id,
        total_photos,
        lots_saved,
        len(segments),
    )
    _verify_job_catalog_files_on_disk(db, job)
    _emit_job(job, {"type": "status", "status": "completed"})


def _process_job_single_lot_pipeline(
    db: Session,
    job: ProcessingJob,
    photos: list[Path],
    *,
    folder: Path | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Single-lot upload using triage + capped listing/Pro instead of full Flash on every photo."""
    total_photos = len(photos)
    pending_photo_ids: list[str] = []
    split = _load_split_triage(folder) if getattr(job, "parent_job_id", None) else None
    pre_triage: list[dict] | None = None
    if split:
        rows = [r for r in (split.get("rows") or []) if isinstance(r, dict)]
        # Reuse the batch's triage only when it covers every frame (else triage again).
        if {str(r.get("filename") or "") for r in rows} >= {p.name for p in photos}:
            pre_triage = rows

    _emit_job(job, {"type": "batch_start", "total_photos": total_photos, "phase": "pipeline"})

    if _cancel_signaled(db, job.id, cancel_event):
        job.status = JobStatus.failed
        job.error_message = "Cancelled"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return

    def _emit(payload: dict) -> None:
        _emit_job(job, payload)

    def _should_cancel() -> bool:
        return _cancel_signaled(db, job.id, cancel_event)

    try:
        lot, pipeline_result = run_single_lot_pipeline(
            photos,
            manifest_tag=str(job.id),
            emit=_emit,
            should_cancel=_should_cancel,
            pre_triage_rows=pre_triage,
        )
    except PipelineCancelled:
        job.status = JobStatus.failed
        job.error_message = "Cancelled"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return

    token_est = dict((pipeline_result.get("token_estimates") or {}))
    job.analyzed_photos = int(total_photos)
    job.failed_photos = 0
    job.est_input_tokens = int(token_est.get("input") or total_photos * EST_INPUT_TOKENS_PER_PHOTO)
    job.est_output_tokens = int(token_est.get("output") or EST_OUTPUT_TOKENS_PER_SUCCESS)
    db.commit()
    _heartbeat_job(db, job)

    if _single_lot_batch_already_cataloged(db, job, photos, same_sale=True):
        _LOG.info(
            "pipeline inventory skipped — same photos already cataloged in this sale job_id=%s photos=%d",
            job.id,
            total_photos,
        )
        _finish_job_completed(db, job)
        return

    _emit_job(job, {"type": "batch_phase", "phase": "inventory", "total_photos": total_photos})

    _apply_single_lot_manual_sku(job, lot)
    if folder is not None and photos:
        _apply_inherit_sku_from_subfolder_name(job, lot, _subfolder_batch_key(folder, photos[0]))
    _strip_maker_stamp_misread_as_tape_sku(lot, None)
    if split and int(split.get("parts") or 1) > 1:
        lot.sku_needs_review = True

    # Persist photos in capture order first; _finalize_open_lot then groups for listing
    # (true group shots → per-item chapters → labels → measurements → tape).
    catalog_paths = list(photos)
    attrs_by_path = listing_attrs_by_path(
        catalog_paths, list((pipeline_result or {}).get("triage") or [])
    )
    for i, photo_path in enumerate(catalog_paths):
        if _cancel_signaled(db, job.id, cancel_event):
            job.status = JobStatus.failed
            job.error_message = "Cancelled"
            job.cancel_requested = False
            db.commit()
            _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
            return
        _emit_job(
            job,
            {
                "type": "inventory_step",
                "index": i,
                "total": total_photos,
                "file_name": photo_path.name,
            },
        )
        row = _add_catalog_photo(
            db, job, photo_path, attrs=attrs_by_path.get(photo_path)
        )
        pending_photo_ids.append(row.id)
        db.commit()
        if i == 0 or (i + 1) % 5 == 0 or i + 1 == total_photos:
            _heartbeat_job(db, job)

    if _single_lot_batch_already_cataloged(db, job, photos):
        _LOG.info(
            "pipeline finalize skipped — batch already cataloged job_id=%s photos=%d",
            job.id,
            total_photos,
        )
        _finish_job_completed(db, job)
        return

    lot.photo_count = len(catalog_paths)
    _, pending_photo_ids, finalized = _finalize_open_lot(
        db,
        job.account_id,
        job.user_id,
        lot,
        pending_photo_ids,
        job.sale_id,
        listing_channel=getattr(job, "listing_channel", "ctseller"),
        listing_order=list((pipeline_result or {}).get("listing_order") or []),
    )
    db.commit()
    if finalized is not None:
        _emit_job(job, {"type": "lot_ready", "lot": _lot_sse_payload(finalized)})

    recompute_sku_sanity_flags_for_sale(db, job.sale_id, job.account_id)
    job.status = JobStatus.completed
    job.cancel_requested = False
    db.commit()
    lots_n = 1 if finalized is not None else 0
    _note_isolated_job_persisted(job, lots=lots_n)
    _LOG.info(
        "single-lot pipeline completed job_id=%s photos=%d lots=%d",
        job.id,
        total_photos,
        lots_n,
    )
    _verify_job_catalog_files_on_disk(db, job)
    _emit_job(job, {"type": "status", "status": "completed"})


def process_job(
    db: Session,
    job: ProcessingJob,
    *,
    cancel_event: threading.Event | None = None,
) -> None:
    with _process_job_execution_lock(db, job.id) as acquired:
        if not acquired:
            return
        _process_job_body(db, job, cancel_event=cancel_event)


def _process_job_body(
    db: Session,
    job: ProcessingJob,
    *,
    cancel_event: threading.Event | None = None,
) -> None:
    db.refresh(job)
    if job.status == JobStatus.completed:
        _LOG.info("process_job skipped — already completed job_id=%s", job.id)
        return

    from app.core.gemini_retry import parse_catalog_retry_attempt

    retry_prior = parse_catalog_retry_attempt(job.error_message)
    job.status = JobStatus.processing
    # Crash markers must survive this start so a later child OOM can increment the cap.
    job.error_message = processing_error_message_preserving_crash(job.error_message)
    job.cancel_requested = False
    job.analyzed_photos = 0
    job.failed_photos = 0
    job.est_input_tokens = 0
    job.est_output_tokens = 0
    db.commit()
    _emit_job(job, {"type": "status", "status": "processing"})

    if not google_api_key():
        job.status = JobStatus.failed
        job.error_message = "GOOGLE_API_KEY is not set in backend/.env"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return

    from app.job_batch_storage import confined_job_folder, ensure_job_input_folder, mirror_folder_to_r2

    folder = Path(job.input_folder)
    confined = confined_job_folder(folder, job.account_id)
    if confined is None:
        job.status = JobStatus.failed
        job.error_message = "Job folder is outside this account's upload directory"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return
    folder = confined
    try:
        # Last-chance mirror if files are local but never made it to R2 (e.g. older deploys).
        if folder.is_dir():
            mirror_folder_to_r2(folder, account_id=job.account_id)
        folder = ensure_job_input_folder(folder, account_id=job.account_id)
    except Exception:
        _LOG.exception("job-batch R2 ensure failed job_id=%s", job.id)

    if not folder.exists() or not folder.is_dir():
        job.status = JobStatus.failed
        job.error_message = (
            f"Input folder not found: {folder} "
            "(upload disk was lost — re-sync from the phone, or attach a Railway volume / keep R2 job staging)"
        )
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return

    raw_photos = sorted(
        [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
        key=lambda p: _job_photo_sort_key(folder, p),
    )
    if not raw_photos:
        try:
            from app.job_batch_storage import ensure_job_input_folder

            folder = ensure_job_input_folder(folder, account_id=job.account_id)
            raw_photos = sorted(
                [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
                key=lambda p: _job_photo_sort_key(folder, p),
            )
        except Exception:
            _LOG.exception("job-batch R2 restore retry failed job_id=%s", job.id)
    # Folder jobs may point at originals (server path) or pre-compressed uploads; normalize only when
    # EXIF orientation / HEIC still require it so catalog pixels are upright without re-saving every JPEG.
    seen_paths: set[str] = set()
    photos: list[Path] = []
    for p in raw_photos:
        np = normalize_job_folder_image(folder, p)
        key = str(np.resolve())
        if key not in seen_paths:
            seen_paths.add(key)
            photos.append(np)
    photos = sorted(
        [p for p in photos if not looks_like_video_file(p)],
        key=lambda p: _job_photo_sort_key(folder, p),
    )
    if not photos:
        job.status = JobStatus.failed
        job.error_message = "No supported images found in that folder (JPEG, PNG, WebP, HEIC)"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return

    if job_photos_already_cataloged_on_sale(db, job, photos):
        _LOG.info(
            "process_job skipped — same photos already cataloged in this sale job_id=%s photos=%d",
            job.id,
            len(photos),
        )
        _finish_job_completed(db, job)
        return

    _heartbeat_job(db, job)

    if _cancel_signaled(db, job.id, cancel_event):
        job.status = JobStatus.failed
        job.error_message = "Cancelled"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return

    from app.core.gemini_retry import GeminiRetryableError
    from pipeline.pipeline import reset_usage_counters, usage_snapshot

    single_lot_mode = bool(getattr(job, "single_lot_mode", False))
    reset_usage_counters()
    try:
        if single_lot_mode:
            _process_job_single_lot_pipeline(db, job, photos, folder=folder, cancel_event=cancel_event)
        else:
            _process_job_batch_pipeline(db, job, photos, folder, cancel_event=cancel_event)
    except GeminiRetryableError as exc:
        _handle_transient_job_failure(db, job, exc, prior_attempts=retry_prior)
        return

    # Persist the real Gemini token usage metered during this job (feeds accurate admin cost analytics).
    try:
        snap = usage_snapshot()
        job.actual_flash_input_tokens = int(snap.get("flash_input", 0))
        job.actual_flash_output_tokens = int(snap.get("flash_output", 0))
        job.actual_pro_input_tokens = int(snap.get("pro_input", 0))
        job.actual_pro_output_tokens = int(snap.get("pro_output", 0))
        db.commit()
    except Exception:  # noqa: BLE001
        _LOG.exception("persist actual token usage failed job_id=%s", job.id)
    return

LOT_AI_REGENERATE_MAX_PHOTOS = 80


def _verify_job_catalog_files_on_disk(db: Session, job: ProcessingJob) -> None:
    """Log when this job's new catalog rows point at files that are not readable.

    Must not call ``resolve_catalog_storage_path`` — that downloads every missing
    object from R2. On a real franchise catalog that hung the isolated child for
    minutes after the lot was already saved (job db5b42d2, 2026-11-04).
    """
    created_after = job.created_at
    q = db.query(Photo.id, Photo.storage_path).filter(Photo.account_id == job.account_id)
    if created_after is not None:
        q = q.filter(Photo.created_at >= created_after)
    rows = q.all()
    _LOG.info(
        "catalog file check start job_id=%s photos=%s",
        job.id,
        len(rows),
    )
    missing = [pid for pid, path in rows if not catalog_photo_file_on_disk(path)]
    if missing:
        _LOG.warning(
            "job=%s completed but %s/%s catalog photo file(s) missing "
            "(check upload volume, cwd, or multiple API replicas without shared storage)",
            job.id,
            len(missing),
            len(rows),
        )
    _LOG.info("catalog file check end job_id=%s missing=%s", job.id, len(missing))


def _sync_lot_photo_count(db: Session, lot_id: str, account_id: str) -> None:
    r = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
    if not r:
        return
    n = int(
        db.query(func.count())
        .select_from(Photo)
        .where(Photo.lot_id == lot_id, Photo.account_id == account_id)
        .scalar()
        or 0
    )
    r.photo_count = n


def _safe_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(x).strip() for x in parsed if str(x).strip()]


def _regenerate_fallback_lot_output(row: Lot) -> LotOutput:
    """Keep existing listing content when AI returns nothing usable."""
    return LotOutput(
        sku=tape_marking_canonical(row.sku),
        auction_title=(row.auction_title or "").strip(),
        professional_description=(row.professional_description or "").strip(),
        specification_lines=_safe_json_list(row.specification_lines_json),
        brands=_safe_json_list(row.brands_json),
        labels_and_tags=_safe_json_list(row.labels_and_tags_json),
        search_tags=_safe_json_list(getattr(row, "search_tags_json", None)),
        condition=(row.condition_text or "").strip(),
        starting_price=row.starting_price,
        sku_needs_review=True,
        lot_boundary_blank_page=False,
    )


def run_lot_ai_regenerate_and_save(
    db: Session,
    *,
    account_id: str,
    user_id: str,
    lot_id: str,
    photo_ids: list[str] | None,
    preserve_sku: bool,
) -> Lot:
    """
    Re-run Gemini on existing catalog files for one lot (or a subset), merge frames like single-lot mode,
    then overwrite listing fields on the existing ``Lot`` row. Does not create or delete ``Photo`` rows.
    """
    if not google_api_key():
        raise ValueError("GOOGLE_API_KEY is not configured on the server.")

    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
    if not row:
        raise ValueError("Lot not found")

    q = db.query(Photo).filter(Photo.lot_id == lot_id, Photo.account_id == account_id)
    if photo_ids:
        ids = list(dict.fromkeys(photo_ids))
        if len(ids) > LOT_AI_REGENERATE_MAX_PHOTOS:
            raise ValueError(f"At most {LOT_AI_REGENERATE_MAX_PHOTOS} photos per AI regenerate request.")
        photos = q.filter(Photo.id.in_(ids)).all()
        found = {p.id for p in photos}
        missing = [i for i in ids if i not in found]
        if missing:
            raise ValueError("One or more photo ids are not on this lot.")
        order = {pid: j for j, pid in enumerate(ids)}
        photos.sort(key=lambda p: (order.get(p.id, 999), p.created_at.timestamp()))
    else:
        photos = q.order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc()).all()

    if len(photos) > LOT_AI_REGENERATE_MAX_PHOTOS:
        raise ValueError(f"At most {LOT_AI_REGENERATE_MAX_PHOTOS} photos per AI regenerate request.")
    if not photos:
        raise ValueError("This lot has no catalog photos to analyze.")

    paths: list[Path] = []
    photo_rows: list[Photo] = []
    for ph in photos:
        pth = resolve_catalog_storage_path(ph.storage_path)
        if pth.is_file():
            paths.append(pth)
            photo_rows.append(ph)
    if not paths:
        raise ValueError("Image files are missing on disk for this lot.")

    manifest_tag = f"regen-{lot_id}"

    labeled_paths = [
        (pth, (ph.original_name or pth.name).strip() or pth.name)
        for ph, pth in zip(photo_rows, paths)
    ]
    current, _pipeline_result = run_single_lot_pipeline(
        labeled_paths,
        manifest_tag=manifest_tag,
    )
    if is_weak_pipeline_lot(current):
        current = _regenerate_fallback_lot_output(row)
        current.sku_needs_review = True

    if preserve_sku:
        current.sku = tape_marking_canonical(row.sku) or row.sku

    linked = int(
        db.query(func.count())
        .select_from(Photo)
        .where(Photo.lot_id == lot_id, Photo.account_id == account_id)
        .scalar()
        or 0
    )
    current.photo_count = linked

    ct_cat, ct_sub = resolve_lot_ct_category(current)
    row.auction_title = current.auction_title
    row.professional_description = current.professional_description
    row.specification_lines_json = json.dumps(current.specification_lines, ensure_ascii=True)
    row.brands_json = json.dumps(current.brands, ensure_ascii=True)
    row.labels_and_tags_json = json.dumps(current.labels_and_tags, ensure_ascii=True)
    row.search_tags_json = json.dumps(getattr(current, "search_tags", None) or [], ensure_ascii=True)
    row.ct_category = ct_cat
    row.ct_subcategory = ct_sub
    row.starting_price = current.starting_price
    row.condition_text = current.condition
    row.sku_needs_review = bool(current.sku_needs_review)
    if not preserve_sku:
        row.sku = current.sku
    _ = user_id  # future: audit log
    _sync_lot_photo_count(db, lot_id, account_id)
    db.commit()
    db.refresh(row)
    recompute_sku_sanity_flags_for_sale(db, row.sale_id, account_id)
    db.commit()
    db.refresh(row)
    return row

