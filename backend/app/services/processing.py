from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.core.gemini import analyze_path_with_retries, analyze_photos_parallel_async, verify_tape_sku
from app.core.lotting import (
    LotOutput,
    folder_token_as_inherited_sku,
    normalize_tape_sku_input,
    payload_lot_boundary_blank_page,
    sku_digits,
    tape_marking_canonical,
    tape_sku_opens_new_lot,
    tape_sku_same_marking,
)
from sqlalchemy import update

from app.models import JobStatus, Lot, Photo, ProcessingJob
from app.services.category_inference import infer_ct_category
from app.services import job_events

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}

# Set by ``_apply_last_known_sku_fallback`` when SKU was copied from a prior frame (not read on this image).
# Must not open lots or run tape verify — otherwise stale digits + new lot SKU causes spurious verify stalls.
_CT_SKU_INJECTED_FALLBACK_KEY = "_ct_sku_injected_fallback"

_LOG = logging.getLogger(__name__)


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
)


def _blob_suggests_purple_tape_language(blob: str) -> bool:
    """True when prose likely refers to purple/lavender *estate* tape (not substrings like ``plate`` in ``silverplate``)."""
    if re.search(r"\b(purple|lavender)\b", blob):
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


def _strip_maker_stamp_misread_as_tape_sku(lot: LotOutput, raw_payload: dict | None = None) -> None:
    """
    Detail shots of silverplate often show stamped pattern numbers (e.g. 1420) on the base — not purple tape.

    When the model puts those digits in ``sku``, we would otherwise run tape verify and churn lot boundaries.

    Sheffield reproduction / hand-chased / silverplate base titles: clear **any** sku unless the row text
    uses real tape wording (word-boundary purple/lavender or phrases like ``purple tape``), not naive substring
    checks that misfire on ``silverplate``. Other cases: digits-only plus underside hints.
    """
    if not lot.sku or lot.lot_boundary_blank_page:
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
    """
    current_tokens = _title_tokens(current_lot.auction_title)
    candidate_tokens = _title_tokens(candidate.auction_title)
    token_overlap = bool(current_tokens and candidate_tokens and (current_tokens & candidate_tokens))
    current_brands = {b.lower() for b in current_lot.brands if b}
    candidate_brands = {b.lower() for b in candidate.brands if b}
    brand_overlap = bool(current_brands and candidate_brands and (current_brands & candidate_brands))
    return token_overlap or brand_overlap


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

    A blank divider page ends the prior lot in the inventory pass; ``last`` must reset here so the
    next frame (typically the hero shot with tape) is not filled with the previous lot's SKU when
    Gemini leaves ``SKU`` empty on that frame.
    """
    if not settings.last_known_sku_fallback:
        return
    last: str | None = None
    for p in payloads:
        if not p or not isinstance(p, dict):
            continue
        if payload_lot_boundary_blank_page(p):
            last = None
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
    db.execute(update(Photo).where(Photo.id.in_(photo_ids)).values(lot_id=lot_id))


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
    }


def _emit_job(job: ProcessingJob, payload: dict) -> None:
    job_events.publish(job.user_id, job.id, {**payload, "job_id": job.id})


def _emit_job_debug(job: ProcessingJob, message: str) -> None:
    """Mirror debug lines to stderr and SSE so Railway log search is optional."""
    if not settings.job_debug_inventory:
        return
    _LOG.info("%s", message)
    _emit_job(job, {"type": "job_log", "message": message})


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


