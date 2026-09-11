from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from collections import defaultdict

from app.catalog_storage import (
    catalog_file_listed_without_remote_stat,
    catalog_photo_file_on_disk,
    resolve_catalog_storage_path,
)
from app.core.lotting import listing_quality_issue
from app.models import Lot, LotVideo, Photo
from app.schemas import LotOut, LotPhotoHealthRow, LotPhotoOut, LotVideoOut
from app.services.audience_hunt import audience_payload_for_lot


def _json_str_list(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(x).strip() for x in data if str(x).strip()]


def sale_lot_photo_counts(db: Session, sale_id: str, account_id: str) -> list[int]:
    return [
        c[0]
        for c in db.query(Lot.photo_count)
        .filter(Lot.sale_id == sale_id, Lot.account_id == account_id)
        .all()
    ]


def photo_count_outlier_by_lot_id(rows: list[Lot]) -> dict[str, bool]:
    """Within each sale, mark at most one lot as the heavy photo-count outlier (mis-merge hint)."""
    by_sale: dict[str, list[Lot]] = {}
    for r in rows:
        by_sale.setdefault(r.sale_id, []).append(r)
    out: dict[str, bool] = {}
    for lst in by_sale.values():
        counts = [x.photo_count for x in lst]
        for x in lst:
            out[x.id] = photo_count_is_sale_outlier(x.photo_count, counts)
    return out


def photo_count_is_sale_outlier(photo_count: int, all_counts: list[int]) -> bool:
    """
    Flag the single heaviest lot in a sale when it has far more linked photos than the rest.

    Catches mis-merged jobs (e.g. one listing absorbing another lot's frames) without a DB migration for photos.
    """
    if photo_count < 12 or not all_counts:
        return False
    desc = sorted(all_counts, reverse=True)
    top = desc[0]
    second = desc[1] if len(desc) > 1 else 0
    if photo_count != top:
        return False
    return top >= second + 8


def lot_video_out(video: LotVideo, *, lot_id: str | None = None) -> LotVideoOut:
    lid = lot_id or video.lot_id or ""
    return LotVideoOut(
        id=video.id,
        lot_id=lid,
        original_name=video.original_name,
        created_at=video.created_at,
        sort_order=int(video.sort_order or 0),
        content_type=(video.content_type or "video/mp4"),
        byte_size=int(video.byte_size or 0),
        file_on_disk=catalog_file_listed_without_remote_stat(video.storage_path),
    )


def lot_photo_out(photo: Photo, *, lot_id: str | None = None) -> LotPhotoOut:
    lid = lot_id or photo.lot_id or ""
    return LotPhotoOut(
        id=photo.id,
        lot_id=lid,
        original_name=photo.original_name,
        created_at=photo.created_at,
        sort_order=int(photo.sort_order or 0),
        file_on_disk=catalog_file_listed_without_remote_stat(photo.storage_path),
        photo_role=(str(photo.photo_role).strip() or None) if getattr(photo, "photo_role", None) else None,
        distinct_item_id=(str(photo.distinct_item_id).strip() or None)
        if getattr(photo, "distinct_item_id", None)
        else None,
        is_primary_front=bool(getattr(photo, "is_primary_front", False)),
        shows_whole_lot=bool(getattr(photo, "shows_whole_lot", False)),
        shows_measuring_scale=bool(getattr(photo, "shows_measuring_scale", False)),
        label_legibility=(str(photo.label_legibility).strip() or None)
        if getattr(photo, "label_legibility", None)
        else None,
    )


def sale_lots_with_missing_photo_files(
    db: Session, account_id: str, sale_id: str
) -> list[LotPhotoHealthRow]:
    """One row per lot that has at least one linked photo record with no file on disk."""
    lots = (
        db.query(Lot)
        .filter(Lot.sale_id == sale_id, Lot.account_id == account_id)
        .order_by(Lot.created_at.asc(), Lot.id.asc())
        .all()
    )
    if not lots:
        return []
    lot_meta = [
        (lot.id, lot.sku, lot.auction_title or "", int(lot.photo_count or 0)) for lot in lots
    ]
    lot_ids = [row[0] for row in lot_meta]
    photo_rows = (
        db.query(Photo.lot_id, Photo.storage_path)
        .filter(Photo.account_id == account_id, Photo.lot_id.in_(lot_ids))
        .all()
    )
    return photo_health_from_loaded(lot_meta, list(photo_rows))


