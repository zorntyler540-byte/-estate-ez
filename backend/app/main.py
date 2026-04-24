from __future__ import annotations

import asyncio
import csv
import io
import os
import json
import queue
import re
import shutil
import smtplib
import mimetypes
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sse_starlette import EventSourceResponse

from app.auth import create_access_token, get_current_user, get_current_user_sse, hash_password, verify_password
from app.config import cors_allow_origins, public_job_input_folder, settings
from app.security_middleware import SecurityHeadersMiddleware
from app.db import Base, engine, get_db
from app.models import Account, JobStatus, Lot, Photo, ProcessingJob, Sale, User
from app.schemas import (
    CreateJobRequest,
    HealthResponse,
    JobOut,
    LoginRequest,
    LotOut,
    LotUpdate,
    RegisterRequest,
    SaleCreate,
    SaleOut,
    SaleUpdate,
    SuggestionIn,
    TokenResponse,
    UserSummary,
    LotPhotoOut,
)
from app import job_tasks
from app.services import job_events
from app.services.serialization import (
    lot_ids_with_linked_photos,
    lot_to_schema,
    photo_count_is_sale_outlier,
    photo_count_outlier_by_lot_id,
    sale_lot_photo_counts,
)
from app.services.suggestion_email import send_suggestion_email, smtp_configured
from app.services.thumbnail_image import thumbnail_jpeg_bytes
from app.upload_storage import UPLOAD_ROOT, new_batch_dir, save_uploaded_files

app = FastAPI(title=settings.app_name)


def _sale_for_user(sale_id: str, current: User, db: Session) -> Sale:
    row = (
        db.query(Sale)
        .filter(Sale.id == sale_id, Sale.account_id == current.account_id, Sale.user_id == current.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Sale not found")
    return row


def _require_open_sale(row: Sale) -> None:
    if row.closed_at is not None:
        raise HTTPException(status_code=409, detail="Sale is closed; reopen or create a new sale for new jobs.")


def _job_out(job: ProcessingJob) -> JobOut:
    return JobOut(
        id=job.id,
        account_id=job.account_id,
        user_id=job.user_id,
        sale_id=job.sale_id,
        status=job.status.value,
        input_folder=public_job_input_folder(job.input_folder),
        error_message=job.error_message,
        created_at=job.created_at,
        inherit_sku_from_subfolder=bool(job.inherit_sku_from_subfolder),
        single_lot_mode=bool(getattr(job, "single_lot_mode", False)),
        single_lot_manual_sku=job.single_lot_manual_sku,
    )


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins(),
    # Allow Chrome extension UIs to call API directly.
    allow_origin_regex=r"^chrome-extension://[a-z]{32}$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
_th = settings.trusted_host_list()
if _th:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_th)


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/auth/register", response_model=TokenResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    if settings.is_production and not settings.allow_open_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="New account registration is disabled on this server.",
        )
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")
    account = Account(name=payload.account_name)
    db.add(account)
    db.flush()
    try:
        pw_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user = User(
        account_id=account.id,
        email=payload.email.lower(),
        password_hash=pw_hash,
        role="owner",
    )
    db.add(user)
    db.flush()
    db.add(Sale(account_id=account.id, user_id=user.id, name="General"))
    db.commit()
    token = create_access_token(user.id, account.id, user.role)
    return TokenResponse(access_token=token)


@app.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid login")
    token = create_access_token(user.id, user.account_id, user.role)
    return TokenResponse(access_token=token)


@app.get("/auth/me", response_model=UserSummary)
def me(current: User = Depends(get_current_user)) -> UserSummary:
    return UserSummary(id=current.id, account_id=current.account_id, email=current.email, role=current.role)


@app.post("/feedback/suggestions")
def submit_suggestion(payload: SuggestionIn, current: User = Depends(get_current_user)) -> dict[str, str]:
    """Email product suggestions to the configured inbox (requires SMTP_* in settings)."""
    if not smtp_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Email delivery is not configured on the server. "
                "An administrator must set SMTP_HOST, SMTP_PORT, SMTP_USER, and SMTP_PASSWORD."
            ),
        )
    subject = f"[CaringTransitions] Suggestion from {current.email}"
    body = (
        f"User email: {current.email}\n"
        f"User ID: {current.id}\n"
        f"Account ID: {current.account_id}\n"
        f"Role: {current.role}\n\n"
        f"--- Message ---\n\n"
        f"{payload.message.strip()}\n"
    )
    try:
        send_suggestion_email(subject=subject, body=body)
    except (OSError, ValueError, smtplib.SMTPException) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not send email: {exc}",
        ) from exc
    return {"status": "sent"}


