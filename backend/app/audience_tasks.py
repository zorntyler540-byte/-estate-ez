"""Background audience hunt (same threading pattern as AI regenerate)."""

from __future__ import annotations

import logging
import threading
import traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Lot
from app.services.audience_hunt import run_audience_hunt_for_lot

_LOG = logging.getLogger(__name__)

STALE_HUNT = timedelta(hours=2)

_active: set[str] = set()
_lock = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def lot_hunt_is_active(row: Lot | None) -> bool:
    if row is None or getattr(row, "audience_hunting_at", None) is None:
        return False
    started = _as_utc(row.audience_hunting_at)
    return (_utcnow() - started) < STALE_HUNT


def is_hunting(db: Session, *, lot_id: str, account_id: str) -> bool:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
    return lot_hunt_is_active(row)


def mark_lot_hunt_started(db: Session, *, lot_id: str, account_id: str) -> None:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
    if not row:
        raise ValueError("Lot not found")
    if lot_hunt_is_active(row):
        raise RuntimeError("audience_hunt_already_running")
    row.audience_hunting_at = _utcnow()
    row.audience_hunt_error = None
    db.commit()


def finish_lot_hunt(
    db: Session,
    *,
    lot_id: str,
    account_id: str,
    error: str | None = None,
) -> None:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
    if not row:
        return
    row.audience_hunting_at = None
    row.audience_hunt_error = (error or None)[:2000] if error else None
    db.commit()


def pop_hunt_error(db: Session, row: Lot) -> str | None:
    if lot_hunt_is_active(row):
        return None
    err = (getattr(row, "audience_hunt_error", None) or "").strip() or None
    if err:
        row.audience_hunt_error = None
        db.commit()
    return err


def start_audience_hunt_in_background(*, lot_id: str, account_id: str, force: bool) -> None:
    with _lock:
        if lot_id in _active:
            raise RuntimeError("audience_hunt_already_running")
        _active.add(lot_id)

    def _runner() -> None:
        err_msg: str | None = None
        try:
            with SessionLocal() as db:
                row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
                if row is None:
                    raise RuntimeError("Lot not found")
                run_audience_hunt_for_lot(db, row, force=force)
                db.commit()
        except Exception as exc:
            tb = traceback.format_exc()
            _LOG.error("audience hunt crashed lot_id=%s\n%s", lot_id, tb)
            err_msg = f"{type(exc).__name__}: {exc}"[:2000]
        finally:
            with SessionLocal() as db:
                finish_lot_hunt(
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
        name=f"audience-{lot_id[:8]}",
    ).start()
