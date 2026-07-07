from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, text, update
from sqlalchemy.orm import Session

from app.config import settings
from app.core.gemini import analyze_path_with_retries, analyze_photos_parallel_async, verify_tape_sku
from app.core.lotting import (
    LotOutput,
    folder_token_as_inherited_sku,
    normalize_tape_sku_input,
    sku_digits,
    tape_marking_canonical,
    tape_sku_opens_new_lot,
    tape_sku_same_marking,
    strip_product_box_misread_as_tape_sku,
)
from app.models import JobStatus, Lot, Photo, ProcessingJob
from app.services.category_inference import infer_ct_category
from app.services.pipeline_integration import (
    is_weak_pipeline_lot,
    run_single_lot_pipeline,
    triage_ordered_photo_batch,
    triage_tape_marking_from_row,
)
from pipeline.pipeline import PipelineCancelled
from app.services.sku_sanity import recompute_sku_sanity_flags_for_sale
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
    return zlib.crc32(job_id.encode("utf-8")) & 0x7FFFFFFF


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


def _heartbeat_job(db: Session, job: ProcessingJob) -> None:
    """Touch ``updated_at`` so multi-instance stale reconcile does not false-fail long runs."""
    job.status = JobStatus.processing
    job.updated_at = datetime.now(timezone.utc)
    db.commit()


def _single_lot_batch_already_cataloged(db: Session, job: ProcessingJob, photo_paths: list[Path]) -> bool:
    """True when this Add-one-lot upload already produced catalog rows (re-run / duplicate worker)."""
    if not bool(getattr(job, "single_lot_mode", False)) or not photo_paths:
        return False
    db.refresh(job)
    if job.status == JobStatus.completed:
        return True
    names = [p.name for p in photo_paths if p.name]
    if not names:
        return False
    needed = set(names)
    created_after = job.created_at
    if created_after is not None and created_after.tzinfo is None:
        created_after = created_after.replace(tzinfo=timezone.utc)
    q = (
        db.query(Photo.lot_id, Photo.original_name)
        .join(Lot, Photo.lot_id == Lot.id)
        .filter(
            Lot.sale_id == job.sale_id,
            Lot.account_id == job.account_id,
            Photo.original_name.in_(names),
        )
    )
    if created_after is not None:
        q = q.filter(Lot.created_at >= created_after)
    by_lot: dict[str, set[str]] = {}
    for lot_id, original_name in q.all():
        if lot_id and original_name:
            by_lot.setdefault(str(lot_id), set()).add(str(original_name))
    return any(found >= needed for found in by_lot.values())


def _finish_job_completed(db: Session, job: ProcessingJob) -> None:
    job.status = JobStatus.completed
    job.cancel_requested = False
    job.error_message = None
    db.commit()
    _emit_job(job, {"type": "status", "status": "completed"})


def _add_catalog_photo(db: Session, job: ProcessingJob, photo_path: Path) -> Photo:
    """Register a photo row pointing at a permanent copy under ``catalog/{account_id}/``."""
    photo_id, storage_path = persist_catalog_photo(job.account_id, photo_path)
    row = Photo(
        id=photo_id,
        account_id=job.account_id,
        user_id=job.user_id,
        lot_id=None,
        original_name=photo_path.name,
        storage_path=storage_path,
    )
    db.add(row)
    db.flush()
    return row


# Set by ``_apply_last_known_sku_fallback`` when SKU was copied from a prior frame (not read on this image).
# Must not open lots or run tape verify — otherwise stale digits + new lot SKU causes spurious verify stalls.
_CT_SKU_INJECTED_FALLBACK_KEY = "_ct_sku_injected_fallback"
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


_TITLE_WORD_RE = re.compile(r"[a-z0-9]+", re.I)
_TITLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "the",
    "with",
    "for",
    "of",
    "set",
    "lot",
    "item",
    "estate",
    "vintage",
}


def _title_tokens(title: str) -> set[str]:
    raw = {t.lower() for t in _TITLE_WORD_RE.findall(title or "")}
    return {t for t in raw if len(t) > 2 and t not in _TITLE_STOPWORDS}


