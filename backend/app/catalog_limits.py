"""Catalog / marketplace limits (aligned with CTBids listing constraints)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

# CT Seller hard-caps listings at 40 photos.
# Set to None to disable the per-listing cap.
CTBIDS_MAX_PHOTOS_PER_LOT: int | None = 40

# Estate-EZ hosted clips (not posted to CT Bids). Keep short for field uploads.
MAX_VIDEOS_PER_LOT: int | None = 8
MAX_VIDEO_BYTES = 80 * 1024 * 1024
_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm"})

_CATALOG_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})


def job_photo_cap(*, single_lot_mode: bool) -> int | None:
    """Max stills for one catalog job. Single-lot follows CT Bids; batch has a separate ceiling."""
    if single_lot_mode:
        return CTBIDS_MAX_PHOTOS_PER_LOT
    from app.config import settings

    return int(settings.catalog_max_photos_per_job)


def job_photo_count_error(*, current: int, delta: int, single_lot_mode: bool) -> str | None:
    cap = job_photo_cap(single_lot_mode=single_lot_mode)
    if cap is None or delta <= 0:
        return None
    if current > cap:
        return (
            f"This job already has {current} photos (limit {cap}). "
            "Remove photos before adding more."
        )
    if current >= cap:
        return f"This job already has {current} photos (limit {cap})."
    if current + delta > cap:
        kind = "listing" if single_lot_mode else "batch"
        return (
            f"A {kind} upload may have at most {cap} photos; "
            f"this job has {current} and adding {delta} would exceed the limit."
        )
    return None


def assert_account_catalog_quota(
    db: Session,
    account_id: str,
    *,
    extra_jobs: int = 0,
    extra_photos: int = 0,
) -> None:
    """Reject when the account is over queued-job or daily-photo spend brakes."""
    from app.config import settings
    from app.models import JobStatus, Photo, ProcessingJob

    queued_cap = int(settings.catalog_max_queued_jobs_per_account)
    photo_cap = int(settings.catalog_max_photos_per_account_per_day)
    queued_n = int(
        db.query(func.count())
        .select_from(ProcessingJob)
        .where(
            ProcessingJob.account_id == account_id,
            ProcessingJob.status.in_((JobStatus.receiving, JobStatus.queued)),
        )
        .scalar()
        or 0
    )
    if extra_jobs and queued_n + extra_jobs > queued_cap:
        raise HTTPException(
            status_code=429,
            detail=(
                f"This account already has {queued_n} catalog job(s) waiting "
                f"(limit {queued_cap}). Wait for them to finish or cancel extras."
            ),
        )
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    stored_photos = int(
        db.query(func.count())
        .select_from(Photo)
        .where(Photo.account_id == account_id, Photo.created_at >= start)
        .scalar()
        or 0
    )
    pending_photos = 0
    pending_jobs = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.account_id == account_id,
            ProcessingJob.status.in_((JobStatus.receiving, JobStatus.queued)),
            ProcessingJob.created_at >= start,
        )
        .all()
    )
    from app.job_batch_storage import confined_job_folder

    for job in pending_jobs:
        folder = confined_job_folder(Path(job.input_folder), account_id)
        if folder is None:
            continue
        pending_photos += count_catalog_image_files(folder)
    if stored_photos + pending_photos + extra_photos > photo_cap:
        raise HTTPException(
            status_code=429,
            detail=(
                f"This account has already cataloged or queued {stored_photos + pending_photos} "
                f"photos today (limit {photo_cap}). Try again tomorrow or ask an admin to raise "
                "CATALOG_MAX_PHOTOS_PER_ACCOUNT_PER_DAY."
            ),
        )


def ctbids_lot_photo_delta_error(*, current: int, delta: int) -> str | None:
    """
    None if there is no cap, or if ``current + delta`` is within the per-listing photo cap; else an HTTP 400 detail string.

    Use after counting **actual** rows to add (saved files, photos to transfer, etc.), not only multipart part count.
    """
    if delta <= 0:
        return None
    cap = CTBIDS_MAX_PHOTOS_PER_LOT
    if cap is None:
        return None
    if current > cap:
        return (
            f"CTBids allows at most {cap} photos per listing; this lot has {current} (already over the limit). "
            "Remove photos before adding or moving more."
        )
    if current >= cap:
        return f"CTBids allows at most {cap} photos per listing; this lot already has {current}."
    if current + delta > cap:
        return (
            f"CTBids allows at most {cap} photos per listing; this lot has {current} and adding {delta} would exceed the limit."
        )
    return None


def count_catalog_image_files(root: Path) -> int:
    """Count image-like files under ``root`` (browser uploads are flattened or shallow)."""
    if not root.is_dir():
        return 0
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in _CATALOG_SUFFIXES:
            n += 1
    return n


def list_catalog_image_files_sorted(root: Path) -> list[Path]:
    """Catalog image paths under ``root`` (recursive), sorted for stable gallery order."""
    if not root.is_dir():
        return []
    paths = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _CATALOG_SUFFIXES]
    paths.sort(key=lambda p: (str(p.relative_to(root)).lower(), p.name.lower()))
    return paths


def is_catalog_video_filename(name: object | None) -> bool:
    """True when ``name`` looks like an MP4/MOV/WebM clip (not a catalog still)."""
    raw = str(name or "").strip().lower().replace("\\", "/")
    if not raw:
        return False
    return Path(raw).suffix in _VIDEO_SUFFIXES


def video_content_type(name: object | None) -> str:
    suffix = Path(str(name or "").strip().lower()).suffix
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".m4v": "video/x-m4v",
        ".webm": "video/webm",
    }.get(suffix, "application/octet-stream")


def lot_video_delta_error(*, current: int, delta: int) -> str | None:
    """None if ``current + delta`` is within the per-lot video cap; else a 400 detail."""
    if delta <= 0:
        return None
    cap = MAX_VIDEOS_PER_LOT
    if cap is None:
        return None
    if current >= cap:
        return f"This lot already has {current} video{'' if current == 1 else 's'} (max {cap})."
    if current + delta > cap:
        return (
            f"This lot has {current} video{'' if current == 1 else 's'}; "
            f"adding {delta} would exceed the limit of {cap}."
        )
    return None


def lot_video_size_error(size: int) -> str | None:
    if size <= 0:
        return "Empty video file"
    if size > MAX_VIDEO_BYTES:
        mb = MAX_VIDEO_BYTES // (1024 * 1024)
        return f"Videos must be {mb} MB or smaller (this file is {size / (1024 * 1024):.1f} MB)."
    return None
