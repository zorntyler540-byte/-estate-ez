"""Persisted display order for catalog photos (``Photo.sort_order`` per lot)."""

from __future__ import annotations

import re
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import Photo
from app.services.photo_roles import normalize_catalog_photo_role

# Lower rank = earlier in the listing gallery / CT Seller export.
# Within a rank, capture order (current sort_order, then filename) is kept.
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


def listing_photo_sort_key(photo: Any) -> tuple:
    """Sort key for a shopper-friendly listing: group → items → details → leftover → tape."""
    role = normalize_catalog_photo_role(getattr(photo, "photo_role", None))
    rank = LISTING_ROLE_RANK.get(role, LISTING_ROLE_RANK[None])
    current = getattr(photo, "sort_order", None)
    try:
        current_i = int(current) if current is not None else 0
    except (TypeError, ValueError):
        current_i = 0
    return (rank, current_i, natural_photo_name_key(getattr(photo, "original_name", None)))


def ordered_photos_for_listing(photos: Iterable[Any]) -> list[Any]:
    """Return photos in listing order without mutating them."""
    return sorted(list(photos), key=listing_photo_sort_key)


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


def reorder_photos_for_listing(db: Session, lot_id: str, account_id: str) -> bool:
    """Re-sort a lot's photos into listing order (role groups). Returns True if anything changed.

    Uses persisted ``photo_role`` from catalog triage. Lots without roles keep their current order.
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