# Used only for merge-before-new-tape when the open lot has no SKU yet (detail before hero with tape).
_SHARED_MATERIAL_TOKENS = frozenset(
    {
        "ceramic",
        "porcelain",
        "pottery",
        "earthenware",
        "stoneware",
        "glass",
        "crystal",
        "wood",
        "metal",
        "brass",
        "bronze",
        "pewter",
        "silver",
        "chrome",
        "plastic",
        "resin",
        "stone",
        "marble",
        "iron",
        "steel",
        "copper",
        "aluminum",
        "bamboo",
        "wicker",
        "rattan",
    }
)
_SHARED_FORM_TOKENS = frozenset(
    {
        "pitcher",
        "ewer",
        "jug",
        "teapot",
        "creamer",
        "carafe",
        "decanter",
        "vase",
        "bowl",
        "platter",
        "plate",
        "tray",
        "tureen",
        "pot",
        "mug",
        "cup",
        "saucer",
        "figurine",
        "statue",
        "clock",
        "lamp",
        "candlestick",
        "candelabra",
        "chair",
        "table",
        "desk",
        "dresser",
        "mirror",
        "frame",
        "bookcase",
        "cabinet",
    }
)


def _lot_text_blob_for_overlap(lot: LotOutput) -> str:
    """Macro + detail frames often disagree on ``auction_title``; include prose/spec hints."""
    specs = " ".join(lot.specification_lines[:10])
    desc = (lot.professional_description or "")[:1200]
    return " ".join([lot.auction_title or "", desc, specs])


def _shared_material_and_form_tokens(ta: set[str], tb: set[str]) -> bool:
    """True when both lots mention the same material and the same object class (e.g. ceramic + pitcher)."""
    shared = ta & tb
    return bool((shared & _SHARED_MATERIAL_TOKENS) and (shared & _SHARED_FORM_TOKENS))


def _strip_product_box_misread_as_tape_sku(lot: LotOutput, raw_payload: dict | None = None) -> None:
    strip_product_box_misread_as_tape_sku(lot, raw_payload)


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


def _likely_same_item(current_lot: LotOutput, candidate: LotOutput) -> bool:
    """
    Heuristic continuity check to avoid accidental lot splits in bulk uploads.
    Returns true when title/brand overlap strongly suggests same physical item.

    When the open lot has **no tape SKU yet** (detail shots before the hero frame with colored estate tape),
    compare tokens from title + description + specs so a handle macro still matches the SA015
    wide shot (e.g. both ``ceramic`` + ``pitcher``).
    """
    if current_lot.sku is None:
        current_tokens = _title_tokens(_lot_text_blob_for_overlap(current_lot))
        candidate_tokens = _title_tokens(_lot_text_blob_for_overlap(candidate))
    else:
        current_tokens = _title_tokens(current_lot.auction_title)
        candidate_tokens = _title_tokens(candidate.auction_title)
    token_overlap = bool(current_tokens and candidate_tokens and (current_tokens & candidate_tokens))
    current_brands = {b.lower() for b in current_lot.brands if b}
    candidate_brands = {b.lower() for b in candidate.brands if b}
    brand_overlap = bool(current_brands and candidate_brands and (current_brands & candidate_brands))
    if token_overlap or brand_overlap:
        return True
    if current_lot.sku is None and _shared_material_and_form_tokens(current_tokens, candidate_tokens):
        return True
    return False


def _is_gemini_placeholder_lot(lot: LotOutput) -> bool:
    """
    True when ``current_lot`` was created only to hold catalog files whose Gemini calls returned null.

    The first successful analysis row replaces this shell so those files are not stranded on a
    blank listing or dropped when a tape frame fails vision.
    """
    if (lot.sku or "").strip():
        return False
    if (lot.auction_title or "").strip():
        return False
    if (lot.professional_description or "").strip():
        return False
    if lot.brands or lot.specification_lines or lot.labels_and_tags:
        return False
    return True


