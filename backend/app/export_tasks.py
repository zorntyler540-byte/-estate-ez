from __future__ import annotations

import threading

from app.db import SessionLocal
from app.models import JobStatus, SalePhotoZipExport
from app.services.sale_zip_export import run_sale_photo_zip_export

_zip_active: set[str] = set()
_lock = threading.Lock()


def is_sale_zip_export_running(export_id: str) -> bool:
    with _lock:
        return export_id in _zip_active


def _runner(export_id: str, account_id: str) -> None:
    try:
        with SessionLocal() as db:
            ex = (
                db.query(SalePhotoZipExport)
                .filter(SalePhotoZipExport.id == export_id, SalePhotoZipExport.account_id == account_id)
                .first()
            )
            if ex is None or ex.status != JobStatus.queued:
                return
            run_sale_photo_zip_export(db, export_id)
    finally:
        with _lock:
            _zip_active.discard(export_id)


def start_sale_zip_export(export_id: str, account_id: str) -> None:
    with _lock:
        if export_id in _zip_active:
            raise RuntimeError("sale_zip_export_already_running")
        _zip_active.add(export_id)
    threading.Thread(
        target=_runner,
        args=(export_id, account_id),
        daemon=True,
        name=f"sale-zip-{export_id}",
    ).start()
