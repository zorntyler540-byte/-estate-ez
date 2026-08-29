"""Map catalog photo filenames back to the phone draft that uploaded them."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Lot, Photo

# ``mobile-<uuid>-NN.jpg`` as written by mobile/lib/queue/sync.ts
_MOBILE_DRAFT_NAME = re.compile(
    r"^mobile-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-\d+\.",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CatalogedMobileDraftLot:
    draft_id: str
    lot_id: str
    sku: str | None
    photo_count: int
    auction_title: str


def mobile_draft_id_from_original_name(name: str | None) -> str | None:
    if not name:
        return None
    m = _MOBILE_DRAFT_NAME.match(str(name).strip())
    return m.group(1).lower() if m else None


def cataloged_mobile_draft_lots(
    db: Session, account_id: str, sale_id: str
) -> list[CatalogedMobileDraftLot]:
    """Phone drafts that already have catalog photos on this sale, with the lot they became."""
    rows = (
        db.query(Photo.lot_id, Photo.original_name, Lot.sku, Lot.photo_count, Lot.auction_title)
        .join(Lot, Photo.lot_id == Lot.id)
        .filter(
            Lot.account_id == account_id,
            Lot.sale_id == sale_id,
            Photo.original_name.isnot(None),
        )
        .all()
    )
    by_draft: dict[str, CatalogedMobileDraftLot] = {}
    for lot_id, original_name, sku, photo_count, auction_title in rows:
        draft_id = mobile_draft_id_from_original_name(original_name)
        if not draft_id or draft_id in by_draft:
            continue
        by_draft[draft_id] = CatalogedMobileDraftLot(
            draft_id=draft_id,
            lot_id=str(lot_id),
            sku=(str(sku).strip() if sku else None) or None,
            photo_count=int(photo_count or 0),
            auction_title=str(auction_title or "").strip(),
        )
    return sorted(by_draft.values(), key=lambda row: row.draft_id)


def cataloged_mobile_draft_ids(db: Session, account_id: str, sale_id: str) -> list[str]:
    """Draft ids that already have at least one catalog photo on this sale."""
    return [row.draft_id for row in cataloged_mobile_draft_lots(db, account_id, sale_id)]
