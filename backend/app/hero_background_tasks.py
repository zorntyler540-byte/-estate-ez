"""Sale-wide AI backgrounds. One listing photo per lot, one lot at a time.

The HTTP call only starts the run. Gemini takes several seconds per cover, so a
full sale cannot finish inside one request.
"""

from __future__ import annotations

import logging
import threading

from app.catalog_storage import resolve_catalog_storage_path
from app.db import SessionLocal
from app.models import Lot, Photo
from app.services.hero_background import render_hero_ai_background
from app.services.plain_background import (
    discard_plain_bg_original,
    ensure_plain_bg_original,
    first_website_photo,
    plain_bg_edit_source_bytes,
    write_replaced_catalog_jpeg,
)

_LOG = logging.getLogger(__name__)
_lock = threading.Lock()
_runs: dict[str, dict] = {}


def hero_background_status(*, sale_id: str, account_id: str) -> dict:
    with _lock:
        run = _runs.get(sale_id)
        if not run or run.get("account_id") != account_id:
            return _idle()
        return {key: run.get(key) for key in _idle()}


def start_sale_hero_backgrounds(
    *,
    account_id: str,
    sale_id: str,
    lot_ids: list[str],
    color: str,
) -> dict:
    """Start a background pass. Raises RuntimeError when this sale is already running."""
    ordered = [lot_id for lot_id in lot_ids if (lot_id or "").strip()]
    with _lock:
        current = _runs.get(sale_id)
        if current and current.get("running") and current.get("account_id") == account_id:
            raise RuntimeError("hero_background_already_running")
        _runs[sale_id] = {
            "account_id": account_id,
            "running": True,
            "color": color,
            "total": len(ordered),
            "done": 0,
            "updated": 0,
            "failed": 0,
            "skipped": 0,
            "current_sku": None,
            "last_error": None,
        }
        snapshot = {key: _runs[sale_id].get(key) for key in _idle()}
    if not ordered:
        with _lock:
            _runs[sale_id]["running"] = False
            snapshot = {key: _runs[sale_id].get(key) for key in _idle()}
        return snapshot

    threading.Thread(
        target=_run_sale,
        kwargs={"account_id": account_id, "sale_id": sale_id, "lot_ids": ordered, "color": color},
        daemon=True,
        name=f"hero-bg-{sale_id[:8]}",
    ).start()
    return snapshot


def process_sale_hero_backgrounds(db, *, account_id: str, sale_id: str, lot_ids: list[str], color: str) -> dict:
    """Apply AI to each lot's first website photo. Commits one lot at a time."""
    updated = 0
    failed = 0
    skipped = 0
    last_error: str | None = None
    for index, lot_id in enumerate(lot_ids):
        lot = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id, Lot.sale_id == sale_id).first()
        sku = ((lot.sku or "").strip() if lot is not None else "") or None
        _set_progress(sale_id, current_sku=sku, done=index)
        if lot is None:
            skipped += 1
            _set_progress(sale_id, done=index + 1, skipped=skipped, current_sku=sku)
            continue
        photos = (
            db.query(Photo)
            .filter(Photo.lot_id == lot.id, Photo.account_id == account_id)
            .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
            .all()
        )
        target = first_website_photo(photos)
        if target is None:
            skipped += 1
            _set_progress(sale_id, done=index + 1, skipped=skipped, current_sku=sku)
            continue
        try:
            apply_ai_background_to_photo(db, account_id, target, color)
            db.commit()
            updated += 1
        except Exception as exc:
            db.rollback()
            failed += 1
            last_error = str(exc).strip().splitlines()[0][:300] or "AI background failed"
            _LOG.warning("sale hero background failed lot_id=%s sku=%s", lot_id, sku)
        _set_progress(
            sale_id,
            done=index + 1,
            updated=updated,
            failed=failed,
            skipped=skipped,
            last_error=last_error,
            current_sku=sku,
        )
    summary = {
        "updated": updated,
        "failed": failed,
        "skipped": skipped,
        "last_error": last_error,
        "done": len(lot_ids),
    }
    _set_progress(sale_id, **summary, current_sku=None)
    return summary


def apply_ai_background_to_photo(db, account_id: str, row: Photo, color: str) -> None:
    """Keep the original once, then replace the catalog file. The db session is not committed here."""
    src = resolve_catalog_storage_path(row.storage_path)
    if not src.is_file():
        raise RuntimeError(f"{row.original_name or 'photo'}: file missing")
    fresh = ensure_plain_bg_original(row.storage_path, src)
    if fresh:
        row.plain_bg_saved = True
    try:
        raw = plain_bg_edit_source_bytes(row.storage_path, src)
        jpeg = render_hero_ai_background(raw, color)
        write_replaced_catalog_jpeg(account_id, row, jpeg)
        row.plain_bg_saved = True
    except Exception:
        if fresh:
            discard_plain_bg_original(row.storage_path)
            row.plain_bg_saved = False
        raise


def _run_sale(*, account_id: str, sale_id: str, lot_ids: list[str], color: str) -> None:
    try:
        with SessionLocal() as db:
            process_sale_hero_backgrounds(
                db,
                account_id=account_id,
                sale_id=sale_id,
                lot_ids=lot_ids,
                color=color,
            )
    except Exception as exc:
        _LOG.exception("sale hero background crashed sale_id=%s", sale_id)
        _set_progress(sale_id, last_error=str(exc).splitlines()[0][:300] or "AI background failed")
    finally:
        _set_progress(sale_id, running=False, current_sku=None)


def _set_progress(sale_id: str, **fields: object) -> None:
    with _lock:
        run = _runs.get(sale_id)
        if not run:
            return
        run.update(fields)


def _idle() -> dict:
    return {
        "running": False,
        "color": "studio",
        "total": 0,
        "done": 0,
        "updated": 0,
        "failed": 0,
        "skipped": 0,
        "current_sku": None,
        "last_error": None,
    }