def _single_lot_maybe_adopt_tape_sku(
    job: ProcessingJob,
    current_lot: LotOutput,
    lot_candidate: LotOutput,
    photo_path: Path,
    *,
    index: int,
    total_photos: int,
) -> None:
    """
    ``merge_detail`` does not merge SKU. For single-lot jobs, adopt a tape marking from any frame
    that passes the same tape gate (and optional second-pass verify) as batch uploads.
    """
    if not bool(getattr(job, "single_lot_mode", False)):
        return
    cand_sku = lot_candidate.sku
    if not cand_sku or not tape_sku_opens_new_lot(cand_sku):
        return
    claimed = (tape_marking_canonical(cand_sku) or sku_digits(cand_sku) or "").strip()
    if not claimed:
        return
    if not settings.skip_tape_verify:
        _emit_inventory_tick(job, index=index, total=total_photos, photo_path=photo_path, stage="tape_verify")
        t0 = time.monotonic()
        verified = verify_tape_sku(photo_path, claimed)
        _emit_job_debug(
            job,
            "job=%s single_lot_tape_verify i=%s file=%s claimed=%s result=%s ms=%.0f"
            % (
                job.id,
                index + 1,
                photo_path.name,
                claimed,
                verified,
                (time.monotonic() - t0) * 1000.0,
            ),
        )
        if verified is not True:
            return
    canonical = tape_marking_canonical(cand_sku) or cand_sku
    if current_lot.sku is None:
        current_lot.sku = canonical
        if settings.skip_tape_verify:
            current_lot.sku_needs_review = bool(lot_candidate.sku_needs_review)
        else:
            current_lot.sku_needs_review = False
        return
    if tape_sku_same_marking(current_lot.sku, canonical):
        current_lot.sku = canonical
        return
    current_lot.sku_needs_review = True


def _apply_single_lot_manual_sku(job: ProcessingJob, lot: LotOutput | None) -> None:
    """When the job was created with optional tape/SKU digits, force that SKU on the merged lot."""
    if not lot or not bool(getattr(job, "single_lot_mode", False)):
        return
    raw = getattr(job, "single_lot_manual_sku", None)
    if not raw:
        return
    s = str(raw).strip()
    if not s:
        return
    normalized = normalize_tape_sku_input(s) or sku_digits(s) or None
    if not normalized:
        return
    digits = sku_digits(normalized)
    if len(digits) < 2 or len(digits) > 12:
        return
    lot.sku = digits
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


def _apply_last_known_sku_fallback(payloads: list[dict | None]) -> None:
    """
    Walk photos in file order: when this frame has no sku digits at all, reuse the last
    tape sku from an earlier frame in the same job.

    Never overwrites sku digits already returned for a frame — clothing lots often have a correct
    tape read (e.g. 034) marked low/unsure on a wide shot; an earlier misread (e.g. 006) must not replace it.

    Injected rows are tagged with ``_CT_SKU_INJECTED_FALLBACK_KEY`` so the inventory pass does not
    treat them as a fresh tape read (avoids spurious tape-verify API calls and lot splits).

    ``last`` still advances on low/unsure when the explicit digits change, so a new lot tape (e.g.
    SA013) is not followed by empty frames that keep reusing the previous lot's digits (e.g. 001).
    """
    if not settings.last_known_sku_fallback:
        return
    last: str | None = None
    for p in payloads:
        if not p or not isinstance(p, dict):
            continue
        conf = str(p.get("SkuConfidence") or p.get("sku_read_confidence") or "").strip().lower()
        raw = p.get("SKU")
        sku_clean = sku_digits(raw) if raw not in (None, "") else None
        if not sku_clean and last:
            p["SKU"] = last
            p[_CT_SKU_INJECTED_FALLBACK_KEY] = True
        try:
            _tmp_lo = LotOutput.from_model(p)
        except (TypeError, ValueError, KeyError):
            _tmp_lo = None
        if _tmp_lo is not None:
            _strip_maker_stamp_misread_as_tape_sku(_tmp_lo, p)
            if _tmp_lo.sku is None:
                p.pop("SKU", None)
                p.pop(_CT_SKU_INJECTED_FALLBACK_KEY, None)
        sku_after = sku_digits(p.get("SKU")) if p.get("SKU") not in (None, "") else None
        if sku_after:
            # Always advance on high/medium confidence.
            if conf not in ("low", "unsure"):
                last = sku_after
            # Low/unsure but digits changed: still advance ``last`` so the chain does not keep an
            # older lot's digits after the model clearly moved to a new tape (e.g. SA001 → SA013).
            elif last is None or sku_after != last:
                last = sku_after


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
) -> tuple[LotOutput | None, list[str], Lot | None]:
    """Persist current lot and attach pending photos; return cleared state and persisted row if any."""
    if current_lot is None:
        return None, pending_photo_ids, None
    persisted = _save_lot(db, account_id, user_id, current_lot, sale_id)
    db.flush()
    _assign_photos_to_lot(db, persisted.id, pending_photo_ids)
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


