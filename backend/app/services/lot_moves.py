"""Move catalog lots between Estate-EZ sales (photos/videos follow the lot)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Lot, Photo, Sale
from app.services.sale_covers import parse_cover_photo_ids, serialize_cover_photo_ids
from app.services.sku_sanity import recompute_sku_sanity_flags_for_sale


def drop_lot_photos_from_sale_covers(db: Session, sale: Sale, lot_id: str, account_id: str) -> bool:
    """Remove this lot's photos from the sale's four CT Seller cover slots."""
    current_ids = parse_cover_photo_ids(getattr(sale, "ctseller_cover_photo_ids", None))
    if not current_ids:
        return False
    lot_photo_ids = {
        str(pid)
        for (pid,) in db.query(Photo.id).filter(
            Photo.lot_id == lot_id,
            Photo.account_id == account_id,
        )
    }
    if not lot_photo_ids:
        return False
    kept = [pid for pid in current_ids if pid not in lot_photo_ids]
    if kept == current_ids:
        return False
    sale.ctseller_cover_photo_ids = serialize_cover_photo_ids(kept) if kept else None
    return True


def move_lots_to_sale(
    db: Session,
    *,
    account_id: str,
    lot_ids: list[str],
    destination: Sale,
) -> tuple[list[Lot], list[str]]:
    """Move lots onto ``destination``. Returns (moved lots, skipped already-there ids)."""
    wanted = list(dict.fromkeys(lid.strip() for lid in lot_ids if (lid or "").strip()))
    if not wanted:
        return [], []
    rows = (
        db.query(Lot)
        .filter(Lot.account_id == account_id, Lot.id.in_(wanted))
        .all()
    )
    by_id = {row.id: row for row in rows}
    missing = [lid for lid in wanted if lid not in by_id]
    if missing:
        raise LookupError(f"Lot not found: {missing[0]}")

    dest_id = destination.id
    moved: list[Lot] = []
    skipped: list[str] = []
    source_ids: set[str] = set()
    for lid in wanted:
        row = by_id[lid]
        if row.sale_id == dest_id:
            skipped.append(lid)
            continue
        source_ids.add(row.sale_id)
        source = db.query(Sale).filter(Sale.id == row.sale_id, Sale.account_id == account_id).first()
        if source is not None:
            drop_lot_photos_from_sale_covers(db, source, row.id, account_id)
        row.sale_id = dest_id
        moved.append(row)

    if not moved:
        return [], skipped

    db.flush()
    for sale_id in source_ids | {dest_id}:
        recompute_sku_sanity_flags_for_sale(db, sale_id, account_id)
    db.commit()
    for row in moved:
        db.refresh(row)
    return moved, skipped