@app.get("/sales", response_model=list[SaleOut])
def list_sales(current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[SaleOut]:
    rows = (
        db.query(Sale)
        .filter(Sale.account_id == current.account_id, Sale.user_id == current.id)
        .order_by(Sale.created_at.asc())
        .all()
    )
    return [
        SaleOut(
            id=r.id,
            account_id=r.account_id,
            user_id=r.user_id,
            name=r.name,
            closed_at=r.closed_at,
            created_at=r.created_at,
        )
        for r in rows
    ]


@app.post("/sales", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
def create_sale(
    payload: SaleCreate, current: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> SaleOut:
    row = Sale(account_id=current.account_id, user_id=current.id, name=payload.name.strip())
    db.add(row)
    db.commit()
    db.refresh(row)
    return SaleOut(
        id=row.id,
        account_id=row.account_id,
        user_id=row.user_id,
        name=row.name,
        closed_at=row.closed_at,
        created_at=row.created_at,
    )


@app.patch("/sales/{sale_id}", response_model=SaleOut)
def update_sale(
    sale_id: str,
    payload: SaleUpdate,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaleOut:
    row = _sale_for_user(sale_id, current, db)
    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.is_closed is not None:
        row.closed_at = datetime.now(timezone.utc) if payload.is_closed else None
    db.commit()
    db.refresh(row)
    return SaleOut(
        id=row.id,
        account_id=row.account_id,
        user_id=row.user_id,
        name=row.name,
        closed_at=row.closed_at,
        created_at=row.created_at,
    )


@app.delete("/sales/{sale_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sale(
    sale_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = _sale_for_user(sale_id, current, db)
    lot_n = db.query(Lot).filter(Lot.sale_id == row.id).count()
    job_n = db.query(ProcessingJob).filter(ProcessingJob.sale_id == row.id).count()
    if lot_n or job_n:
        raise HTTPException(
            status_code=409,
            detail=f"Sale still has {lot_n} lot(s) and {job_n} job(s). Remove or reassign them first.",
        )
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/lots", response_model=list[LotOut])
def list_lots(
    sale_id: str | None = Query(None, description="When set, only lots for this sale."),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LotOut]:
    q = db.query(Lot).filter(Lot.account_id == current.account_id, Lot.user_id == current.id)
    if sale_id is not None:
        _sale_for_user(sale_id, current, db)
        q = q.filter(Lot.sale_id == sale_id)
    rows = q.order_by(Lot.created_at.desc()).all()
    lot_ids = [r.id for r in rows]
    thumb_ids = lot_ids_with_linked_photos(db, current.account_id, current.id, lot_ids)
    counts = [r.photo_count for r in rows]
    return [
        lot_to_schema(
            r,
            has_thumbnail=r.id in thumb_ids,
            many_photos_review=photo_count_is_sale_outlier(r.photo_count, counts),
        )
        for r in rows
    ]


@app.get("/lots/{lot_id}/thumbnail")
def lot_thumbnail(
    lot_id: str,
    max_px: int = 220,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """First catalog photo for the lot, resized (JPEG). Requires Bearer auth (not usable as bare img src cross-origin)."""
    max_px = min(800, max(64, int(max_px)))
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id, Lot.user_id == current.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    ph = (
        db.query(Photo)
        .filter(
            Photo.lot_id == lot_id,
            Photo.account_id == current.account_id,
            Photo.user_id == current.id,
        )
        .order_by(Photo.created_at.asc())
        .first()
    )
    if not ph:
        raise HTTPException(status_code=404, detail="No image linked to this lot")
    src = Path(ph.storage_path).resolve()
    if not src.is_file():
        raise HTTPException(status_code=404, detail="Image file missing on disk")
    try:
        body = thumbnail_jpeg_bytes(src, max_px)
    except OSError:
        raise HTTPException(status_code=404, detail="Image could not be opened") from None
    except Exception:
        raise HTTPException(status_code=415, detail="Unsupported or corrupt image") from None
    return Response(
        content=body,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


@app.get("/lots/{lot_id}", response_model=LotOut)
def get_lot(lot_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LotOut:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id, Lot.user_id == current.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    has = lot_id in lot_ids_with_linked_photos(db, current.account_id, current.id, [lot_id])
    peer = sale_lot_photo_counts(db, row.sale_id, current.account_id, current.id)
    many = photo_count_is_sale_outlier(row.photo_count, peer)
    return lot_to_schema(row, has_thumbnail=has, many_photos_review=many)


@app.get("/lots/{lot_id}/photos", response_model=list[LotPhotoOut])
def list_lot_photos(
    lot_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[LotPhotoOut]:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id, Lot.user_id == current.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == current.account_id, Photo.user_id == current.id)
        .order_by(Photo.created_at.asc())
        .all()
    )
    return [
        LotPhotoOut(id=p.id, lot_id=lot_id, original_name=p.original_name, created_at=p.created_at) for p in photos
    ]


@app.get("/lots/{lot_id}/photos/{photo_id}/thumbnail")
def lot_photo_thumbnail(
    lot_id: str,
    photo_id: str,
    max_px: int = 320,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """One catalog photo as JPEG (downscaled). Order matches ``GET …/photos`` and extension upload sequence."""
    max_px = min(800, max(64, int(max_px)))
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id, Lot.user_id == current.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    ph = (
        db.query(Photo)
        .filter(
            Photo.id == photo_id,
            Photo.lot_id == lot_id,
            Photo.account_id == current.account_id,
            Photo.user_id == current.id,
        )
        .first()
    )
    if not ph:
        raise HTTPException(status_code=404, detail="Photo not found")
    src = Path(ph.storage_path).resolve()
    if not src.is_file():
        raise HTTPException(status_code=404, detail="Image file missing on disk")
    try:
        body = thumbnail_jpeg_bytes(src, max_px)
    except OSError:
        raise HTTPException(status_code=404, detail="Image could not be opened") from None
    except Exception:
        raise HTTPException(status_code=415, detail="Unsupported or corrupt image") from None
    return Response(
        content=body,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


@app.get("/lots/{lot_id}/photos/{photo_id}/download")
def download_lot_photo(
    lot_id: str, photo_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    row = (
        db.query(Photo)
        .filter(
            Photo.id == photo_id,
            Photo.lot_id == lot_id,
            Photo.account_id == current.account_id,
            Photo.user_id == current.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Photo not found")
    src = Path(row.storage_path).resolve()
    if not src.is_file():
        raise HTTPException(status_code=404, detail="Photo file missing on disk")
    media_type, _ = mimetypes.guess_type(str(src))
    return FileResponse(
        str(src),
        media_type=media_type or "application/octet-stream",
        filename=row.original_name,
    )


def _zip_entry_basename(original_name: str, index: int, used: set[str]) -> str:
    """Stable, collision-free name inside the archive (no path components)."""
    base = Path(original_name.replace("\\", "/")).name
    base = re.sub(r"[^\w.\-]+", "_", base).strip("._") or "photo"
    if len(base) > 180:
        stem, suf = Path(base).stem[:120], Path(base).suffix
        base = f"{stem}{suf}"
    name = f"{index:02d}_{base}"
    if name not in used:
        used.add(name)
        return name
    for n in range(2, 10_000):
        alt = f"{index:02d}_{n}_{base}"
        if alt not in used:
            used.add(alt)
            return alt
    return f"{index:02d}_{base}"


@app.get("/lots/{lot_id}/photos/zip")
def download_lot_photos_zip(
    lot_id: str,
    background_tasks: BackgroundTasks,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Original catalog image files for one lot, as a single ZIP (order matches ``GET …/photos``)."""
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id, Lot.user_id == current.id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == current.account_id, Photo.user_id == current.id)
        .order_by(Photo.created_at.asc())
        .all()
    )
    if not photos:
        raise HTTPException(status_code=404, detail="No photos linked to this lot")
    fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    used: set[str] = set()
    written = 0
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i, ph in enumerate(photos):
                src = Path(ph.storage_path).resolve()
                if not src.is_file():
                    continue
                arc = _zip_entry_basename(ph.original_name, i + 1, used)
                zf.write(src, arcname=arc)
                written += 1
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    if written == 0:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail="Photo files missing on disk")

    sku_slug = re.sub(r"[^\w\-]+", "_", (lot.sku or lot.id)[:48]).strip("_") or "lot"
    download_name = f"lot-{sku_slug}-photos.zip"

    background_tasks.add_task(lambda p=str(tmp_path): Path(p).unlink(missing_ok=True))
    return FileResponse(tmp_path, media_type="application/zip", filename=download_name)


@app.patch("/lots/{lot_id}", response_model=LotOut)
def update_lot(
    lot_id: str,
    patch: LotUpdate,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LotOut:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id, Lot.user_id == current.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")

    if patch.auction_title is not None:
        row.auction_title = patch.auction_title
    if patch.professional_description is not None:
        row.professional_description = patch.professional_description
    if patch.specification_lines is not None:
        row.specification_lines_json = json.dumps(patch.specification_lines, ensure_ascii=True)
    if patch.brands is not None:
        row.brands_json = json.dumps(patch.brands, ensure_ascii=True)
    if patch.labels_and_tags is not None:
        row.labels_and_tags_json = json.dumps(patch.labels_and_tags, ensure_ascii=True)
    if patch.starting_price is not None:
        row.starting_price = patch.starting_price
    if patch.condition_text is not None:
        row.condition_text = patch.condition_text
    if patch.is_completed is not None:
        row.is_completed = bool(patch.is_completed)
        row.completed_at = datetime.now(timezone.utc) if row.is_completed else None
    if "sku" in patch.model_fields_set:
        s = (patch.sku or "").strip()
        row.sku = s or None
        row.sku_needs_review = False
    db.commit()
    db.refresh(row)
    has = lot_id in lot_ids_with_linked_photos(db, current.account_id, current.id, [lot_id])
    peer = sale_lot_photo_counts(db, row.sale_id, current.account_id, current.id)
    many = photo_count_is_sale_outlier(row.photo_count, peer)
    return lot_to_schema(row, has_thumbnail=has, many_photos_review=many)


@app.delete("/lots/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lot(
    lot_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id, Lot.user_id == current.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    db.query(Photo).filter(
        Photo.lot_id == lot_id,
        Photo.account_id == current.account_id,
        Photo.user_id == current.id,
    ).delete()
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/lots/purge-all", status_code=status.HTTP_204_NO_CONTENT)
def purge_all_lots(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Remove this user's lots, photos, jobs, and uploaded batch folders under their account (dev reset)."""
    aid = current.account_id
    uid = current.id
    upload_folders = [
        j.input_folder
        for j in db.query(ProcessingJob)
        .filter(ProcessingJob.account_id == aid, ProcessingJob.user_id == uid)
        .all()
    ]
    db.query(Photo).filter(Photo.account_id == aid, Photo.user_id == uid).delete()
    db.query(Lot).filter(Lot.account_id == aid, Lot.user_id == uid).delete()
    db.query(ProcessingJob).filter(ProcessingJob.account_id == aid, ProcessingJob.user_id == uid).delete()
    db.commit()
    for folder in upload_folders:
        _safe_remove_upload_batch_folder(aid, folder)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/jobs/upload", response_model=JobOut)
async def create_job_from_upload(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    sale_id: str = Form(),
    files: list[UploadFile] = File(),
    inherit_sku_from_subfolder: bool = Form(False),
    single_lot_mode: bool = Form(False),
    single_lot_manual_sku: str = Form(""),
) -> JobOut:
    """Queue a job from a folder chosen in the browser (files uploaded to server disk)."""
    sale = _sale_for_user(sale_id, current, db)
    _require_open_sale(sale)
    dest = new_batch_dir(current.account_id)
    await save_uploaded_files(dest, files)
    _manual_sku = (single_lot_manual_sku or "").strip() or None
    job = ProcessingJob(
        account_id=current.account_id,
        user_id=current.id,
        sale_id=sale.id,
        status=JobStatus.queued,
        input_folder=str(dest),
        inherit_sku_from_subfolder=bool(inherit_sku_from_subfolder),
        single_lot_mode=bool(single_lot_mode),
        single_lot_manual_sku=_manual_sku,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_out(job)


@app.post("/jobs", response_model=JobOut)
def create_job(
    payload: CreateJobRequest,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    sale = _sale_for_user(payload.sale_id, current, db)
    _require_open_sale(sale)
    _manual_sku = (payload.single_lot_manual_sku or "").strip() or None
    job = ProcessingJob(
        account_id=current.account_id,
        user_id=current.id,
        sale_id=sale.id,
        status=JobStatus.queued,
        input_folder=payload.input_folder,
        inherit_sku_from_subfolder=bool(payload.inherit_sku_from_subfolder),
        single_lot_mode=bool(payload.single_lot_mode),
        single_lot_manual_sku=_manual_sku,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_out(job)


@app.get("/jobs", response_model=list[JobOut])
def list_jobs(
    sale_id: str | None = Query(None, description="When set, only jobs for this sale."),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[JobOut]:
    # If the API process restarted/OOM-killed, in-memory worker threads are gone while DB rows may still say
    # "processing". Reconcile those stale rows so UI actions (delete/purge) do not remain blocked forever.
    stale_rows = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.account_id == current.account_id,
            ProcessingJob.user_id == current.id,
            ProcessingJob.status == JobStatus.processing,
        )
        .all()
    )
    changed = False
    for row in stale_rows:
        if not job_tasks.is_job_running(row.id):
            row.status = JobStatus.failed
            if not row.error_message:
                row.error_message = "Job interrupted after server restart. Re-run to continue."
            row.cancel_requested = False
            changed = True
    if changed:
        db.commit()

    q = db.query(ProcessingJob).filter(
        ProcessingJob.account_id == current.account_id, ProcessingJob.user_id == current.id
    )
    if sale_id is not None:
        _sale_for_user(sale_id, current, db)
        q = q.filter(ProcessingJob.sale_id == sale_id)
    rows = q.order_by(ProcessingJob.created_at.desc()).all()
    return [_job_out(r) for r in rows]


@app.post("/jobs/{job_id}/run", response_model=JobOut)
def run_job(job_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> JobOut:
    job = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.id == job_id,
            ProcessingJob.account_id == current.account_id,
            ProcessingJob.user_id == current.id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.queued:
        raise HTTPException(status_code=409, detail="Job must be queued to run")
    try:
        job_tasks.start_job_in_background(job.id, current.account_id)
    except RuntimeError as exc:
        if str(exc) != "job_already_running":
            raise
        raise HTTPException(status_code=409, detail="Job is already running") from exc
    # Worker sets DB to processing asynchronously; return processing so the client
    # starts status polling immediately instead of waiting on the first GET /jobs refresh.
    db.expire(job)
    db.refresh(job)
    return _job_out(job).model_copy(update={"status": JobStatus.processing.value})


@app.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> JobOut:
    job = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.id == job_id,
            ProcessingJob.account_id == current.account_id,
            ProcessingJob.user_id == current.id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.processing:
        raise HTTPException(status_code=400, detail="Only a running job can be cancelled")
    # DB flag: works for standalone worker.py and survives API reload; in-process thread also gets
    # request_cancel for immediate wake when this server started the job.
    job.cancel_requested = True
    db.commit()
    job_tasks.request_cancel(job_id)
    db.refresh(job)
    return _job_out(job)


@app.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    job = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.id == job_id,
            ProcessingJob.account_id == current.account_id,
            ProcessingJob.user_id == current.id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == JobStatus.processing:
        raise HTTPException(status_code=409, detail="Cannot delete a job that is still processing")
    folder = Path(job.input_folder).resolve()
    root = UPLOAD_ROOT.resolve()
    try:
        folder.relative_to(root)
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
    except ValueError:
        pass
    db.delete(job)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _safe_remove_upload_batch_folder(account_id: str, input_folder: str) -> None:
    """Remove a single uploaded batch directory under ``UPLOAD_ROOT / account_id`` (if present)."""
    root = UPLOAD_ROOT.resolve()
    base = (root / account_id).resolve()
    target = Path(input_folder).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)


def _queue_get_line(q: queue.Queue[str], timeout: float) -> str | None:
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


@app.get("/jobs/{job_id}/events")
async def job_events_sse(
    job_id: str,
    request: Request,
    current: User = Depends(get_current_user_sse),
    db: Session = Depends(get_db),
) -> EventSourceResponse:
    """
    Server-Sent Events for one processing job. Auth: ``Authorization: Bearer`` or query ``access_token``
    (EventSource cannot set custom headers in all browsers).
    Events are delivered only on the channel ``(user_id, job_id)`` matching the authenticated user and this job.
    """
    job = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.id == job_id,
            ProcessingJob.account_id == current.account_id,
            ProcessingJob.user_id == current.id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    q = job_events.subscribe(current.id, job_id)

    async def event_generator():
        try:
            yield {"data": json.dumps({"type": "connected", "job_id": job_id}, ensure_ascii=False)}
            await asyncio.sleep(0)
            while True:
                if await request.is_disconnected():
                    break
                line = await asyncio.to_thread(_queue_get_line, q, 1.0)
                if line is None:
                    continue
                yield {"data": line}
                await asyncio.sleep(0)
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "status" and obj.get("status") in ("completed", "failed"):
                    break
        finally:
            job_events.unsubscribe(current.id, job_id, q)

    return EventSourceResponse(
        event_generator(),
        ping=15,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _markdown_for_lots(rows: list[Lot]) -> str:
    out = ["# Estate Inventory", "", "| SKU | Listing | Price | Condition | Photos |", "| :-- | :-- | :-- | :-- | :-- |"]
    for row in rows:
        specs = json.loads(row.specification_lines_json or "[]")
        listing = (
            f"**Auction Title:** {row.auction_title}<br><br>"
            f"**Professional Description:** {row.professional_description}<br><br>"
            f"**Item Specifications:**<br>"
            + "<br>".join(f"- {s}" for s in specs)
        ).replace("|", "/")
        out.append(
            f"| {row.sku or ''} | {listing} | ${row.starting_price or ''} | {row.condition_text} | {row.photo_count} |"
        )
    return "\n".join(out)


def _lots_for_export(db: Session, current: User, sale_id: str | None) -> list[Lot]:
    q = db.query(Lot).filter(Lot.account_id == current.account_id, Lot.user_id == current.id)
    if sale_id is not None:
        _sale_for_user(sale_id, current, db)
        q = q.filter(Lot.sale_id == sale_id)
    return q.order_by(Lot.created_at).all()


@app.get("/exports/markdown")
def export_markdown(
    sale_id: str | None = Query(None, description="Limit export to lots in this sale."),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    rows = _lots_for_export(db, current, sale_id)
    return Response(_markdown_for_lots(rows), media_type="text/markdown")


@app.get("/exports/json")
def export_json(
    sale_id: str | None = Query(None, description="Limit export to lots in this sale."),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LotOut]:
    rows = _lots_for_export(db, current, sale_id)
    lot_ids = [r.id for r in rows]
    thumb_ids = lot_ids_with_linked_photos(db, current.account_id, current.id, lot_ids)
    many_map = photo_count_outlier_by_lot_id(rows)
    return [
        lot_to_schema(r, has_thumbnail=r.id in thumb_ids, many_photos_review=many_map.get(r.id, False))
        for r in rows
    ]


@app.get("/exports/csv")
def export_csv(
    sale_id: str | None = Query(None, description="Limit export to lots in this sale."),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    rows = _lots_for_export(db, current, sale_id)
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(
        [
            "sku",
            "auction_title",
            "professional_description",
            "specification_lines",
            "brands",
            "labels_and_tags",
            "starting_price",
            "condition",
            "photo_count",
            "created_at",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.sku,
                r.auction_title,
                r.professional_description,
                r.specification_lines_json,
                r.brands_json,
                r.labels_and_tags_json,
                r.starting_price,
                r.condition_text,
                r.photo_count,
                r.created_at.isoformat() if r.created_at else "",
            ]
        )
    return Response(stream.getvalue(), media_type="text/csv")