def photo_health_from_loaded(
    lot_meta: list[tuple[str, str | None, str, int]],
    photo_rows: list[tuple[str | None, str | None]],
) -> list[LotPhotoHealthRow]:
    """Stat files after the DB session is closed (R2 HEAD must not pin QueuePool)."""
    lot_by_id = {lot_id: (sku, title, photo_count) for lot_id, sku, title, photo_count in lot_meta}
    linked: dict[str, int] = defaultdict(int)
    missing: dict[str, int] = defaultdict(int)
    for lid, path in photo_rows:
        if not lid:
            continue
        linked[lid] += 1
        if not catalog_photo_file_on_disk(path):
            missing[lid] += 1
    out: list[LotPhotoHealthRow] = []
    for lid, miss in missing.items():
        if miss <= 0:
            continue
        meta = lot_by_id.get(lid)
        if not meta:
            continue
        sku, title, photo_count = meta
        out.append(
            LotPhotoHealthRow(
                lot_id=lid,
                sku=sku,
                auction_title=title,
                photo_count=photo_count,
                linked_photo_count=linked.get(lid, 0),
                missing_count=miss,
            )
        )
    out.sort(key=lambda r: (r.sku or "", r.lot_id))
    return out


def upload_folder_has_linked_photos(db: Session, account_id: str, folder: Path) -> bool:
    """
    True when any catalog ``Photo.storage_path`` still lives under ``folder``.

    Job delete must not ``rmtree`` upload batches that lots still reference — otherwise
    dashboard thumbs fail while ``photo_count`` stays positive.
    """
    root = folder.resolve()
    for (storage_path,) in db.query(Photo.storage_path).filter(Photo.account_id == account_id):
        if not storage_path:
            continue
        try:
            resolve_catalog_storage_path(storage_path).relative_to(root)
            return True
        except (ValueError, OSError):
            continue
    return False


def lot_ids_with_linked_photos(db: Session, account_id: str, lot_ids: list[str]) -> set[str]:
    if not lot_ids:
        return set()
    stmt = (
        select(Photo.lot_id)
        .where(
            Photo.lot_id.in_(lot_ids),
            Photo.account_id == account_id,
        )
        .distinct()
    )
    return {lid for lid in db.scalars(stmt).all() if lid is not None}


def lot_to_schema(
    row: Lot,
    *,
    has_thumbnail: bool = False,
    many_photos_review: bool = False,
) -> LotOut:
    spec_lines = json.loads(row.specification_lines_json or "[]")
    review_reason = listing_quality_issue(
        row.auction_title, row.professional_description, spec_lines
    )
    return LotOut(
        id=row.id,
        account_id=row.account_id,
        user_id=row.user_id,
        sale_id=row.sale_id,
        sku=row.sku,
        auction_title=row.auction_title,
        professional_description=row.professional_description,
        specification_lines=spec_lines,
        brands=json.loads(row.brands_json or "[]"),
        labels_and_tags=json.loads(row.labels_and_tags_json or "[]"),
        search_tags=_json_str_list(getattr(row, "search_tags_json", None)),
        ct_category=row.ct_category,
        ct_subcategory=row.ct_subcategory,
        is_completed=bool(row.is_completed),
        completed_at=row.completed_at,
        starting_price=row.starting_price,
        reserve_price=getattr(row, "reserve_price", None),
        condition_text=row.condition_text,
        photo_count=row.photo_count,
        has_thumbnail=has_thumbnail,
        created_at=row.created_at,
        sku_needs_review=bool(row.sku_needs_review),
        many_photos_review=many_photos_review,
        sku_duplicate_in_sale=bool(getattr(row, "sku_duplicate_in_sale", False)),
        sku_text_mismatch=bool(getattr(row, "sku_text_mismatch", False)),
        listing_needs_review=review_reason is not None,
        listing_review_reason=review_reason,
        ctseller_item_id=getattr(row, "ctseller_item_id", None),
        ctseller_posted_at=getattr(row, "ctseller_posted_at", None),
        ctseller_sale_id=getattr(row, "ctseller_sale_id", None),
        ctseller_item_status=getattr(row, "ctseller_item_status", None),
        ctseller_sold_price=getattr(row, "ctseller_sold_price", None),
        listing_channel=(getattr(row, "listing_channel", None) or "ctseller"),
        audience_hold=bool(getattr(row, "audience_hold", False)),
        audience=audience_payload_for_lot(row),
    )

