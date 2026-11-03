"""Map catalog photo filenames (and job ``client_draft_id``) back to the phone draft."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import JobStatus, Lot, LotVideo, Photo, ProcessingJob

# ``mobile-<uuid>-NN.jpg`` (stills) and ``mobile-<uuid>-vid-NN.mp4`` (clips).
_MOBILE_DRAFT_NAME = re.compile(
    r"^mobile-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-(?:vid-)?\d+\.",
    re.IGNORECASE,
)

# Single-lot jobs without a unique SKU: the new lot is created around the job clock.
_SINGLE_LOT_SKEW = timedelta(minutes=2)


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


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _lot_row_to_draft(
    draft_id: str, lot_id: str, sku: str | None, photo_count: int | None, auction_title: str | None
) -> CatalogedMobileDraftLot:
    return CatalogedMobileDraftLot(
        draft_id=draft_id,
        lot_id=str(lot_id),
        sku=(str(sku).strip() if sku else None) or None,
        photo_count=int(photo_count or 0),
        auction_title=str(auction_title or "").strip(),
    )


def _map_media_name(
    by_draft: dict[str, CatalogedMobileDraftLot],
    lots_by_id: dict[str, tuple],
    lot_id: str,
    original_name: str | None,
    sku: str | None,
    photo_count: int | None,
    auction_title: str | None,
) -> None:
    draft_id = mobile_draft_id_from_original_name(original_name)
    if not draft_id or draft_id in by_draft:
        return
    if str(lot_id) not in lots_by_id:
        return
    by_draft[draft_id] = _lot_row_to_draft(draft_id, str(lot_id), sku, photo_count, auction_title)


def cataloged_mobile_draft_lots(
    db: Session, account_id: str, sale_id: str
) -> list[CatalogedMobileDraftLot]:
    """Phone drafts that already have catalog photos on this sale, with the lot they became.

    Prefer ``ProcessingJob.client_draft_id`` (new app builds). Fall back to
    ``mobile-<draft>-NN.jpg`` / ``mobile-<draft>-vid-NN`` names. Video-only lots
    (or lots whose stills lost the mobile filename) still map via the clip name.
    A completed single-lot job with no unique SKU maps to the one new lot created
    around that job — Queue used to stay on "Cataloging…" when staff left SKU blank.
    """
    by_draft: dict[str, CatalogedMobileDraftLot] = {}

    lots = (
        db.query(Lot.id, Lot.sku, Lot.photo_count, Lot.auction_title, Lot.created_at)
        .filter(Lot.account_id == account_id, Lot.sale_id == sale_id)
        .all()
    )
    lots_by_id = {str(row[0]): row for row in lots}
    sku_to_lot: dict[str, tuple] = {}
    sku_counts: dict[str, int] = {}
    for row in lots:
        sku = (str(row[1]).strip().lower() if row[1] else "")
        if not sku:
            continue
        sku_counts[sku] = sku_counts.get(sku, 0) + 1
        sku_to_lot[sku] = row

    completed_jobs = (
        db.query(
            ProcessingJob.client_draft_id,
            ProcessingJob.single_lot_manual_sku,
            ProcessingJob.single_lot_mode,
            ProcessingJob.created_at,
        )
        .filter(
            ProcessingJob.account_id == account_id,
            ProcessingJob.sale_id == sale_id,
            ProcessingJob.status == JobStatus.completed,
            ProcessingJob.client_draft_id.isnot(None),
        )
        .all()
    )
    unmatched_jobs: list[tuple[str, datetime | None]] = []
    for client_draft_id, job_sku, single_lot_mode, created_at in completed_jobs:
        draft_id = (client_draft_id or "").strip().lower()
        if not draft_id or draft_id in by_draft:
            continue
        sku_key = (str(job_sku).strip().lower() if job_sku else "")
        lot_row = sku_to_lot.get(sku_key) if sku_key and sku_counts.get(sku_key) == 1 else None
        if lot_row is None:
            if single_lot_mode:
                unmatched_jobs.append((draft_id, _aware(created_at)))
            continue
        by_draft[draft_id] = _lot_row_to_draft(
            draft_id, str(lot_row[0]), lot_row[1], lot_row[2], lot_row[3]
        )

    photo_rows = (
        db.query(Photo.lot_id, Photo.original_name, Lot.sku, Lot.photo_count, Lot.auction_title)
        .join(Lot, Photo.lot_id == Lot.id)
        .filter(
            Lot.account_id == account_id,
            Lot.sale_id == sale_id,
            Photo.original_name.isnot(None),
        )
        .all()
    )
    for lot_id, original_name, sku, photo_count, auction_title in photo_rows:
        _map_media_name(by_draft, lots_by_id, str(lot_id), original_name, sku, photo_count, auction_title)

    video_rows = (
        db.query(LotVideo.lot_id, LotVideo.original_name, Lot.sku, Lot.photo_count, Lot.auction_title)
        .join(Lot, LotVideo.lot_id == Lot.id)
        .filter(
            Lot.account_id == account_id,
            Lot.sale_id == sale_id,
            LotVideo.original_name.isnot(None),
        )
        .all()
    )
    for lot_id, original_name, sku, photo_count, auction_title in video_rows:
        _map_media_name(by_draft, lots_by_id, str(lot_id), original_name, sku, photo_count, auction_title)

    mapped_lot_ids = {row.lot_id for row in by_draft.values()}
    for draft_id, job_created in unmatched_jobs:
        if draft_id in by_draft:
            continue
        candidates = []
        for row in lots:
            lot_id = str(row[0])
            if lot_id in mapped_lot_ids:
                continue
            lot_created = _aware(row[4])
            if job_created is not None and lot_created is not None and lot_created < job_created - _SINGLE_LOT_SKEW:
                continue
            candidates.append(row)
        if len(candidates) != 1:
            continue
        lot_row = candidates[0]
        by_draft[draft_id] = _lot_row_to_draft(
            draft_id, str(lot_row[0]), lot_row[1], lot_row[2], lot_row[3]
        )
        mapped_lot_ids.add(str(lot_row[0]))
    return sorted(by_draft.values(), key=lambda row: row.draft_id)


def cataloged_mobile_draft_ids(db: Session, account_id: str, sale_id: str) -> list[str]:
    """Draft ids that already have a catalog lot on this sale.

    Includes completed jobs' ``client_draft_id`` even when the lot mapping is SKU-ambiguous,
    so Sync can skip a re-upload without relying on leftover filenames.
    """
    ids = {row.draft_id for row in cataloged_mobile_draft_lots(db, account_id, sale_id)}
    for (cid,) in (
        db.query(ProcessingJob.client_draft_id)
        .filter(
            ProcessingJob.account_id == account_id,
            ProcessingJob.sale_id == sale_id,
            ProcessingJob.status == JobStatus.completed,
            ProcessingJob.client_draft_id.isnot(None),
        )
        .all()
    ):
        draft_id = (cid or "").strip().lower()
        if draft_id:
            ids.add(draft_id)
    return sorted(ids)
