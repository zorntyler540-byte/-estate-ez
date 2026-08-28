"""Persisted display order for catalog photos (``Photo.sort_order`` per lot)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import Photo
from app.services.photo_roles import normalize_catalog_photo_role

# Lower rank = earlier in the listing gallery / CT Seller export.
# Used when a lot has no listing-meta columns (legacy lots).
# tape is last because dedicated SKU-tape frames are already dropped from CT Seller posts.
LISTING_ROLE_RANK: dict[str | None, int] = {
    "hero": 0,
    "item_front": 1,
    "angle_only": 2,
    None: 3,
    "unusable": 4,
    "tape": 5,
}


def natural_photo_name_key(name: str | None) -> list:
    """Sort key that treats digit runs numerically so ``...-2`` sorts before ``...-10``.

    Mobile capture names are ``mobile-<draft>-NN.jpg`` where NN is the capture index, so sorting by
    this key restores the order photos were taken for lots cataloged before the capture-order fix.
    """
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", name or "")]


def _capture_index(photo: Any) -> int:
    current = getattr(photo, "sort_order", None)
    try:
        return int(current) if current is not None else 0
    except (TypeError, ValueError):
        return 0


def listing_photo_sort_key(photo: Any) -> tuple:
    """Legacy key: group → items → details → leftover → tape (capture order within a role)."""
    role = normalize_catalog_photo_role(getattr(photo, "photo_role", None))
    rank = LISTING_ROLE_RANK.get(role, LISTING_ROLE_RANK[None])
    return (rank, _capture_index(photo), natural_photo_name_key(getattr(photo, "original_name", None)))


def _photos_have_listing_meta(photos: list[Any]) -> bool:
    for photo in photos:
        if str(getattr(photo, "distinct_item_id", None) or "").strip():
            return True
        if getattr(photo, "shows_measuring_scale", False):
            return True
        if getattr(photo, "shows_whole_lot", False):
            return True
    return False


def _item_chapter_rank(photos: list[Any]) -> dict[str, int]:
    """Order distinct items by their primary-front capture (else first photo of that item)."""
    primary_cap: dict[str, int] = {}
    first_cap: dict[str, int] = {}
    for photo in photos:
        item_id = str(getattr(photo, "distinct_item_id", None) or "").strip()
        if not item_id:
            continue
        cap = _capture_index(photo)
        first_cap[item_id] = min(first_cap.get(item_id, cap), cap)
        if getattr(photo, "is_primary_front", False):
            primary_cap[item_id] = min(primary_cap.get(item_id, cap), cap)
    return {item_id: primary_cap.get(item_id, first_cap[item_id]) for item_id in first_cap}


def _chapter_listing_sort_key(photo: Any, item_rank: dict[str, int]) -> tuple:
    """Shopper order: true group shots, then each item as a chapter, labels, measurements, tape."""
    role = normalize_catalog_photo_role(getattr(photo, "photo_role", None))
    cap = _capture_index(photo)
    name = natural_photo_name_key(getattr(photo, "original_name", None))
    measure = bool(getattr(photo, "shows_measuring_scale", False))
    whole = bool(getattr(photo, "shows_whole_lot", False))
    item_id = str(getattr(photo, "distinct_item_id", None) or "").strip()
    primary = bool(getattr(photo, "is_primary_front", False))
    label = str(getattr(photo, "label_legibility", None) or "").strip().lower()

    if role == "tape":
        return (6, 0, 0, cap, name)
    if role == "unusable":
        return (5, 0, 0, cap, name)
    if measure:
        return (4, 0, 0, cap, name)

    is_mark = label in {"clear", "partial"} and not primary and role != "hero" and not whole
    if is_mark:
        return (3, 0, 0, cap, name)

    if role == "hero" or whole:
        return (0, 0, 0, cap, name)

    if item_id:
        within = 0 if primary else 1
        return (1, item_rank.get(item_id, cap), within, cap, name)

    return (2, 0, 0, cap, name)


def ordered_photos_for_listing(photos: Iterable[Any]) -> list[Any]:
    """Return photos in listing order without mutating them."""
    rows = list(photos)
    if not _photos_have_listing_meta(rows):
        return sorted(rows, key=listing_photo_sort_key)
    item_rank = _item_chapter_rank(rows)
    return sorted(rows, key=lambda photo: _chapter_listing_sort_key(photo, item_rank))


def reorder_photos_by_capture_name(db: Session, lot_id: str, account_id: str) -> bool:
    """Re-sort a lot's photos into capture order (by natural filename). Returns True if anything changed."""
    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == account_id)
        .all()
    )
    if not photos:
        return False
    ordered = sorted(photos, key=lambda p: natural_photo_name_key(p.original_name))
    changed = False
    for i, p in enumerate(ordered):
        if p.sort_order != i:
            p.sort_order = i
            changed = True
    return changed


def reorder_photos_by_filenames(
    db: Session, lot_id: str, account_id: str, filenames: list[str]
) -> bool:
    """Apply an explicit filename order (AI gallery plan). Unknown names are ignored; leftovers stay last."""
    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == account_id)
        .all()
    )
    if not photos or not filenames:
        return False
    by_key: dict[str, Photo] = {}
    for photo in photos:
        name = str(photo.original_name or "")
        by_key.setdefault(name.lower(), photo)
        by_key.setdefault(Path(name).name.lower(), photo)
    ordered: list[Photo] = []
    seen: set[str] = set()
    for raw in filenames:
        token = str(raw or "").strip().lower()
        if not token:
            continue
        photo = by_key.get(token) or by_key.get(Path(token).name.lower())
        if photo is None or photo.id in seen:
            continue
        ordered.append(photo)
        seen.add(photo.id)
    for photo in photos:
        if photo.id not in seen:
            ordered.append(photo)
    changed = False
    for i, photo in enumerate(ordered):
        if photo.sort_order != i:
            photo.sort_order = i
            changed = True
    return changed


def reorder_photos_for_listing(db: Session, lot_id: str, account_id: str) -> bool:
    """Re-sort a lot's photos into listing order. Returns True if anything changed.

    New lots use group shots, then per-item chapters, then labels, then measurements.
    Lots without listing-meta columns keep the older role-bucket sort.
    """
    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == account_id)
        .all()
    )
    if not photos:
        return False
    ordered = ordered_photos_for_listing(photos)
    changed = False
    for i, p in enumerate(ordered):
        if p.sort_order != i:
            p.sort_order = i
            changed = True
    return changed


def renormalize_photo_sort_orders(db: Session, lot_id: str, account_id: str) -> None:
    """Set ``sort_order`` to 0..n-1 following current sort_order then created_at."""
    rows = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
        .all()
    )
    for i, p in enumerate(rows):
        if p.sort_order != i:
            p.sort_order = i