def process_job(
    db: Session,
    job: ProcessingJob,
    *,
    cancel_event: threading.Event | None = None,
) -> None:
    job.status = JobStatus.processing
    job.error_message = None
    job.cancel_requested = False
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

    photos = sorted(
        [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
        key=lambda p: str(p).lower(),
    )
    if not photos:
        job.status = JobStatus.failed
        job.error_message = "No supported images found in that folder (JPEG, PNG, WebP, HEIC)"
        job.cancel_requested = False
        db.commit()
        _emit_job(job, {"type": "status", "status": "failed", "error_message": job.error_message})
        return
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
            if current_lot is not None:
                current_lot.photo_count += 1
                row = Photo(
                    account_id=job.account_id,
                    user_id=job.user_id,
                    lot_id=None,
                    original_name=photo_path.name,
                    storage_path=str(photo_path.resolve()),
                )
                db.add(row)
                db.flush()
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
        _emit_job_debug(
            job,
            f"job={job.id} after strip i={i + 1} file={photo_path.name} sku={lot_candidate.sku!r} blank={lot_candidate.lot_boundary_blank_page}",
        )
        if lot_candidate.lot_boundary_blank_page:
            if single_lot_mode:
                continue
            if current_lot is not None:
                _apply_inherit_sku_from_subfolder_name(job, current_lot, current_lot_folder_batch_key)
                current_lot, pending_photo_ids, finalized = _finalize_open_lot(
                    db, job.account_id, job.user_id, current_lot, pending_photo_ids, job.sale_id
                )
                db.commit()
                if finalized is not None:
                    _emit_job(job, {"type": "lot_ready", "lot": _lot_sse_payload(finalized)})
            current_lot = None
            current_lot_folder_batch_key = None
            continue

        sku_ok = tape_sku_opens_new_lot(lot_candidate.sku)
        # ``last_known_sku_fallback`` can copy the tape SKU onto detail frames that never showed tape
        # in Gemini. Re-running tape verify on those frames is redundant (we already trust the open
        # lot's marking) and can stall for minutes when the audit model waits on a frame with no tape.
        continuation_same_tape = (
            current_lot is not None
            and bool(lot_candidate.sku)
            and tape_sku_same_marking(current_lot.sku, lot_candidate.sku)
        )
        # One-lot (mobile) jobs merge every frame; tape-split verification only adds latency and can
        # mis-trigger on retail packaging digits. The SDK verify path also has no hard timeout and
        # has wedged workers in production when the API never returns.
        if (
            sku_ok
            and not continuation_same_tape
            and not settings.skip_tape_verify
            and lot_candidate.sku
            and not single_lot_mode
        ):
            t_verify = time.monotonic()
            verified = verify_tape_sku(photo_path, sku_digits(lot_candidate.sku))
            _emit_job_debug(
                job,
                "job=%s tape_verify i=%s file=%s claimed=%s result=%s ms=%.0f"
                % (
                    job.id,
                    i + 1,
                    photo_path.name,
                    sku_digits(lot_candidate.sku),
                    verified,
                    (time.monotonic() - t_verify) * 1000.0,
                ),
            )
            if verified is not True:
                lot_candidate.sku = None
                sku_ok = False

        if single_lot_mode and current_lot is not None:
            current_lot.merge_detail(payload)
            row = Photo(
                account_id=job.account_id,
                user_id=job.user_id,
                lot_id=None,
                original_name=photo_path.name,
                storage_path=str(photo_path.resolve()),
            )
            db.add(row)
            db.flush()
            pending_photo_ids.append(row.id)
            db.commit()
            continue

        if sku_ok:
            lot_candidate.sku = tape_marking_canonical(lot_candidate.sku) or lot_candidate.sku
            lot_candidate.sku_needs_review = False
            if current_lot is not None:
                # Do not split on repeated reads of the same lot tape across adjacent frames.
                if tape_sku_same_marking(current_lot.sku, lot_candidate.sku):
                    current_lot.merge_detail(payload)
                    row = Photo(
                        account_id=job.account_id,
                        user_id=job.user_id,
                        lot_id=None,
                        original_name=photo_path.name,
                        storage_path=str(photo_path.resolve()),
                    )
                    db.add(row)
                    db.flush()
                    pending_photo_ids.append(row.id)
                    db.commit()
                    continue
                # Guard against one-frame false-positive sku flips when content appears continuous.
                if _likely_same_item(current_lot, lot_candidate):
                    current_lot.merge_detail(payload)
                    row = Photo(
                        account_id=job.account_id,
                        user_id=job.user_id,
                        lot_id=None,
                        original_name=photo_path.name,
                        storage_path=str(photo_path.resolve()),
                    )
                    db.add(row)
                    db.flush()
                    pending_photo_ids.append(row.id)
                    db.commit()
                    continue
            if current_lot is not None:
                _apply_inherit_sku_from_subfolder_name(job, current_lot, current_lot_folder_batch_key)
                persisted = _save_lot(db, job.account_id, job.user_id, current_lot, job.sale_id)
                db.flush()
                _assign_photos_to_lot(db, persisted.id, pending_photo_ids)
                pending_photo_ids.clear()
                db.commit()
                _emit_job(job, {"type": "lot_ready", "lot": _lot_sse_payload(persisted)})
            current_lot = lot_candidate
            current_lot_folder_batch_key = batch_key
            row = Photo(
                account_id=job.account_id,
                user_id=job.user_id,
                lot_id=None,
                original_name=photo_path.name,
                storage_path=str(photo_path.resolve()),
            )
            db.add(row)
            db.flush()
            pending_photo_ids.append(row.id)
            db.commit()
            continue

        if current_lot is None:
            # First usable frame in this subfolder batch (no tape yet): open a lot so photos are not dropped.
            current_lot = lot_candidate
            current_lot_folder_batch_key = batch_key
            row = Photo(
                account_id=job.account_id,
                user_id=job.user_id,
                lot_id=None,
                original_name=photo_path.name,
                storage_path=str(photo_path.resolve()),
            )
            db.add(row)
            db.flush()
            pending_photo_ids.append(row.id)
            db.commit()
            continue

        current_lot.merge_detail(payload)
        _emit_job_debug(job, f"job={job.id} merged_detail i={i + 1} file={photo_path.name}")
        row = Photo(
            account_id=job.account_id,
            user_id=job.user_id,
            lot_id=None,
            original_name=photo_path.name,
            storage_path=str(photo_path.resolve()),
        )
        db.add(row)
        db.flush()
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

    job.status = JobStatus.completed
    job.cancel_requested = False
    db.commit()
    _emit_job(job, {"type": "status", "status": "completed"})

