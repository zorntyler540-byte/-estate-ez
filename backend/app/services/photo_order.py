"""Persisted display order for catalog photos (``Photo.sort_order`` per lot)."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import Photo


def natural_photo_name_key(name: str | None) -> list:
    """Sort key that treats digit runs numerically so ``...-2`` sorts before ``...-10``.

    Mobile capture names are ``mobile-<draft>-NN.jpg`` where NN is the capture index, so sorting by
    this key restores the order photos were taken for lots cataloged before the capture-order fix.
    """
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", name or "")]


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
