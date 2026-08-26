"""Background audience hunt (same threading pattern as AI regenerate)."""

from __future__ import annotations

import logging
import threading
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Lot
from app.services.audience_hunt import run_audience_hunt_for_lot

_LOG = logging.getLogger(__name__)

STALE_HUNT = timedelta(hours=2)
# Keep a finished sale snapshot long enough for the UI to show 12/12 once.
_COMPLETED_HOLD = timedelta(seconds=20)

_active: set[str] = set()
_lot_phase: dict[str, str] = {}
_sale_runs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def reset_hunt_runtime_state() -> None:
    """Test helper — clear in-memory hunt progress."""
    with _lock:
        _active.clear()
        _lot_phase.clear()
        _sale_runs.clear()


def lot_hunt_is_active(row: Lot | None) -> bool:
    if row is None or getattr(row, "audience_hunting_at", None) is None:
        return False
    started = _as_utc(row.audience_hunting_at)
    return (_utcnow() - started) < STALE_HUNT


def lot_hunt_phase(lot_id: str) -> str | None:
    with _lock:
        return _lot_phase.get(lot_id)


def set_lot_hunt_phase(lot_id: str, phase: str | None) -> None:
    with _lock:
        if phase:
            _lot_phase[lot_id] = phase
        else:
            _lot_phase.pop(lot_id, None)


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
    set_lot_hunt_phase(lot_id, "queued")


def finish_lot_hunt(
    db: Session,
    *,
    lot_id: str,
    account_id: str,
    error: str | None = None,
) -> None:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
    if not row:
        set_lot_hunt_phase(lot_id, None)
        return
    row.audience_hunting_at = None
    row.audience_hunt_error = (error or None)[:2000] if error else None
    db.commit()
    set_lot_hunt_phase(lot_id, None)
    _mark_sale_lot_finished(sale_id=row.sale_id, lot_id=lot_id)


def pop_hunt_error(db: Session, row: Lot) -> str | None:
    if lot_hunt_is_active(row):
        return None
    err = (getattr(row, "audience_hunt_error", None) or "").strip() or None
    if err:
        row.audience_hunt_error = None
        db.commit()
    return err


def register_sale_hunt(
    *,
    sale_id: str,
    account_id: str,
    lot_ids: list[str],
    skus: dict[str, str | None],
) -> None:
    ordered = [x for x in lot_ids if (x or "").strip()]
    if not ordered:
        return
    with _lock:
        _sale_runs[sale_id] = {
            "account_id": account_id,
            "lot_ids": ordered,
            "skus": {lid: skus.get(lid) for lid in ordered},
            "finished_ids": [],
            "current_id": ordered[0],
            "complete": False,
            "completed_at": None,
        }


def _mark_sale_lot_finished(*, sale_id: str | None, lot_id: str) -> None:
    if not sale_id:
        return
    with _lock:
        run = _sale_runs.get(sale_id)
        if not run or lot_id not in run["lot_ids"]:
            return
        if lot_id not in run["finished_ids"]:
            run["finished_ids"].append(lot_id)
        remaining = [x for x in run["lot_ids"] if x not in run["finished_ids"]]
        run["current_id"] = remaining[0] if remaining else None
        if not remaining:
            run["complete"] = True
            run["completed_at"] = _utcnow()


def _sale_run_snapshot(run: dict[str, Any]) -> dict[str, Any]:
    finished = set(run["finished_ids"])
    current_id = run.get("current_id")
    lots: list[dict[str, Any]] = []
    for lot_id in run["lot_ids"]:
        sku = run["skus"].get(lot_id)
        sku_s = (str(sku).strip() or None) if sku is not None else None
        if lot_id in finished:
            lots.append({"lot_id": lot_id, "sku": sku_s, "status": "done", "phase": None})
        elif lot_id == current_id:
            lots.append(
                {
                    "lot_id": lot_id,
                    "sku": sku_s,
                    "status": "running",
                    "phase": _lot_phase.get(lot_id) or "searching",
                }
            )
        else:
            lots.append(
                {
                    "lot_id": lot_id,
                    "sku": sku_s,
                    "status": "queued",
                    "phase": _lot_phase.get(lot_id) or "queued",
                }
            )
    current_sku = None
    current_phase = None
    if current_id:
        current_sku = run["skus"].get(current_id)
        if current_sku is not None:
            current_sku = str(current_sku).strip() or None
        current_phase = _lot_phase.get(current_id) or "searching"
    total = len(run["lot_ids"])
    done = len(run["finished_ids"])
    complete = bool(run.get("complete"))
    return {
        "active": (not complete) and total > 0,
        "total": total,
        "done": done,
        "current_lot_id": None if complete else current_id,
        "current_sku": None if complete else current_sku,
        "current_phase": None if complete else current_phase,
        "lots": lots,
    }


def sale_hunt_status(*, sale_id: str, account_id: str) -> dict[str, Any]:
    """In-memory progress for a sale-wide hunt running in this API process."""
    with _lock:
        run = _sale_runs.get(sale_id)
        if not run or run.get("account_id") != account_id:
            return {
                "active": False,
                "total": 0,
                "done": 0,
                "current_lot_id": None,
                "current_sku": None,
                "current_phase": None,
                "lots": [],
            }
        completed_at = run.get("completed_at")
        if run.get("complete") and completed_at is not None:
            finished_at = _as_utc(completed_at) if isinstance(completed_at, datetime) else _utcnow()
            if _utcnow() - finished_at > _COMPLETED_HOLD:
                _sale_runs.pop(sale_id, None)
                return {
                    "active": False,
                    "total": 0,
                    "done": 0,
                    "current_lot_id": None,
                    "current_sku": None,
                    "current_phase": None,
                    "lots": [],
                }
        return _sale_run_snapshot(run)


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
                run_audience_hunt_for_lot(
                    db,
                    row,
                    force=force,
                    on_phase=lambda phase: set_lot_hunt_phase(lot_id, phase),
                )
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


def start_sequential_hunts_in_background(
    *,
    account_id: str,
    sale_id: str,
    lot_ids: list[str],
    skus: dict[str, str | None],
    force: bool,
) -> None:
    """Hunt existing lots one at a time (Gemini + Search). Caller already marked hunting_at."""
    ordered = [x for x in lot_ids if (x or "").strip()]
    if not ordered:
        return
    register_sale_hunt(sale_id=sale_id, account_id=account_id, lot_ids=ordered, skus=skus)
    with _lock:
        for lot_id in ordered:
            _active.add(lot_id)

    def _runner() -> None:
        for lot_id in ordered:
            err_msg: str | None = None
            with _lock:
                run = _sale_runs.get(sale_id)
                if run:
                    run["current_id"] = lot_id
            set_lot_hunt_phase(lot_id, "searching")
            try:
                with SessionLocal() as db:
                    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
                    if row is None:
                        raise RuntimeError("Lot not found")
                    run_audience_hunt_for_lot(
                        db,
                        row,
                        force=force,
                        on_phase=lambda phase, lid=lot_id: set_lot_hunt_phase(lid, phase),
                    )
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
        name=f"audience-sale-{ordered[0][:8]}",
    ).start()
