from __future__ import annotations

import logging
import threading
import traceback

from app.db import SessionLocal
from app.services.processing import run_lot_ai_regenerate_and_save

_LOG = logging.getLogger(__name__)

_active: set[str] = set()
_errors: dict[str, str] = {}
_lock = threading.Lock()


def is_regenerating(lot_id: str) -> bool:
    with _lock:
        return lot_id in _active


def pop_regeneration_error(lot_id: str) -> str | None:
    with _lock:
        return _errors.pop(lot_id, None)


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
        _errors.pop(lot_id, None)

    def _runner() -> None:
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
            with _lock:
                _errors[lot_id] = f"{type(exc).__name__}: {exc}"[:2000]
        finally:
            with _lock:
                _active.discard(lot_id)

    threading.Thread(
        target=_runner,
        daemon=True,
        name=f"regen-{lot_id[:8]}",
    ).start()
