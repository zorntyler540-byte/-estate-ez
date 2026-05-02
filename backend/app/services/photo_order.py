"""Persisted display order for catalog photos (``Photo.sort_order`` per lot)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Photo


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
