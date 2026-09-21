"""Create / rename Estate-EZ sales to match open CT Seller auctions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from sqlalchemy.orm import Session

from app.models import Sale


class RemoteAuction(Protocol):
    id: int
    title: str


@dataclass(frozen=True)
class CtsellerCatalogSyncResult:
    created: int
    renamed: int
    already_mapped: int


def catalog_sale_name_for_remote(title: str, remote_id: int) -> str:
    name = (title or "").strip() or f"CT Seller #{int(remote_id)}"
    return name[:255]


def apply_ctseller_catalog_sale_sync(
    db: Session,
    *,
    account_id: str,
    user_id: str,
    remote_sales: Iterable[RemoteAuction],
) -> CtsellerCatalogSyncResult:
    """
    Add a catalog sale for each open CT Seller auction that is not mapped yet.
    Refresh the name on mapped, still-open catalog sales when CT Bids retitles.

    Does not close, delete, or invent lots. Manual Estate-EZ sales without a mapping stay put.
    """
    existing = (
        db.query(Sale)
        .filter(Sale.account_id == account_id)
        .all()
    )
    by_remote: dict[int, Sale] = {}
    for row in existing:
        raw = getattr(row, "ctseller_sale_id", None)
        try:
            rid = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            rid = 0
        if rid > 0 and rid not in by_remote:
            by_remote[rid] = row

    created = 0
    renamed = 0
    already = 0
    seen: set[int] = set()
    for remote in remote_sales:
        try:
            rid = int(remote.id)
        except (TypeError, ValueError):
            continue
        if rid <= 0 or rid in seen:
            continue
        seen.add(rid)
        want = catalog_sale_name_for_remote(getattr(remote, "title", "") or "", rid)
        row = by_remote.get(rid)
        if row is None:
            row = Sale(
                account_id=account_id,
                user_id=user_id,
                name=want,
                ctseller_sale_id=rid,
            )
            db.add(row)
            by_remote[rid] = row
            created += 1
            continue
        already += 1
        if row.closed_at is not None:
            continue
        if want and (row.name or "").strip() != want:
            row.name = want
            renamed += 1
    return CtsellerCatalogSyncResult(created=created, renamed=renamed, already_mapped=already)
