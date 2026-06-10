from __future__ import annotations

import logging
import threading
import traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Lot
from app.services.processing import run_lot_ai_regenerate_and_save

_LOG = logging.getLogger(__name__)

STALE_REGENERATE = timedelta(hours=2)

_active: set[str] = set()
_lock = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def lot_regenerate_is_active(row: Lot | None) -> bool:
    if row is None or row.ai_regenerate_started_at is None:
        return False
    started = _as_utc(row.ai_regenerate_started_at)
    return (_utcnow() - started) < STALE_REGENERATE


def mark_lot_regenerate_started(db: Session, *, lot_id: str, account_id: str) -> None:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
    if not row:
        raise ValueError("Lot not found")
    if lot_regenerate_is_active(row):
        raise RuntimeError("regenerate_already_running")
    row.ai_regenerate_started_at = _utcnow()
    row.ai_regenerate_error = None
    db.commit()


def finish_lot_regenerate(
    db: Session,
    *,
    lot_id: str,
    account_id: str,
    error: str | None = None,
) -> None:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
    if not row:
        return
    row.ai_regenerate_started_at = None
    row.ai_regenerate_error = (error or None)[:2000] if error else None
    db.commit()


def pop_regeneration_error(db: Session, row: Lot) -> str | None:
    if lot_regenerate_is_active(row):
        return None
    err = (row.ai_regenerate_error or "").strip() or None
    if err:
        row.ai_regenerate_error = None
        db.commit()
    return err


def is_regenerating(db: Session, *, lot_id: str, account_id: str) -> bool:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
    return lot_regenerate_is_active(row)


def start_lot_regeneration_in_background(
    *,
    lot_id: str,
    account_id: str,
    user_id: str,
    photo_ids: list[str] | None,
    preserve_sku: bool,
) -> None:
    with _lock:
        if lot_id in _active:
            raise RuntimeError("regenerate_already_running")
        _active.add(lot_id)

    def _runner() -> None:
        err_msg: str | None = None
        try:
            with SessionLocal() as db:
                run_lot_ai_regenerate_and_save(
                    db,
                    account_id=account_id,
                    user_id=user_id,
                    lot_id=lot_id,
                    photo_ids=photo_ids,
                    preserve_sku=preserve_sku,
                )
        except Exception as exc:
            tb = traceback.format_exc()
            _LOG.error("lot AI regenerate crashed lot_id=%s\n%s", lot_id, tb)
            err_msg = f"{type(exc).__name__}: {exc}"[:2000]
        finally:
            with SessionLocal() as db:
                finish_lot_regenerate(
                    db,
                    lot_id=lot_id,
                    account_id=account_id,
                    error=err_msg,
                )
            with _lock:
                _active.discard(lot_id)

    threading.Thread(
        target=_runner,
        daemon=True,
        name=f"regen-{lot_id[:8]}",
    ).start()
