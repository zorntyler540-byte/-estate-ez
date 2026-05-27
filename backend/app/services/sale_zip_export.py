from __future__ import annotations

import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.catalog_storage import resolve_catalog_storage_path
from app.core.lotting import normalize_tape_sku_input, sku_digits
from app.models import JobStatus, Lot, Photo, SalePhotoZipExport

EXPORT_ROOT = Path("data") / "exports"


def export_dir_for_account(account_id: str) -> Path:
    d = (EXPORT_ROOT / account_id).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def count_sale_catalog_photos(db: Session, sale_id: str, account_id: str) -> int:
    return (
        db.query(func.count(Photo.id))
        .join(Lot, Photo.lot_id == Lot.id)
        .filter(
            Lot.sale_id == sale_id,
            Lot.account_id == account_id,
            Photo.account_id == account_id,
        )
        .scalar()
        or 0
    )


def _lot_folder_name(lot: Lot, used_folders: set[str]) -> str:
    """
    One ZIP folder per lot, named from the estate tape / lot SKU (not a sale-wide sequence).
    Duplicate SKUs in one sale get ``lot-045-2``, etc.
    """
    raw = (lot.sku or "").strip()
    if raw:
        digits = normalize_tape_sku_input(raw) or sku_digits(raw) or None
        if digits:
            candidate = f"lot-{digits}"
        else:
            slug = re.sub(r"[^\w.\-]+", "_", raw[:40]).strip("._")
            candidate = f"lot-{slug}" if slug else f"lot-no-sku-{lot.id.replace('-', '')[:8]}"
    else:
        candidate = f"lot-no-sku-{lot.id.replace('-', '')[:8]}"
    if candidate not in used_folders:
        used_folders.add(candidate)
        return candidate
    for n in range(2, 10000):
        alt = f"{candidate}-{n}"
        if alt not in used_folders:
            used_folders.add(alt)
            return alt
    return f"{candidate}-{lot.id.replace('-', '')[:8]}"


def _arc_base(original_name: str, photo_index: int, used: set[str]) -> str:
    base = Path(original_name.replace("\\", "/")).name
    base = re.sub(r"[^\w.\-]+", "_", base).strip("._") or "photo"
    if len(base) > 180:
        stem, suf = Path(base).stem[:120], Path(base).suffix
        base = f"{stem}{suf}"
    name = f"{photo_index:03d}_{base}"
    if name not in used:
        used.add(name)
        return name
    for n in range(2, 10000):
        alt = f"{photo_index:03d}_{n}_{base}"
        if alt not in used:
            used.add(alt)
            return alt
    return name


def cleanup_expired_sale_zip_exports(db: Session, account_id: str) -> None:
    now = datetime.now(timezone.utc)
    rows = (
        db.query(SalePhotoZipExport)
        .filter(
            SalePhotoZipExport.account_id == account_id,
            SalePhotoZipExport.status == JobStatus.completed,
            SalePhotoZipExport.expires_at.isnot(None),
            SalePhotoZipExport.expires_at < now,
        )
        .all()
    )
    for r in rows:
        if r.zip_path:
            Path(r.zip_path).unlink(missing_ok=True)
        db.delete(r)
    if rows:
        db.commit()


def run_sale_photo_zip_export(db: Session, export_id: str) -> None:
    row = db.query(SalePhotoZipExport).filter(SalePhotoZipExport.id == export_id).first()
    if row is None or row.status != JobStatus.queued:
        return
    row.status = JobStatus.processing
    row.error_message = None
    row.photo_files_written = 0
    db.commit()

    zip_final = export_dir_for_account(row.account_id) / f"sale-{row.sale_id}-{export_id}.zip"
    try:
        lots = (
            db.query(Lot)
            .filter(
                Lot.sale_id == row.sale_id,
                Lot.account_id == row.account_id,
            )
            .order_by(Lot.created_at.asc())
            .all()
        )
        written = 0
        used_folders: set[str] = set()
        with zipfile.ZipFile(zip_final, "w", compression=zipfile.ZIP_STORED) as zf:
            for lot in lots:
                folder = _lot_folder_name(lot, used_folders)
                used_names: set[str] = set()
                photos = (
                    db.query(Photo)
                    .filter(
                        Photo.lot_id == lot.id,
                        Photo.account_id == row.account_id,
                    )
                    .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
                    .all()
                )
                for pi, ph in enumerate(photos, start=1):
                    src = resolve_catalog_storage_path(ph.storage_path)
                    if not src.is_file():
                        continue
                    base = _arc_base(ph.original_name, pi, used_names)
                    arcname = f"{folder}/{base}"
                    zf.write(src, arcname=arcname)
                    written += 1
                    if written % 100 == 0:
                        row.photo_files_written = written
                        db.commit()
        if written == 0:
            zip_final.unlink(missing_ok=True)
            row.status = JobStatus.failed
            row.error_message = "No photo files found on disk for this sale."
            row.zip_path = None
            db.commit()
            return
        row.status = JobStatus.completed
        row.zip_path = str(zip_final)
        row.photo_files_written = written
        row.expires_at = datetime.now(timezone.utc) + timedelta(hours=48)
        db.commit()
    except Exception as e:
        zip_final.unlink(missing_ok=True)
        row = db.query(SalePhotoZipExport).filter(SalePhotoZipExport.id == export_id).first()
        if row:
            row.status = JobStatus.failed
            row.error_message = str(e)[:2000]
            row.zip_path = None
            db.commit()
