"""Pick and persist the four CT Seller sale cover photos from catalog lots."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.catalog_storage import resolve_catalog_storage_path
from app.core.lotting import sku_digits
from app.models import Lot, Photo, Sale
from app.services.photo_order import listing_photo_sort_key
from app.services.photo_roles import (
    is_hero_eligible_photo_role,
    is_sku_tape_photo_role,
    normalize_catalog_photo_role,
)
from app.services.sale_zip_export import pick_lot_hero_photo

logger = logging.getLogger(__name__)

SALE_COVER_SLOT_COUNT = 4
CANDIDATE_EXTRAS_PER_LOT = 3
# Extra frames after one photo per lot. Never used to drop a lot from the picker.
CANDIDATE_MAX_EXTRAS = 160


def parse_cover_photo_ids(raw: str | None) -> list[str]:
    """Return up to four unique catalog photo ids from a JSON list."""
    if not (raw or "").strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        pid = str(item or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
        if len(out) >= SALE_COVER_SLOT_COUNT:
            break
    return out


def serialize_cover_photo_ids(photo_ids: Iterable[str]) -> str:
    return json.dumps(parse_cover_photo_ids(json.dumps(list(photo_ids))), ensure_ascii=True)


def lot_cover_sort_key(lot: Lot) -> tuple:
    digits = sku_digits(lot.sku)
    try:
        n = int(digits) if digits else 10**9
    except ValueError:
        n = 10**9
    created = lot.created_at or datetime.min
    return (n, (lot.sku or "").lower(), created, lot.id)


def listing_cover_photos(photos: Iterable[Photo]) -> list[Photo]:
    eligible = [
        p
        for p in photos
        if is_hero_eligible_photo_role(getattr(p, "photo_role", None))
        and not is_sku_tape_photo_role(getattr(p, "photo_role", None))
    ]
    return sorted(eligible, key=listing_photo_sort_key)


def pick_lot_cover_photo(lot_photos: list[Photo]) -> Photo | None:
    """Listing hero from cover-eligible frames only — never SKU tape or unusable."""
    eligible = listing_cover_photos(lot_photos)
    if not eligible:
        return None
    return pick_lot_hero_photo(eligible)


def photos_by_lot_id(photos: Iterable[Photo]) -> dict[str, list[Photo]]:
    grouped: dict[str, list[Photo]] = {}
    for photo in photos:
        lid = getattr(photo, "lot_id", None)
        if not lid:
            continue
        grouped.setdefault(str(lid), []).append(photo)
    return grouped


def suggest_cover_photos(lots: list[Lot], photos: list[Photo]) -> list[Photo]:
    """One lot hero first (SKU order), then extra listing frames until four unique photos."""
    by_lot = photos_by_lot_id(photos)
    ordered_lots = sorted(lots, key=lot_cover_sort_key)
    picked: list[Photo] = []
    seen: set[str] = set()

    def _add(photo: Photo | None) -> None:
        if photo is None or photo.id in seen:
            return
        seen.add(photo.id)
        picked.append(photo)

    for lot in ordered_lots:
        _add(pick_lot_cover_photo(by_lot.get(lot.id, [])))
        if len(picked) >= SALE_COVER_SLOT_COUNT:
            return picked[:SALE_COVER_SLOT_COUNT]

    for lot in ordered_lots:
        extras = listing_cover_photos(by_lot.get(lot.id, []))
        for photo in extras:
            _add(photo)
            if len(picked) >= SALE_COVER_SLOT_COUNT:
                return picked[:SALE_COVER_SLOT_COUNT]
    return picked[:SALE_COVER_SLOT_COUNT]


def cover_candidate_photos(
    lots: list[Lot],
    photos: list[Photo],
    *,
    extras_per_lot: int = CANDIDATE_EXTRAS_PER_LOT,
    max_extras: int = CANDIDATE_MAX_EXTRAS,
) -> list[Photo]:
    """One photo from every lot, then a few extra listing frames for cover choice.

    Extra frames can be capped. The first photo from each lot is never dropped.
    """
    by_lot = photos_by_lot_id(photos)
    suggested_ids = {p.id for p in suggest_cover_photos(lots, photos)}
    out: list[Photo] = []
    seen: set[str] = set()
    extras_added = 0
    extras_budget = max(0, max_extras)
    ordered_lots = sorted(lots, key=lot_cover_sort_key)

    for lot in ordered_lots:
        first = pick_lot_cover_photo(by_lot.get(lot.id, []))
        if first is None or first.id in seen:
            continue
        seen.add(first.id)
        out.append(first)

    for lot in ordered_lots:
        lot_photos = by_lot.get(lot.id, [])
        extras = [
            p
            for p in listing_cover_photos(lot_photos)
            if p.id not in seen
        ]
        extra_cap = max(0, extras_per_lot)
        chosen_extras = extras[:extra_cap]
        for photo in lot_photos:
            if photo.id in suggested_ids and photo.id not in seen and photo not in chosen_extras:
                if is_hero_eligible_photo_role(getattr(photo, "photo_role", None)):
                    chosen_extras.append(photo)
        for photo in chosen_extras:
            if photo.id in seen:
                continue
            if extras_added >= extras_budget:
                return out
            seen.add(photo.id)
            out.append(photo)
            extras_added += 1
    return out


def load_sale_lots_and_photos(db: Session, sale: Sale) -> tuple[list[Lot], list[Photo]]:
    lots = (
        db.query(Lot)
        .filter(Lot.sale_id == sale.id, Lot.account_id == sale.account_id)
        .all()
    )
    if not lots:
        return [], []
    photos = (
        db.query(Photo)
        .filter(
            Photo.account_id == sale.account_id,
            Photo.lot_id.in_([lot.id for lot in lots]),
        )
        .all()
    )
    return lots, photos


def load_cover_photos_by_ids(
    db: Session, account_id: str, photo_ids: Iterable[str]
) -> tuple[list[Lot], list[Photo], dict[str, Sale]]:
    """Resolve selected cover ids from the account — including lots on other sales."""
    wanted = parse_cover_photo_ids(json.dumps(list(photo_ids)))
    if not wanted:
        return [], [], {}
    photos = (
        db.query(Photo)
        .filter(Photo.account_id == account_id, Photo.id.in_(wanted))
        .all()
    )
    lot_ids = {photo.lot_id for photo in photos if photo.lot_id}
    lots = (
        db.query(Lot).filter(Lot.account_id == account_id, Lot.id.in_(lot_ids)).all()
        if lot_ids
        else []
    )
    sale_ids = {lot.sale_id for lot in lots if lot.sale_id}
    sales = (
        db.query(Sale).filter(Sale.account_id == account_id, Sale.id.in_(sale_ids)).all()
        if sale_ids
        else []
    )
    return lots, photos, {sale.id: sale for sale in sales}


def photos_for_ids(photos: Iterable[Photo], photo_ids: list[str]) -> list[Photo]:
    by_id = {p.id: p for p in photos}
    return [by_id[pid] for pid in photo_ids if pid in by_id]


def validate_cover_photo_ids(
    *,
    photo_ids: list[str],
    catalog_photos: Iterable[Photo] | None = None,
    sale_photos: Iterable[Photo] | None = None,
    require_four: bool,
) -> list[str]:
    photos = list(catalog_photos if catalog_photos is not None else (sale_photos or []))
    wanted = parse_cover_photo_ids(json.dumps(photo_ids))
    if require_four and len(wanted) != SALE_COVER_SLOT_COUNT:
        raise ValueError("Pick exactly 4 different catalog photos (1 primary + 3 secondary)")
    by_id = {p.id: p for p in photos}
    missing = [pid for pid in wanted if pid not in by_id]
    if missing:
        raise ValueError("One or more cover photos are not in your catalog")
    tape_or_bad = [
        pid
        for pid in wanted
        if normalize_catalog_photo_role(getattr(by_id[pid], "photo_role", None)) in {"tape", "unusable"}
    ]
    if tape_or_bad:
        raise ValueError("SKU-tape and unusable frames cannot be sale covers")
    return wanted


def read_cover_image_bytes(photo: Photo) -> bytes:
    path = resolve_catalog_storage_path(photo.storage_path)
    if path is None or not path.is_file():
        raise FileNotFoundError(f"Cover photo file is missing ({photo.id})")
    return path.read_bytes()


def persist_cover_photo_ids(sale: Sale, photo_ids: list[str]) -> list[str]:
    cleaned = parse_cover_photo_ids(json.dumps(photo_ids))
    sale.ctseller_cover_photo_ids = serialize_cover_photo_ids(cleaned) if cleaned else None
    return cleaned


def candidate_is_hero(photo: Photo, lot_photos: list[Photo]) -> bool:
    hero = pick_lot_hero_photo(lot_photos)
    return hero is not None and hero.id == photo.id


def cover_thumb_url(storage_path: str | None) -> str | None:
    """Presign the original. Never HEAD/generate thumbs on the picker request path."""
    try:
        from app import object_storage
        from app.catalog_storage import signed_catalog_url

        if not object_storage.r2_configured():
            return None
        return signed_catalog_url(
            storage_path,
            max_px=None,
            expires_seconds=3600,
            generate_if_missing=False,
        )
    except Exception:  # noqa: BLE001 — thumbs are optional for the picker
        return None


def lot_title_map(lots: Iterable[Lot]) -> dict[str, Lot]:
    return {lot.id: lot for lot in lots}


def photo_card_meta(
    photo: Photo,
    lots_by_id: dict[str, Lot],
    *,
    sales_by_id: dict[str, Sale] | None = None,
    current_sale_id: str | None = None,
) -> dict[str, Any]:
    lot = lots_by_id.get(photo.lot_id or "")
    sale = None
    if lot is not None and sales_by_id:
        sale = sales_by_id.get(lot.sale_id)
    from_other = bool(current_sale_id and lot is not None and lot.sale_id != current_sale_id)
    return {
        "photo_id": photo.id,
        "lot_id": photo.lot_id,
        "sku": (lot.sku if lot else None),
        "auction_title": (lot.auction_title if lot else "") or "",
        "photo_role": normalize_catalog_photo_role(getattr(photo, "photo_role", None)),
        "thumb_url": cover_thumb_url(photo.storage_path),
        "sale_id": (lot.sale_id if lot else None),
        "sale_name": (sale.name if sale else "") or "",
        "from_other_sale": from_other,
    }
