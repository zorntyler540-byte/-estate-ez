from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.core.gemini import analyze_photo, verify_tape_sku
from app.core.lotting import LotOutput, sku_digits, tape_sku_opens_new_lot
from app.models import JobStatus, Lot, Photo, ProcessingJob

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _save_lot(db: Session, account_id: str, lot: LotOutput) -> Lot:
    row = Lot(
        account_id=account_id,
        sku=lot.sku,
        auction_title=lot.auction_title,
        professional_description=lot.professional_description,
        specification_lines_json=json.dumps(lot.specification_lines, ensure_ascii=True),
        brands_json=json.dumps(lot.brands, ensure_ascii=True),
        labels_and_tags_json=json.dumps(lot.labels_and_tags, ensure_ascii=True),
        starting_price=lot.starting_price,
        condition_text=lot.condition,
        photo_count=lot.photo_count,
    )
    db.add(row)
    db.flush()
    return row


def process_job(db: Session, job: ProcessingJob) -> None:
    job.status = JobStatus.processing
    db.commit()

    folder = Path(job.input_folder)
    if not folder.exists() or not folder.is_dir():
        job.status = JobStatus.failed
        job.error_message = f"Input folder not found: {folder}"
        db.commit()
        return

    photos = sorted(
        [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
        key=lambda p: str(p).lower(),
    )
    current_lot: LotOutput | None = None
    current_lot_id: str | None = None

    for photo_path in photos:
        payload = analyze_photo(photo_path)
        if not payload:
            continue
        lot_candidate = LotOutput.from_model(payload)
        sku_ok = tape_sku_opens_new_lot(lot_candidate.sku)
        if sku_ok and not settings.skip_tape_verify and lot_candidate.sku:
            verified = verify_tape_sku(photo_path, sku_digits(lot_candidate.sku))
            if verified is False:
                lot_candidate.sku = None
                sku_ok = False

        if sku_ok:
            lot_candidate.sku = sku_digits(lot_candidate.sku)
            if current_lot is not None:
                persisted = _save_lot(db, job.account_id, current_lot)
                current_lot_id = persisted.id
            current_lot = lot_candidate
            current_lot_id = None
            db.add(
                Photo(
                    account_id=job.account_id,
                    lot_id=current_lot_id,
                    original_name=photo_path.name,
                    storage_path=str(photo_path.resolve()),
                )
            )
            db.commit()
            continue

        if current_lot is not None:
            current_lot.merge_detail(payload)
            db.add(
                Photo(
                    account_id=job.account_id,
                    lot_id=current_lot_id,
                    original_name=photo_path.name,
                    storage_path=str(photo_path.resolve()),
                )
            )
            db.commit()

    if current_lot is not None:
        _save_lot(db, job.account_id, current_lot)
        db.commit()

    job.status = JobStatus.completed
    db.commit()

