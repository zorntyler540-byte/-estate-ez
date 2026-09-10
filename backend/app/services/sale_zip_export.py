from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.catalog_storage import resolve_catalog_storage_path
from app.models import JobStatus, Lot, Photo, SalePhotoZipExport
from app.services.photo_order import listing_photo_sort_key
from app.services.photo_roles import is_hero_eligible_photo_role, normalize_catalog_photo_role

EXPORT_ROOT = Path("data") / "exports"
ZIP_KIND_ALL = "all"
ZIP_KIND_HEROES = "heroes"
ZIP_KINDS = frozenset({ZIP_KIND_ALL, ZIP_KIND_HEROES})


def export_dir_for_account(account_id: str) -> Path:
    d = (EXPORT_ROOT / account_id).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _zip_lot_filters(
    sale_id: str,
    account_id: str,
    filter_ctseller_sale_id: int | None = None,
) -> list:
    clauses = [
        Lot.sale_id == sale_id,
        Lot.account_id == account_id,
        Photo.account_id == account_id,
    ]
    if filter_ctseller_sale_id is not None:
        clauses.append(Lot.ctseller_item_id.isnot(None))
        clauses.append(Lot.ctseller_sale_id == int(filter_ctseller_sale_id))
    return clauses


def count_sale_catalog_photos(
    db: Session, sale_id: str, account_id: str, filter_ctseller_sale_id: int | None = None
) -> int:
    return (
        db.query(func.count(Photo.id))
        .join(Lot, Photo.lot_id == Lot.id)
        .filter(*_zip_lot_filters(sale_id, account_id, filter_ctseller_sale_id))
        .scalar()
        or 0
    )


def count_sale_hero_photos(
    db: Session, sale_id: str, account_id: str, filter_ctseller_sale_id: int | None = None
) -> int:
    """Lots in this sale that have at least one catalog photo (one hero zip entry each)."""
    return (
        db.query(func.count(func.distinct(Lot.id)))
        .join(Photo, Photo.lot_id == Lot.id)
        .filter(*_zip_lot_filters(sale_id, account_id, filter_ctseller_sale_id))
        .scalar()
        or 0
    )


def normalize_sale_zip_kind(raw: object | None) -> str:
    kind = str(raw or ZIP_KIND_ALL).strip().lower()
    return kind if kind in ZIP_KINDS else ZIP_KIND_ALL


def pick_lot_hero_photo(photos: list[Photo]) -> Photo | None:
    """One listing photo per lot: AI hero, else first non-tape frame. Never prefer SKU tape."""
    if not photos:
        return None
    heroes = [
        p
        for p in photos
        if normalize_catalog_photo_role(p.photo_role) == "hero"
        and is_hero_eligible_photo_role(p.photo_role)
    ]
    if heroes:
        return min(heroes, key=listing_photo_sort_key)
    nontape = [p for p in photos if is_hero_eligible_photo_role(p.photo_role)]
    pool = nontape or photos
    return min(pool, key=listing_photo_sort_key)


_WIN_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)
_MAX_EXPORT_STEM = 100


def _short_lot_id(lot: Lot) -> str:
    return lot.id.replace("-", "")[:8]


def _safe_fs_token(raw: str, max_len: int) -> str:
    """Windows-safe file/folder token: no path separators or reserved device names."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(raw or ""))
    s = re.sub(r"[^\w.\-]+", "-", s, flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip(".-")
    if not s:
        return ""
    if s.upper() in _WIN_RESERVED_STEMS:
        s = f"lot-{s}"
    return s[:max_len].rstrip(".-")


def _sku_stem(lot: Lot) -> str:
    """Full estate SKU (JZ5137AD stays JZ5137AD). Digit-only SKUs keep a ``lot-`` prefix."""
    raw = (lot.sku or "").strip()
    if not raw:
        return f"lot-no-sku-{_short_lot_id(lot)}"
    compact = re.sub(r"\s+", "", raw)
    token = _safe_fs_token(compact, 48)
    if not token:
        return f"lot-no-sku-{_short_lot_id(lot)}"
    if re.fullmatch(r"\d{1,8}", token):
        return f"lot-{token}"
    return token


def _title_slug(lot: Lot) -> str:
    return _safe_fs_token((lot.auction_title or "").strip(), 50)


def _lot_folder_name(lot: Lot, used_folders: set[str]) -> str:
    """
    One ZIP folder (or hero filename stem) per lot: SKU plus a short title so the archive
    is readable in WinRAR. Duplicate stems in one sale get ``-2``, ``-3``, etc.
    """
    sku = _sku_stem(lot)
    title = _title_slug(lot)
    candidate = f"{sku}-{title}" if title else sku
    if len(candidate) > _MAX_EXPORT_STEM:
        candidate = candidate[:_MAX_EXPORT_STEM].rstrip(".-")
    if candidate not in used_folders:
        used_folders.add(candidate)
        return candidate
    for n in range(2, 10000):
        alt = f"{candidate}-{n}"
        if len(alt) > _MAX_EXPORT_STEM:
            alt = f"{candidate[: _MAX_EXPORT_STEM - len(f'-{n}')].rstrip('.-')}-{n}"
        if alt not in used_folders:
            used_folders.add(alt)
            return alt
    return f"{candidate}-{_short_lot_id(lot)}"


def _lots_index_csv(rows: list[tuple[str, str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["sku", "title", "zip_entry"])
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


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
        lots_q = (
            db.query(Lot)
            .filter(
                Lot.sale_id == row.sale_id,
                Lot.account_id == row.account_id,
            )
            .order_by(Lot.created_at.asc())
        )
        filter_sid = getattr(row, "filter_ctseller_sale_id", None)
        if filter_sid is not None:
            lots_q = lots_q.filter(
                Lot.ctseller_item_id.isnot(None),
                Lot.ctseller_sale_id == int(filter_sid),
            )
        lots = lots_q.all()
        written = 0
        used_folders: set[str] = set()
        index_rows: list[tuple[str, str, str]] = []
        kind = normalize_sale_zip_kind(getattr(row, "kind", None))
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
                if kind == ZIP_KIND_HEROES:
                    hero = pick_lot_hero_photo(photos)
                    photos = [hero] if hero is not None else []
                lot_entry: str | None = None
                for pi, ph in enumerate(photos, start=1):
                    src = resolve_catalog_storage_path(ph.storage_path)
                    if not src.is_file():
                        continue
                    if kind == ZIP_KIND_HEROES:
                        suf = Path(ph.original_name.replace("\\", "/")).suffix or src.suffix or ".jpg"
                        base = folder + suf
                        if base in used_names:
                            base = f"{folder}-{pi}{suf}"
                        used_names.add(base)
                        arcname = base
                    else:
                        base = _arc_base(ph.original_name, pi, used_names)
                        arcname = f"{folder}/{base}"
                    zf.write(src, arcname=arcname)
                    if lot_entry is None:
                        lot_entry = folder if kind != ZIP_KIND_HEROES else arcname
                    written += 1
                    if written % 100 == 0:
                        row.photo_files_written = written
                        db.commit()
                if lot_entry is not None:
                    index_rows.append((lot.sku or "", lot.auction_title or "", lot_entry))
            if index_rows:
                zf.writestr("_lots.csv", _lots_index_csv(index_rows))
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
