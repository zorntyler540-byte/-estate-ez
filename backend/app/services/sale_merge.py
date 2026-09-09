"""Move one Estate-EZ catalog sale into another, then delete the empty source.

Does not combine CT Seller / CT Bids auctions. Posted ``ctseller_item_id`` values stay
on the lots; staff keep posting to the keeper sale's mapped auction.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import (
    AppraisalCheck,
    AudienceHuntEvent,
    JobStatus,
    Lot,
    ProcessingJob,
    Sale,
    SalePhotoZipExport,
)
from app.services.sale_covers import parse_cover_photo_ids, serialize_cover_photo_ids
from app.services.sku_sanity import recompute_sku_sanity_flags_for_sale

ACTIVE_JOB_STATUSES = {JobStatus.receiving, JobStatus.queued, JobStatus.processing}


class SaleMergeError(ValueError):
    """Invalid merge (wrong sale, closed, busy jobs)."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class SaleMergeResult:
    target_sale_id: str
    target_sale_name: str
    source_sale_id: str
    source_sale_name: str
    lots_moved: int
    jobs_moved: int
    appraisals_moved: int
    zip_exports_moved: int
    duplicate_sku_count: int


def _count_active_jobs(db: Session, sale_id: str, account_id: str) -> int:
    jobs = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.sale_id == sale_id,
            ProcessingJob.account_id == account_id,
            ProcessingJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .count()
    )
    zips = (
        db.query(SalePhotoZipExport)
        .filter(
            SalePhotoZipExport.sale_id == sale_id,
            SalePhotoZipExport.account_id == account_id,
            SalePhotoZipExport.status.in_(ACTIVE_JOB_STATUSES),
        )
        .count()
    )
    apps = (
        db.query(AppraisalCheck)
        .filter(
            AppraisalCheck.sale_id == sale_id,
            AppraisalCheck.account_id == account_id,
            AppraisalCheck.status.in_(ACTIVE_JOB_STATUSES),
        )
        .count()
    )
    return int(jobs) + int(zips) + int(apps)


def _sale(db: Session, sale_id: str, account_id: str) -> Sale:
    row = db.query(Sale).filter(Sale.id == sale_id, Sale.account_id == account_id).first()
    if row is None:
        raise SaleMergeError("Sale not found", status_code=404)
    return row


def merge_catalog_sales(
    db: Session,
    *,
    account_id: str,
    target_sale_id: str,
    source_sale_id: str,
) -> SaleMergeResult:
    source_id = (source_sale_id or "").strip()
    target_id = (target_sale_id or "").strip()
    if not source_id or not target_id:
        raise SaleMergeError("Source and target sales are required")
    if source_id == target_id:
        raise SaleMergeError("Cannot merge a sale into itself")

    target = _sale(db, target_id, account_id)
    source = _sale(db, source_id, account_id)
    if target.closed_at is not None or source.closed_at is not None:
        raise SaleMergeError("Both sales must be open to merge", status_code=409)

    busy = _count_active_jobs(db, source.id, account_id) + _count_active_jobs(db, target.id, account_id)
    if busy:
        raise SaleMergeError(
            "Wait until catalog uploads, photo ZIPs, and appraiser checks on both sales have finished.",
            status_code=409,
        )

    lots_moved = (
        db.query(Lot)
        .filter(Lot.sale_id == source.id, Lot.account_id == account_id)
        .update({Lot.sale_id: target.id}, synchronize_session=False)
    )
    jobs_moved = (
        db.query(ProcessingJob)
        .filter(ProcessingJob.sale_id == source.id, ProcessingJob.account_id == account_id)
        .update({ProcessingJob.sale_id: target.id}, synchronize_session=False)
    )
    appraisals_moved = (
        db.query(AppraisalCheck)
        .filter(AppraisalCheck.sale_id == source.id, AppraisalCheck.account_id == account_id)
        .update({AppraisalCheck.sale_id: target.id}, synchronize_session=False)
    )
    zip_exports_moved = (
        db.query(SalePhotoZipExport)
        .filter(SalePhotoZipExport.sale_id == source.id, SalePhotoZipExport.account_id == account_id)
        .update({SalePhotoZipExport.sale_id: target.id}, synchronize_session=False)
    )
    db.query(AudienceHuntEvent).filter(
        AudienceHuntEvent.sale_id == source.id,
        AudienceHuntEvent.account_id == account_id,
    ).update({AudienceHuntEvent.sale_id: target.id}, synchronize_session=False)

    target_covers = parse_cover_photo_ids(getattr(target, "ctseller_cover_photo_ids", None))
    source_covers = parse_cover_photo_ids(getattr(source, "ctseller_cover_photo_ids", None))
    if not target_covers and source_covers:
        target.ctseller_cover_photo_ids = serialize_cover_photo_ids(source_covers)

    source_name = source.name
    target_name = target.name
    keeper_id = target.id
    db.delete(source)
    db.flush()
    # Bulk UPDATE skips the identity map; drop cached rows so SKU flags and the
    # duplicate count see the lots on the keeper sale (session autoflush is off).
    db.expunge_all()

    recompute_sku_sanity_flags_for_sale(db, keeper_id, account_id)
    db.flush()
    duplicate_sku_count = sum(
        1
        for lot in db.query(Lot)
        .filter(Lot.sale_id == keeper_id, Lot.account_id == account_id)
        .all()
        if lot.sku_duplicate_in_sale
    )
    db.commit()
    return SaleMergeResult(
        target_sale_id=keeper_id,
        target_sale_name=target_name,
        source_sale_id=source_id,
        source_sale_name=source_name,
        lots_moved=int(lots_moved or 0),
        jobs_moved=int(jobs_moved or 0),
        appraisals_moved=int(appraisals_moved or 0),
        zip_exports_moved=int(zip_exports_moved or 0),
        duplicate_sku_count=int(duplicate_sku_count),
    )