def _emit_inventory_tick(
    job: ProcessingJob, *, index: int, total: int, photo_path: Path, stage: str
) -> None:
    """
    Small ``data:`` SSE between slow steps so HTTP proxies (e.g. Railway edge) do not close the
    EventSource with 502 while ``merge_detail`` / ``tape_verify`` / DB work runs without other events.
    """
    _emit_job(
        job,
        {
            "type": "inventory_tick",
            "index": index,
            "total": total,
            "file_name": photo_path.name,
            "stage": stage,
        },
    )


def _save_lot(db: Session, account_id: str, user_id: str, lot: LotOutput, sale_id: str) -> Lot:
    ct_category, ct_subcategory = infer_ct_category(lot)
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
        ct_category=ct_category,
        ct_subcategory=ct_subcategory,
        is_completed=False,
        completed_at=None,
        starting_price=lot.starting_price,
        condition_text=lot.condition,
        photo_count=lot.photo_count,
        sku_needs_review=bool(lot.sku_needs_review),
    )
    db.add(row)
    db.flush()
    return row


def _segment_batch_lots_by_tape(
    folder: Path,
    ordered: list[tuple[Path, str, dict]],
) -> list[list[tuple[Path, str]]]:
    """
    Split a batch upload into lot segments using estate tape SKU (and subfolder boundaries).

    Matches the legacy inventory loop: a new colored tape marking starts the next catalog lot.
    """
    segments: list[list[tuple[Path, str]]] = []
    current: list[tuple[Path, str]] = []
    open_sku: str | None = None
    prev_batch_key: str | None = None

    for path, key, row in ordered:
        batch_key = _subfolder_batch_key(folder, path)
        if prev_batch_key is not None and batch_key != prev_batch_key and current:
            segments.append(current)
            current = []
            open_sku = None

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


