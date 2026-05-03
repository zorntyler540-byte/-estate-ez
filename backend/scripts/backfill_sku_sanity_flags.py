"""
One-off backfill: recompute ``sku_duplicate_in_sale`` and ``sku_text_mismatch`` for every lot
(after Alembic migration ``0014_lot_sku_sanity_flags``). Safe to re-run.

Usage (from ``backend/`` with ``DATABASE_URL`` set, e.g. Railway shell):

  python scripts/backfill_sku_sanity_flags.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Lot
from app.services.sku_sanity import recompute_sku_sanity_flags_for_sale


def main() -> None:
    with SessionLocal() as db:
        sale_keys = db.execute(select(Lot.sale_id, Lot.account_id).distinct()).all()
        for sale_id, account_id in sale_keys:
            recompute_sku_sanity_flags_for_sale(db, sale_id, account_id)
        db.commit()
    print(f"Updated SKU sanity flags for {len(sale_keys)} sale/account pair(s).")


if __name__ == "__main__":
    main()
