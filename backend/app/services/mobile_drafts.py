"""Map catalog photo filenames back to the phone draft that uploaded them."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import Lot, Photo

# ``mobile-<uuid>-NN.jpg`` as written by mobile/lib/queue/sync.ts
_MOBILE_DRAFT_NAME = re.compile(
    r"^mobile-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-\d+\.",
    re.IGNORECASE,
)


def mobile_draft_id_from_original_name(name: str | None) -> str | None:
    if not name:
        return None
    m = _MOBILE_DRAFT_NAME.match(str(name).strip())
    return m.group(1).lower() if m else None


def cataloged_mobile_draft_ids(db: Session, account_id: str, sale_id: str) -> list[str]:
    """Draft ids that already have at least one catalog photo on this sale."""
    names = (
        db.query(Photo.original_name)
        .join(Lot, Photo.lot_id == Lot.id)
        .filter(
            Lot.account_id == account_id,
            Lot.sale_id == sale_id,
            Photo.original_name.isnot(None),
        )
        .all()
    )
    found: set[str] = set()
    for (original_name,) in names:
        draft_id = mobile_draft_id_from_original_name(original_name)
        if draft_id:
            found.add(draft_id)
    return sorted(found)
