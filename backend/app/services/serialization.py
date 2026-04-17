from __future__ import annotations

import json

from app.models import Lot
from app.schemas import LotOut


def lot_to_schema(row: Lot) -> LotOut:
    return LotOut(
        id=row.id,
        account_id=row.account_id,
        sku=row.sku,
        auction_title=row.auction_title,
        professional_description=row.professional_description,
        specification_lines=json.loads(row.specification_lines_json or "[]"),
        brands=json.loads(row.brands_json or "[]"),
        labels_and_tags=json.loads(row.labels_and_tags_json or "[]"),
        starting_price=row.starting_price,
        condition_text=row.condition_text,
        photo_count=row.photo_count,
        created_at=row.created_at,
    )