def _process_job_batch_pipeline(
    db: Session,
    job: ProcessingJob,
    photos: list[Path],
    folder: Path,
    *,
    cancel_event: threading.Event | None = None,
) -> None:
    """Choose photos / folder batch: triage once, split on tape SKU, pipeline each lot."""
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

    segments = _segment_batch_lots_by_tape(folder, ordered)
    if not segments:
        job.status = JobStatus.failed
        job.error_message = "No photos to catalog after triage"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
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

        try:
            lot, _pipeline_result = run_single_lot_pipeline(
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

        pending_photo_ids: list[str] = []
        for i, photo_path in enumerate(seg_paths):
            _emit_job(
                job,
                {
                    "type": "inventory_step",
                    "index": i,
                    "total": len(seg_paths),
                    "file_name": photo_path.name,
                },
            )
            row = _add_catalog_photo(db, job, photo_path)
            pending_photo_ids.append(row.id)
            db.commit()

        lot.photo_count = len(seg_paths)
        _, pending_photo_ids, finalized = _finalize_open_lot(
            db, job.account_id, job.user_id, lot, pending_photo_ids, job.sale_id
        )
        db.commit()
        if finalized is not None:
            lots_saved += 1
            _emit_job(job, {"type": "lot_ready", "lot": _lot_sse_payload(finalized)})

    recompute_sku_sanity_flags_for_sale(db, job.sale_id, job.account_id)
    job.status = JobStatus.completed
    job.cancel_requested = False
    db.commit()
    _verify_job_catalog_files_on_disk(db, job)
    _emit_job(job, {"type": "status", "status": "completed"})
    _LOG.info(
        "batch pipeline completed job_id=%s photos=%d lots=%d segments=%d",
        job.id,
        total_photos,
        lots_saved,
        len(segments),
    )


def _process_job_single_lot_pipeline(
    db: Session,
    job: ProcessingJob,
    photos: list[Path],
    *,
    cancel_event: threading.Event | None = None,
) -> None:
    """Single-lot upload using triage + capped listing/Pro instead of full Flash on every photo."""
    total_photos = len(photos)
    pending_photo_ids: list[str] = []

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

    if _single_lot_batch_already_cataloged(db, job, photos):
        _LOG.info(
            "pipeline inventory skipped — batch already cataloged job_id=%s photos=%d",
            job.id,
            total_photos,
        )
        _finish_job_completed(db, job)
        return

    _emit_job(job, {"type": "batch_phase", "phase": "inventory", "total_photos": total_photos})

    _apply_single_lot_manual_sku(job, lot)
    _strip_maker_stamp_misread_as_tape_sku(lot, None)

    for i, photo_path in enumerate(photos):
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
        row = _add_catalog_photo(db, job, photo_path)
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

    lot.photo_count = len(photos)
    _, pending_photo_ids, finalized = _finalize_open_lot(
        db, job.account_id, job.user_id, lot, pending_photo_ids, job.sale_id
    )
    db.commit()
    if finalized is not None:
        _emit_job(job, {"type": "lot_ready", "lot": _lot_sse_payload(finalized)})

    recompute_sku_sanity_flags_for_sale(db, job.sale_id, job.account_id)
    job.status = JobStatus.completed
    job.cancel_requested = False
    db.commit()
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

    job.status = JobStatus.processing
    job.error_message = None
    job.cancel_requested = False
    job.analyzed_photos = 0
    job.failed_photos = 0
    job.est_input_tokens = 0
    job.est_output_tokens = 0
    db.commit()
    _emit_job(job, {"type": "status", "status": "processing"})

    if not (settings.google_api_key or "").strip():
        job.status = JobStatus.failed
        job.error_message = "GOOGLE_API_KEY is not set in backend/.env"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return

    folder = Path(job.input_folder)
    if not folder.exists() or not folder.is_dir():
        job.status = JobStatus.failed
        job.error_message = f"Input folder not found: {folder}"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return

    raw_photos = sorted(
        [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
        key=lambda p: str(p).lower(),
    )
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
    photos = sorted(photos, key=lambda p: str(p).lower())
    if not photos:
        job.status = JobStatus.failed
        job.error_message = "No supported images found in that folder (JPEG, PNG, WebP, HEIC)"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return

    if _single_lot_batch_already_cataloged(db, job, photos):
        _LOG.info(
            "process_job skipped — Add-one-lot batch already cataloged job_id=%s photos=%d",
            job.id,
            len(photos),
        )
        _finish_job_completed(db, job)
        return

    _heartbeat_job(db, job)
    current_lot: LotOutput | None = None
    pending_photo_ids: list[str] = []
    current_lot_folder_batch_key: str | None = None

    if _cancel_signaled(db, job.id, cancel_event):
        job.status = JobStatus.failed
        job.error_message = "Cancelled"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return

    single_lot_mode = bool(getattr(job, "single_lot_mode", False))
    if settings.pipeline_triage_brand_audit:
        if single_lot_mode:
            _process_job_single_lot_pipeline(db, job, photos, cancel_event=cancel_event)
        else:
            _process_job_batch_pipeline(db, job, photos, folder, cancel_event=cancel_event)
        return

    total_photos = len(photos)
    _emit_job(job, {"type": "batch_start", "total_photos": total_photos, "phase": "gemini"})

    def _on_gemini_photo(idx: int, total: int, path: Path, payload: dict | None) -> None:
        preview: dict | None = None
        if payload:
            try:
                lo = LotOutput.from_model(payload)
                preview = {"auction_title": lo.auction_title or "", "sku": lo.sku}
            except (TypeError, ValueError, KeyError):
                preview = {"auction_title": "", "sku": None}
        _emit_job(
            job,
            {
                "type": "photo_analyzed",
                "index": idx,
                "total": total,
                "file_name": path.name,
                "preview": preview,
            },
        )

    def _run_parallel_analysis() -> list[dict | None]:
        return asyncio.run(
            analyze_photos_parallel_async(
                photos,
                manifest_tag=str(job.id),
                limit=settings.gemini_parallelism,
                on_each=_on_gemini_photo,
            )
        )

    def _run_parallel_analysis_threadpool() -> list[dict | None]:
        """Same concurrency cap as asyncio path when ``asyncio.run`` is unavailable."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        cap = max(1, settings.gemini_parallelism)
        payloads: list[dict | None] = [None] * total_photos
        with ThreadPoolExecutor(max_workers=cap) as pool:
            future_to_index = {
                pool.submit(analyze_path_with_retries, p, manifest_tag=str(job.id)): i
                for i, p in enumerate(photos)
            }
            for fut in as_completed(future_to_index):
                i = future_to_index[fut]
                p = photos[i]
                try:
                    r = fut.result()
                except Exception:
                    r = None
                payloads[i] = r
                _on_gemini_photo(i, total_photos, p, r)
        return payloads

    try:
        payloads = _run_parallel_analysis()
    except RuntimeError:
        # ``asyncio.run`` cannot nest inside an active loop; keep parallelism via thread pool.
        payloads = _run_parallel_analysis_threadpool()

    _apply_last_known_sku_fallback(payloads)
    success_count = sum(1 for p in payloads if p is not None)
    job.analyzed_photos = int(success_count)
    job.failed_photos = int(max(0, total_photos - success_count))
    # Pilot-level heuristic estimates; not exact billable token numbers from provider metering.
    job.est_input_tokens = int(total_photos * EST_INPUT_TOKENS_PER_PHOTO)
    job.est_output_tokens = int(success_count * EST_OUTPUT_TOKENS_PER_SUCCESS)
    db.commit()

    _emit_job(job, {"type": "batch_phase", "phase": "inventory", "total_photos": total_photos})

    single_lot_mode = bool(getattr(job, "single_lot_mode", False))
    prev_batch_key: str | None = None
    for i, (photo_path, payload) in enumerate(zip(photos, payloads)):
        _emit_job(
            job,
            {
                "type": "inventory_step",
                "index": i,
                "total": total_photos,
                "file_name": photo_path.name,
            },
        )
        _emit_job_debug(
            job,
            f"job={job.id} inventory enter i={i + 1}/{total_photos} file={photo_path.name}",
        )
        if _cancel_signaled(db, job.id, cancel_event):
            job.status = JobStatus.failed
            job.error_message = "Cancelled"
            job.cancel_requested = False
            db.commit()
            _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
            return
        batch_key = _subfolder_batch_key(folder, photo_path)
        if (
            not single_lot_mode
            and prev_batch_key is not None
            and batch_key != prev_batch_key
        ):
            _apply_inherit_sku_from_subfolder_name(job, current_lot, prev_batch_key)
            current_lot, pending_photo_ids, finalized = _finalize_open_lot(
                db, job.account_id, job.user_id, current_lot, pending_photo_ids, job.sale_id
            )
            db.commit()
            if finalized is not None:
                _emit_job(job, {"type": "lot_ready", "lot": _lot_sse_payload(finalized)})
        prev_batch_key = batch_key

        if not payload:
            # Keep photo files on the active lot even when Gemini fails so nothing “disappears” from inventory.
            if current_lot is None:
                current_lot = LotOutput(
                    sku=None,
                    auction_title="",
                    professional_description="",
                    specification_lines=[],
                    brands=[],
                    labels_and_tags=[],
                    condition="",
                    starting_price=None,
                    photo_count=0,
                    sku_needs_review=False,
                    lot_boundary_blank_page=False,
                )
                current_lot_folder_batch_key = batch_key
            current_lot.photo_count += 1
            row = _add_catalog_photo(db, job, photo_path)
            pending_photo_ids.append(row.id)
            db.commit()
            continue
        lot_candidate = LotOutput.from_model(payload)
        _emit_job_debug(
            job,
            "job=%s raw SKU=%s conf=%s title=%r"
            % (
                job.id,
                payload.get("SKU") or payload.get("sku"),
                payload.get("SkuConfidence") or payload.get("sku_read_confidence"),
                ((payload.get("AuctionTitle") or payload.get("Title") or payload.get("item_name") or "")[:120]),
            ),
        )
        if payload.get(_CT_SKU_INJECTED_FALLBACK_KEY):
            # Injected SKU is for display/continuity only — not a new tape read on this frame.
            lot_candidate.sku = None
            lot_candidate.sku_needs_review = False
        _strip_maker_stamp_misread_as_tape_sku(lot_candidate, payload)
        _strip_product_box_misread_as_tape_sku(lot_candidate, payload)
        _emit_job_debug(
            job,
            f"job={job.id} after strip i={i + 1} file={photo_path.name} sku={lot_candidate.sku!r}",
        )

        sku_ok = tape_sku_opens_new_lot(lot_candidate.sku)
        # ``last_known_sku_fallback`` can copy the tape SKU onto detail frames that never showed tape
        # in Gemini. Re-running tape verify on those frames is redundant (we already trust the open
        # lot's marking) and can stall for minutes when the audit model waits on a frame with no tape.
        continuation_same_tape = (
            current_lot is not None
            and bool(lot_candidate.sku)
            and tape_sku_same_marking(current_lot.sku, lot_candidate.sku)
        )
        # Second-pass tape verify before opening a new lot from a fresh SKU read (single-lot jobs
        # still use this on the first tape frame; merged follow-ups use ``_single_lot_maybe_adopt_tape_sku``).
        if (
            sku_ok
            and not continuation_same_tape
            and not settings.skip_tape_verify
            and lot_candidate.sku
        ):
            _emit_inventory_tick(job, index=i, total=total_photos, photo_path=photo_path, stage="tape_verify")
            t_verify = time.monotonic()
            claimed_marking = (
                tape_marking_canonical(lot_candidate.sku) or sku_digits(lot_candidate.sku) or ""
            ).strip()
            verified = verify_tape_sku(photo_path, claimed_marking)
            _emit_job_debug(
                job,
                "job=%s tape_verify i=%s file=%s claimed=%s result=%s ms=%.0f"
                % (
                    job.id,
                    i + 1,
                    photo_path.name,
                    claimed_marking,
                    verified,
                    (time.monotonic() - t_verify) * 1000.0,
                ),
            )
            if verified is not True:
                if verified is None and lot_candidate.sku:
                    lot_candidate.sku_needs_review = True
                    sku_ok = tape_sku_opens_new_lot(lot_candidate.sku)
                else:
                    lot_candidate.sku = None
                    sku_ok = False

        if current_lot is not None and _is_gemini_placeholder_lot(current_lot):
            saved_pc = current_lot.photo_count
            current_lot = lot_candidate
            current_lot.photo_count = saved_pc + 1
            _emit_inventory_tick(
                job, index=i, total=total_photos, photo_path=photo_path, stage="promote_after_gemini_failures"
            )
            row = _add_catalog_photo(db, job, photo_path)
            pending_photo_ids.append(row.id)
            db.commit()
            continue

        if single_lot_mode and current_lot is not None:
            _single_lot_maybe_adopt_tape_sku(
                job,
                current_lot,
                lot_candidate,
                photo_path,
                index=i,
                total_photos=total_photos,
            )
            _emit_inventory_tick(job, index=i, total=total_photos, photo_path=photo_path, stage="merge_detail")
            current_lot.merge_detail(payload)
            row = _add_catalog_photo(db, job, photo_path)
            pending_photo_ids.append(row.id)
            db.commit()
            continue

        if sku_ok:
            lot_candidate.sku = tape_marking_canonical(lot_candidate.sku) or lot_candidate.sku
            lot_candidate.sku_needs_review = False
            if current_lot is not None:
                # Do not split on repeated reads of the same lot tape across adjacent frames.
                if tape_sku_same_marking(current_lot.sku, lot_candidate.sku):
                    _emit_inventory_tick(job, index=i, total=total_photos, photo_path=photo_path, stage="merge_detail")
                    current_lot.merge_detail(payload)
                    row = _add_catalog_photo(db, job, photo_path)
                    pending_photo_ids.append(row.id)
                    db.commit()
                    continue
                # Guard against one-frame false-positive sku flips when content appears continuous.
                if _likely_same_item(current_lot, lot_candidate):
                    _emit_inventory_tick(job, index=i, total=total_photos, photo_path=photo_path, stage="merge_detail")
                    current_lot.merge_detail(payload)
                    row = _add_catalog_photo(db, job, photo_path)
                    pending_photo_ids.append(row.id)
                    db.commit()
                    continue
            if current_lot is not None:
                _apply_inherit_sku_from_subfolder_name(job, current_lot, current_lot_folder_batch_key)
                _emit_inventory_tick(job, index=i, total=total_photos, photo_path=photo_path, stage="save_lot")
                persisted = _save_lot(db, job.account_id, job.user_id, current_lot, job.sale_id)
                db.flush()
                _assign_photos_to_lot(db, persisted.id, pending_photo_ids)
                pending_photo_ids.clear()
                db.commit()
                _emit_job(job, {"type": "lot_ready", "lot": _lot_sse_payload(persisted)})
            current_lot = lot_candidate
            current_lot_folder_batch_key = batch_key
            row = _add_catalog_photo(db, job, photo_path)
            pending_photo_ids.append(row.id)
            db.commit()
            continue

        if current_lot is None:
            # First usable frame in this subfolder batch (no tape yet): open a lot so photos are not dropped.
            current_lot = lot_candidate
            current_lot_folder_batch_key = batch_key
            row = _add_catalog_photo(db, job, photo_path)
            pending_photo_ids.append(row.id)
            db.commit()
            continue

        _emit_inventory_tick(job, index=i, total=total_photos, photo_path=photo_path, stage="merge_detail")
        current_lot.merge_detail(payload)
        _emit_job_debug(job, f"job={job.id} merged_detail i={i + 1} file={photo_path.name}")
        row = _add_catalog_photo(db, job, photo_path)
        pending_photo_ids.append(row.id)
        db.commit()
        _emit_job_debug(job, f"job={job.id} committed i={i + 1} file={photo_path.name}")

    if current_lot is not None:
        _apply_inherit_sku_from_subfolder_name(job, current_lot, current_lot_folder_batch_key)
        _apply_single_lot_manual_sku(job, current_lot)
        current_lot, pending_photo_ids, finalized = _finalize_open_lot(
            db, job.account_id, job.user_id, current_lot, pending_photo_ids, job.sale_id
        )
        db.commit()
        if finalized is not None:
            _emit_job(job, {"type": "lot_ready", "lot": _lot_sse_payload(finalized)})

    recompute_sku_sanity_flags_for_sale(db, job.sale_id, job.account_id)
    job.status = JobStatus.completed
    job.cancel_requested = False
    db.commit()
    _verify_job_catalog_files_on_disk(db, job)
    _emit_job(job, {"type": "status", "status": "completed"})


LOT_AI_REGENERATE_MAX_PHOTOS = 80


def _verify_job_catalog_files_on_disk(db: Session, job: ProcessingJob) -> None:
    """Log when this job's upload batch has catalog rows pointing at files not on disk."""
    folder = Path(job.input_folder).resolve()
    if not folder.is_dir():
        return
    rows = (
        db.query(Photo.id, Photo.storage_path)
        .filter(Photo.account_id == job.account_id)
        .all()
    )
    in_batch: list[tuple[str, str]] = []
    for pid, path in rows:
        try:
            resolve_catalog_storage_path(path).relative_to(folder)
        except (ValueError, OSError):
            continue
        in_batch.append((pid, path))
    missing = [pid for pid, path in in_batch if not catalog_photo_file_on_disk(path)]
    if missing:
        _LOG.warning(
            "job=%s completed but %s/%s catalog photo file(s) missing on disk under %s "
            "(check upload volume, cwd, or multiple API replicas without shared storage)",
            job.id,
            len(missing),
            len(in_batch),
            folder,
        )


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


def _run_photo_analysis_parallel(paths: list[Path], *, manifest_tag: str) -> list[dict | None]:
    """Same execution strategy as ``process_job`` analysis (asyncio or thread pool)."""
    total = len(paths)
    try:
        return asyncio.run(
            analyze_photos_parallel_async(
                paths,
                manifest_tag=manifest_tag,
                limit=settings.gemini_parallelism,
                on_each=None,
            )
        )
    except RuntimeError:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        cap = max(1, settings.gemini_parallelism)
        payloads: list[dict | None] = [None] * total
        with ThreadPoolExecutor(max_workers=cap) as pool:
            future_to_index = {
                pool.submit(analyze_path_with_retries, p, manifest_tag=manifest_tag): i for i, p in enumerate(paths)
            }
            for fut in as_completed(future_to_index):
                i = future_to_index[fut]
                try:
                    payloads[i] = fut.result()
                except Exception:
                    payloads[i] = None
        return payloads


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
    if not (settings.google_api_key or "").strip():
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

    if settings.pipeline_triage_brand_audit:
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
    else:
        payloads = _run_photo_analysis_parallel(paths, manifest_tag=manifest_tag)
        _apply_last_known_sku_fallback(payloads)

        current = None
        for payload in payloads:
            if not payload:
                continue
            try:
                lot_candidate = LotOutput.from_model(payload)
            except (TypeError, ValueError, KeyError):
                continue
            _strip_maker_stamp_misread_as_tape_sku(lot_candidate, payload)
            _strip_product_box_misread_as_tape_sku(lot_candidate, payload)
            if payload.get(_CT_SKU_INJECTED_FALLBACK_KEY):
                lot_candidate.sku = None
                lot_candidate.sku_needs_review = False
            if current is None:
                current = lot_candidate
                continue
            current.merge_detail(payload)

        if current is None:
            current = _regenerate_fallback_lot_output(row)

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

    ct_cat, ct_sub = infer_ct_category(current)
    row.auction_title = current.auction_title
    row.professional_description = current.professional_description
    row.specification_lines_json = json.dumps(current.specification_lines, ensure_ascii=True)
    row.brands_json = json.dumps(current.brands, ensure_ascii=True)
    row.labels_and_tags_json = json.dumps(current.labels_and_tags, ensure_ascii=True)
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

