from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.appraisal_storage import resolve_appraisal_storage_path
from app.models import AppraisalCheck, JobStatus
from pipeline.appraiser import run_field_appraisal

logger = logging.getLogger(__name__)


def run_appraisal_for_check(db: Session, check_id: str, account_id: str) -> None:
    row = (
        db.query(AppraisalCheck)
        .filter(AppraisalCheck.id == check_id, AppraisalCheck.account_id == account_id)
        .first()
    )
    if row is None:
        return
    if row.status not in (JobStatus.queued, JobStatus.processing):
        return

    row.status = JobStatus.processing
    row.error_message = None
    db.commit()

    try:
        paths: list[str] = json.loads(row.photo_paths_json or "[]")
        names: list[str] = json.loads(row.original_names_json or "[]")
        if not paths:
            raise RuntimeError("No appraisal photos on check.")

        photo_bytes_by_name: dict[str, bytes] = {}
        for i, storage_path in enumerate(paths):
            src = resolve_appraisal_storage_path(storage_path)
            if not src.is_file():
                raise FileNotFoundError(f"Appraisal photo missing: {storage_path}")
            label = names[i] if i < len(names) and names[i] else Path(storage_path).name
            photo_bytes_by_name[label] = src.read_bytes()

        result, tokens = run_field_appraisal(photo_bytes_by_name, scene_note=row.scene_note or "")
        row.result_json = result.model_dump_json()
        row.est_input_tokens = int(tokens.get("input") or 0)
        row.est_output_tokens = int(tokens.get("output") or 0)
        row.status = JobStatus.completed
    except Exception as exc:
        logger.exception("Appraisal check %s failed", check_id)
        row.status = JobStatus.failed
        row.error_message = str(exc)[:2000]
    db.commit()
