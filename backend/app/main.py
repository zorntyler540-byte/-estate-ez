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
import uuid
import zipfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from statistics import mean

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, nullslast, or_, select, update
from sqlalchemy.orm import Session
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sse_starlette import EventSourceResponse

from app.auth import (
    create_access_token,
    get_current_user,
    get_current_user_sse,
    hash_password,
    is_master_admin_email,
    oauth2_scheme,
    user_from_access_token,
    verify_password,
)
from app.config import cors_allow_origins, public_job_input_folder, settings
from app.security_middleware import SecurityHeadersMiddleware
from app.db import Base, engine, get_db, SessionLocal
from app import export_tasks, job_tasks
from app.services.processing import run_lot_ai_regenerate_and_save
from app.models import Account, JobStatus, Lot, Photo, ProcessingJob, Sale, SalePhotoZipExport, User
from app.schemas import (
    CreateJobRequest,
    DeleteLotPhotosIn,
    HealthResponse,
    JobOut,
    LoginRequest,
    MergeLotsIn,
    LotOut,
    LotUpdate,
    PasswordChangeIn,
    PasswordResetIn,
    RegisterRequest,
    SaleCreate,
    SaleOut,
    SaleUpdate,
    SuggestionIn,
    SuggestionSavedOut,
    TokenResponse,
    TeamMemberCreate,
    TeamMemberOut,
    AdminAccountOut,
    AdminAccountUpdateIn,
    AdminUsageAccountOut,
    AdminUsageSummaryOut,
    UserSummary,
    LotAiRegenerateIn,
    LotPhotoOut,
    ReorderLotPhotosIn,
    SalePhotoHealthOut,
    SaleZipExportOut,
    SplitPhotosToNewLotIn,
    SplitPhotosToNewLotOut,
    TransferPhotosIn,
    TransferPhotosOut,
)
from app.services import job_events
from app.services.serialization import (
    lot_ids_with_linked_photos,
    lot_photo_out,
    lot_to_schema,
    photo_count_is_sale_outlier,
    photo_count_outlier_by_lot_id,
    sale_lot_photo_counts,
    sale_lots_with_missing_photo_files,
    upload_folder_has_linked_photos,
)
from app.services.sku_sanity import recompute_sku_sanity_flags_for_sale
from app.services.suggestion_email import (
    read_recent_saved_suggestions,
    save_suggestion_local,
    send_suggestion_email,
    smtp_configured,
)
from app.services.photo_order import renormalize_photo_sort_orders
from app.services.sale_zip_export import cleanup_expired_sale_zip_exports, count_sale_catalog_photos
from app.services.thumbnail_image import thumbnail_jpeg_bytes, thumbnail_jpeg_bytes_first_available
from app.catalog_storage import catalog_storage_key, resolve_catalog_storage_path, upload_root
from app.upload_storage import UPLOAD_ROOT, new_batch_dir, save_uploaded_files
from app.catalog_limits import (
    CTBIDS_MAX_PHOTOS_PER_LOT,
    count_catalog_image_files,
    ctbids_lot_photo_delta_error,
    list_catalog_image_files_sorted,
)

app = FastAPI(title=settings.app_name)


def _sale_for_user(sale_id: str, current: User, db: Session) -> Sale:
    row = (
        db.query(Sale)
        .filter(Sale.id == sale_id, Sale.account_id == current.account_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Sale not found")
    return row


def _require_owner(current: User) -> None:
    if (current.role or "").strip().lower() != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required")


def _require_master_admin(current: User) -> None:
    if not is_master_admin_email(current.email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Master admin access required")


def _require_open_sale(row: Sale) -> None:
    if row.closed_at is not None:
        raise HTTPException(status_code=409, detail="Sale is closed; reopen or create a new sale for new jobs.")


def _assert_sale_editable_for_lot_moves(db: Session, sale_id: str, current: User) -> Sale:
    sale = _sale_for_user(sale_id, current, db)
    _require_open_sale(sale)
    return sale


def _reconcile_lot_photo_count(db: Session, lot_id: str, account_id: str) -> None:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
    if not row:
        return
    n = (
        db.query(func.count())
        .select_from(Photo)
        .where(Photo.lot_id == lot_id, Photo.account_id == account_id)
        .scalar()
    )
    row.photo_count = int(n or 0)


def _lot_out_for_id(db: Session, account_id: str, lot_id: str) -> LotOut:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    has = lot_id in lot_ids_with_linked_photos(db, account_id, [lot_id])
    peer = sale_lot_photo_counts(db, row.sale_id, account_id)
    many = photo_count_is_sale_outlier(row.photo_count, peer)
    return lot_to_schema(row, has_thumbnail=has, many_photos_review=many)


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
    return HealthResponse(
        status="ok",
        pipeline_triage_brand_audit=settings.pipeline_triage_brand_audit,
        gemini_pro_model=settings.gemini_pro_model,
        gemini_flash_model=settings.gemini_flash_model,
    )


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
    account = Account(name=payload.account_name.strip(), plan_code="beta_free", is_active=True)
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
    acct_active = db.query(Account.is_active).filter(Account.id == user.account_id).scalar()
    if acct_active is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")
    token = create_access_token(user.id, user.account_id, user.role)
    return TokenResponse(access_token=token)


@app.get("/auth/me", response_model=UserSummary)
def me(current: User = Depends(get_current_user)) -> UserSummary:
    return UserSummary(id=current.id, account_id=current.account_id, email=current.email, role=current.role)


@app.patch("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
def change_own_password(
    payload: PasswordChangeIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="New password must be different")
    row = db.query(User).filter(User.id == current.id, User.account_id == current.account_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(payload.current_password, row.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    try:
        row.password_hash = hash_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/team/members", response_model=list[TeamMemberOut])
def list_team_members(current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[TeamMemberOut]:
    _require_owner(current)
    rows = (
        db.query(User)
        .filter(User.account_id == current.account_id)
        .order_by(User.created_at.asc())
        .all()
    )
    return [TeamMemberOut(id=r.id, email=r.email, role=r.role, created_at=r.created_at) for r in rows]


@app.post("/team/members", response_model=TeamMemberOut, status_code=status.HTTP_201_CREATED)
def create_team_member(
    payload: TeamMemberCreate,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TeamMemberOut:
    _require_owner(current)
    email = payload.email.lower().strip()
    existing = db.query(User).filter(User.email == email).first()
    if existing is not None:
        raise HTTPException(status_code=400, detail="Email already exists")
    try:
        pw_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    role = payload.role.strip().lower()
    if role not in ("owner", "catalogger"):
        raise HTTPException(status_code=400, detail="Role must be owner or catalogger")
    row = User(
        account_id=current.account_id,
        email=email,
        password_hash=pw_hash,
        role=role,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return TeamMemberOut(id=row.id, email=row.email, role=row.role, created_at=row.created_at)


@app.post("/team/members/{member_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_team_member_password(
    member_id: str,
    payload: PasswordResetIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    _require_owner(current)
    if member_id == current.id:
        raise HTTPException(status_code=400, detail="Use /auth/password to change your own password")
    row = db.query(User).filter(User.id == member_id, User.account_id == current.account_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Team member not found")
    try:
        row.password_hash = hash_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/admin/accounts", response_model=list[AdminAccountOut])
def admin_list_accounts(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AdminAccountOut]:
    _require_master_admin(current)
    rows = db.query(Account).order_by(Account.created_at.desc()).all()
    out: list[AdminAccountOut] = []
    for a in rows:
        owner_email = (
            db.query(User.email)
            .filter(User.account_id == a.id, User.role == "owner")
            .order_by(User.created_at.asc())
            .scalar()
        )
        if not owner_email:
            owner_email = (
                db.query(User.email)
                .filter(User.account_id == a.id)
                .order_by(User.created_at.asc())
                .scalar()
            )
        user_count = db.query(func.count()).select_from(User).filter(User.account_id == a.id).scalar() or 0
        sale_count = db.query(func.count()).select_from(Sale).filter(Sale.account_id == a.id).scalar() or 0
        lot_count = db.query(func.count()).select_from(Lot).filter(Lot.account_id == a.id).scalar() or 0
        job_count = (
            db.query(func.count()).select_from(ProcessingJob).filter(ProcessingJob.account_id == a.id).scalar() or 0
        )
        out.append(
            AdminAccountOut(
                id=a.id,
                name=a.name,
                owner_email=owner_email,
                plan_code=a.plan_code,
                is_active=bool(a.is_active),
                created_at=a.created_at,
                user_count=int(user_count),
                sale_count=int(sale_count),
                lot_count=int(lot_count),
                job_count=int(job_count),
            )
        )
    return out


@app.patch("/admin/accounts/{account_id}", response_model=AdminAccountOut)
def admin_update_account(
    account_id: str,
    payload: AdminAccountUpdateIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdminAccountOut:
    _require_master_admin(current)
    row = db.query(Account).filter(Account.id == account_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if payload.is_active is not None:
        row.is_active = bool(payload.is_active)
    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.plan_code is not None:
        row.plan_code = payload.plan_code.strip().lower()
    db.commit()
    db.refresh(row)
    owner_email = (
        db.query(User.email)
        .filter(User.account_id == row.id, User.role == "owner")
        .order_by(User.created_at.asc())
        .scalar()
    )
    if not owner_email:
        owner_email = (
            db.query(User.email)
            .filter(User.account_id == row.id)
            .order_by(User.created_at.asc())
            .scalar()
        )
    user_count = db.query(func.count()).select_from(User).filter(User.account_id == row.id).scalar() or 0
    sale_count = db.query(func.count()).select_from(Sale).filter(Sale.account_id == row.id).scalar() or 0
    lot_count = db.query(func.count()).select_from(Lot).filter(Lot.account_id == row.id).scalar() or 0
    job_count = db.query(func.count()).select_from(ProcessingJob).filter(ProcessingJob.account_id == row.id).scalar() or 0
    return AdminAccountOut(
        id=row.id,
        name=row.name,
        owner_email=owner_email,
        plan_code=row.plan_code,
        is_active=bool(row.is_active),
        created_at=row.created_at,
        user_count=int(user_count),
        sale_count=int(sale_count),
        lot_count=int(lot_count),
        job_count=int(job_count),
    )


@app.post("/admin/accounts/{account_id}/archive", response_model=AdminAccountOut)
def admin_archive_account(
    account_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdminAccountOut:
    _require_master_admin(current)
    row = db.query(Account).filter(Account.id == account_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    row.is_active = False
    db.commit()
    db.refresh(row)
    owner_email = (
        db.query(User.email)
        .filter(User.account_id == row.id, User.role == "owner")
        .order_by(User.created_at.asc())
        .scalar()
    )
    if not owner_email:
        owner_email = (
            db.query(User.email)
            .filter(User.account_id == row.id)
            .order_by(User.created_at.asc())
            .scalar()
        )
    user_count = db.query(func.count()).select_from(User).filter(User.account_id == row.id).scalar() or 0
    sale_count = db.query(func.count()).select_from(Sale).filter(Sale.account_id == row.id).scalar() or 0
    lot_count = db.query(func.count()).select_from(Lot).filter(Lot.account_id == row.id).scalar() or 0
    job_count = db.query(func.count()).select_from(ProcessingJob).filter(ProcessingJob.account_id == row.id).scalar() or 0
    return AdminAccountOut(
        id=row.id,
        name=row.name,
        owner_email=owner_email,
        plan_code=row.plan_code,
        is_active=bool(row.is_active),
        created_at=row.created_at,
        user_count=int(user_count),
        sale_count=int(sale_count),
        lot_count=int(lot_count),
        job_count=int(job_count),
    )


@app.post("/admin/accounts/{account_id}/restore", response_model=AdminAccountOut)
def admin_restore_account(
    account_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdminAccountOut:
    _require_master_admin(current)
    row = db.query(Account).filter(Account.id == account_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    row.is_active = True
    db.commit()
    db.refresh(row)
    owner_email = (
        db.query(User.email)
        .filter(User.account_id == row.id, User.role == "owner")
        .order_by(User.created_at.asc())
        .scalar()
    )
    if not owner_email:
        owner_email = (
            db.query(User.email)
            .filter(User.account_id == row.id)
            .order_by(User.created_at.asc())
            .scalar()
        )
    user_count = db.query(func.count()).select_from(User).filter(User.account_id == row.id).scalar() or 0
    sale_count = db.query(func.count()).select_from(Sale).filter(Sale.account_id == row.id).scalar() or 0
    lot_count = db.query(func.count()).select_from(Lot).filter(Lot.account_id == row.id).scalar() or 0
    job_count = db.query(func.count()).select_from(ProcessingJob).filter(ProcessingJob.account_id == row.id).scalar() or 0
    return AdminAccountOut(
        id=row.id,
        name=row.name,
        owner_email=owner_email,
        plan_code=row.plan_code,
        is_active=bool(row.is_active),
        created_at=row.created_at,
        user_count=int(user_count),
        sale_count=int(sale_count),
        lot_count=int(lot_count),
        job_count=int(job_count),
    )


@app.delete("/admin/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_account(
    account_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    _require_master_admin(current)
    row = db.query(Account).filter(Account.id == account_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if account_id == current.account_id:
        raise HTTPException(status_code=400, detail="Cannot delete your currently signed-in account")
    # Explicit delete order (no DB-level cascade configured): children first, then parent account.
    db.query(SalePhotoZipExport).filter(SalePhotoZipExport.account_id == account_id).delete(synchronize_session=False)
    db.query(ProcessingJob).filter(ProcessingJob.account_id == account_id).delete(synchronize_session=False)
    db.query(Photo).filter(Photo.account_id == account_id).delete(synchronize_session=False)
    db.query(Lot).filter(Lot.account_id == account_id).delete(synchronize_session=False)
    db.query(Sale).filter(Sale.account_id == account_id).delete(synchronize_session=False)
    db.query(User).filter(User.account_id == account_id).delete(synchronize_session=False)
    db.query(Account).filter(Account.id == account_id).delete(synchronize_session=False)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/admin/analytics", response_model=AdminUsageSummaryOut)
def admin_analytics(
    since_days: int = Query(30, ge=1, le=365),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdminUsageSummaryOut:
    _require_master_admin(current)
    in_rate = float(settings.gemini_input_cost_per_million)
    out_rate = float(settings.gemini_output_cost_per_million)

    def _est_cost_usd(inp: int, out: int) -> float:
        return round((inp / 1_000_000.0) * in_rate + (out / 1_000_000.0) * out_rate, 4)

    since_dt = datetime.now(timezone.utc) - timedelta(days=since_days)
    accounts = db.query(Account).order_by(Account.created_at.asc()).all()
    rows: list[AdminUsageAccountOut] = []
    for a in accounts:
        jobs = (
            db.query(ProcessingJob)
            .filter(ProcessingJob.account_id == a.id, ProcessingJob.created_at >= since_dt)
            .all()
        )
        jobs_started = len(jobs)
        jobs_completed = sum(1 for j in jobs if j.status == JobStatus.completed)
        jobs_failed = sum(1 for j in jobs if j.status == JobStatus.failed)
        durations: list[float] = []
        for j in jobs:
            if j.updated_at and j.created_at:
                durations.append(max(0.0, (j.updated_at - j.created_at).total_seconds()))
        photos_uploaded = int(
            db.query(func.count())
            .select_from(Photo)
            .filter(Photo.account_id == a.id, Photo.created_at >= since_dt)
            .scalar()
            or 0
        )
        lots_created = int(
            db.query(func.count())
            .select_from(Lot)
            .filter(Lot.account_id == a.id, Lot.created_at >= since_dt)
            .scalar()
            or 0
        )
        est_input_tokens = int(sum(int(getattr(j, "est_input_tokens", 0) or 0) for j in jobs))
        est_output_tokens = int(sum(int(getattr(j, "est_output_tokens", 0) or 0) for j in jobs))
        rows.append(
            AdminUsageAccountOut(
                account_id=a.id,
                account_name=a.name,
                jobs_started=jobs_started,
                jobs_completed=jobs_completed,
                jobs_failed=jobs_failed,
                photos_uploaded=photos_uploaded,
                lots_created=lots_created,
                avg_photos_per_job=round((photos_uploaded / jobs_started), 2) if jobs_started else 0.0,
                avg_job_duration_seconds=round(mean(durations), 1) if durations else 0.0,
                est_input_tokens=est_input_tokens,
                est_output_tokens=est_output_tokens,
                est_total_cost_usd=_est_cost_usd(est_input_tokens, est_output_tokens),
            )
        )
    total_accounts = len(rows)
    totals = AdminUsageAccountOut(
        account_id="all",
        account_name="All accounts",
        jobs_started=sum(r.jobs_started for r in rows),
        jobs_completed=sum(r.jobs_completed for r in rows),
        jobs_failed=sum(r.jobs_failed for r in rows),
        photos_uploaded=sum(r.photos_uploaded for r in rows),
        lots_created=sum(r.lots_created for r in rows),
        avg_photos_per_job=(
            round(sum(r.photos_uploaded for r in rows) / sum(r.jobs_started for r in rows), 2)
            if sum(r.jobs_started for r in rows) > 0
            else 0.0
        ),
        avg_job_duration_seconds=round(mean([r.avg_job_duration_seconds for r in rows if r.jobs_started > 0]), 1)
        if any(r.jobs_started > 0 for r in rows)
        else 0.0,
        est_input_tokens=sum(r.est_input_tokens for r in rows),
        est_output_tokens=sum(r.est_output_tokens for r in rows),
        est_total_cost_usd=round(sum(r.est_total_cost_usd for r in rows), 4),
    )
    return AdminUsageSummaryOut(
        since_days=since_days,
        total_accounts=total_accounts,
        totals=totals,
        by_account=rows,
    )


@app.post("/feedback/suggestions")
def submit_suggestion(payload: SuggestionIn, current: User = Depends(get_current_user)) -> dict[str, str]:
    """Send product suggestions to inbox, or persist locally when SMTP is unavailable."""
    subject = f"[CaringTransitions] Suggestion from {current.email}"
    body = (
        f"User email: {current.email}\n"
        f"User ID: {current.id}\n"
        f"Account ID: {current.account_id}\n"
        f"Role: {current.role}\n\n"
        f"--- Message ---\n\n"
        f"{payload.message.strip()}\n"
    )
    if not smtp_configured():
        try:
            save_suggestion_local(
                {
                    "subject": subject,
                    "user_email": current.email,
                    "user_id": current.id,
                    "account_id": current.account_id,
                    "role": current.role,
                    "message": payload.message.strip(),
                }
            )
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Suggestion could not be saved locally: {exc}",
            ) from exc
        return {"status": "saved_local"}
    try:
        send_suggestion_email(subject=subject, body=body)
    except (OSError, ValueError, smtplib.SMTPException) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not send email: {exc}",
        ) from exc
    return {"status": "sent"}


@app.get("/feedback/suggestions/saved", response_model=list[SuggestionSavedOut])
def list_saved_suggestions(
    limit: int = Query(50, ge=1, le=200),
    current: User = Depends(get_current_user),
) -> list[SuggestionSavedOut]:
    """Owner-only view of local fallback suggestions for pilot workflow."""
    if (current.role or "").strip().lower() != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required")
    rows = read_recent_saved_suggestions(limit=limit)
    return [SuggestionSavedOut(**r) for r in rows]


@app.get("/sales", response_model=list[SaleOut])
def list_sales(current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[SaleOut]:
    rows = (
        db.query(Sale)
        .filter(Sale.account_id == current.account_id)
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


@app.get("/sales/{sale_id}/photo-health", response_model=SalePhotoHealthOut)
def sale_photo_health(
    sale_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SalePhotoHealthOut:
    """
    Lots in this sale whose catalog ``Photo`` rows point at files missing on the API host.

    Use for dashboard repair hints (e.g. after job cleanup or a redeploy without a volume).
    """
    sale = db.query(Sale).filter(Sale.id == sale_id, Sale.account_id == current.account_id).first()
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    return SalePhotoHealthOut(lots=sale_lots_with_missing_photo_files(db, current.account_id, sale_id))


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
        _require_owner(current)
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
    _require_owner(current)
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
    response: Response,
    sale_id: str | None = Query(None, description="When set, only lots for this sale."),
    sku_flags: str | None = Query(
        None,
        description="When set to duplicate, text_mismatch, or any, only rows with that SKU sanity flag.",
    ),
    sort: str = Query(
        "created_desc",
        description="created_desc (default), created_asc (oldest first), or sku_asc.",
    ),
    limit: int | None = Query(
        None,
        ge=1,
        le=500,
        description="When set, return at most this many rows after offset; response includes X-Total-Count.",
    ),
    offset: int = Query(0, ge=0, le=100_000),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LotOut]:
    sort_key = (sort or "created_desc").strip().lower()
    if sort_key not in ("created_desc", "created_asc", "sku_asc"):
        raise HTTPException(status_code=400, detail="sort must be created_desc, created_asc, or sku_asc")

    q = db.query(Lot).filter(Lot.account_id == current.account_id)
    if sale_id is not None:
        _sale_for_user(sale_id, current, db)
        q = q.filter(Lot.sale_id == sale_id)

    fk = (sku_flags or "").strip().lower()
    if fk:
        if fk not in ("duplicate", "text_mismatch", "any"):
            raise HTTPException(
                status_code=400,
                detail="sku_flags must be one of: duplicate, text_mismatch, any",
            )
        if fk == "duplicate":
            q = q.filter(Lot.sku_duplicate_in_sale.is_(True))
        elif fk == "text_mismatch":
            q = q.filter(Lot.sku_text_mismatch.is_(True))
        else:
            q = q.filter(or_(Lot.sku_duplicate_in_sale.is_(True), Lot.sku_text_mismatch.is_(True)))

    if sort_key == "created_asc":
        q = q.order_by(Lot.created_at.asc())
    elif sort_key == "sku_asc":
        q = q.order_by(nullslast(Lot.sku.asc()), Lot.created_at.asc())
    else:
        q = q.order_by(Lot.created_at.desc())

    if limit is not None:
        total_stmt = select(func.count()).select_from(Lot).where(Lot.account_id == current.account_id)
        if sale_id is not None:
            total_stmt = total_stmt.where(Lot.sale_id == sale_id)
        total = int(db.scalar(total_stmt) or 0)
        response.headers["X-Total-Count"] = str(total)
        rows = q.offset(offset).limit(limit).all()
    else:
        rows = q.all()
    lot_ids = [r.id for r in rows]
    thumb_ids = lot_ids_with_linked_photos(db, current.account_id, lot_ids)
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
    token: str = Depends(oauth2_scheme),
) -> Response:
    """First catalog photo for the lot, resized (JPEG). Requires Bearer auth (not usable as bare img src cross-origin)."""
    max_px = min(800, max(64, int(max_px)))
    # Short-lived session only: ``thumbnail_jpeg_bytes`` can be slow on large originals — do not hold a pool
    # connection during Pillow work (concurrent dashboard thumbs exhausted QueuePool in production logs).
    with SessionLocal() as db:
        user = user_from_access_token(token, db)
        row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == user.account_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Lot not found")
        photos = (
            db.query(Photo)
            .filter(
                Photo.lot_id == lot_id,
                Photo.account_id == user.account_id,
            )
            .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
            .all()
        )
        if not photos:
            raise HTTPException(status_code=404, detail="No image linked to this lot")
        paths = [resolve_catalog_storage_path(p.storage_path) for p in photos]
    try:
        body = thumbnail_jpeg_bytes_first_available(paths, max_px)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image file missing on disk") from None
    except OSError:
        raise HTTPException(status_code=404, detail="Image could not be opened") from None
    except Exception:
        raise HTTPException(status_code=415, detail="Unsupported or corrupt image") from None
    return Response(
        content=body,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/lots/{lot_id}", response_model=LotOut)
def get_lot(lot_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LotOut:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    has = lot_id in lot_ids_with_linked_photos(db, current.account_id, [lot_id])
    peer = sale_lot_photo_counts(db, row.sale_id, current.account_id)
    many = photo_count_is_sale_outlier(row.photo_count, peer)
    return lot_to_schema(row, has_thumbnail=has, many_photos_review=many)


@app.get("/lots/{lot_id}/photos", response_model=list[LotPhotoOut])
def list_lot_photos(
    lot_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[LotPhotoOut]:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == current.account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
        .all()
    )
    return [lot_photo_out(p, lot_id=lot_id) for p in photos]


@app.post("/lots/{lot_id}/photos/upload", response_model=list[LotPhotoOut])
async def upload_lot_photos(
    lot_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    files: list[UploadFile] = File(),
) -> list[LotPhotoOut]:
    """
    Append new catalog images to an existing lot (same sale must be open).

    Files are stored under a new upload batch, compressed like ``/jobs/upload``, then linked in gallery order
    after existing photos.
    """
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    _assert_sale_editable_for_lot_moves(db, row.sale_id, current)
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    existing = (
        db.query(func.count())
        .select_from(Photo)
        .where(Photo.lot_id == lot_id, Photo.account_id == current.account_id)
        .scalar()
    )
    n_existing = int(existing or 0)
    if err := ctbids_lot_photo_delta_error(current=n_existing, delta=len(files)):
        raise HTTPException(status_code=400, detail=err)
    dest = new_batch_dir(current.account_id)
    try:
        await save_uploaded_files(dest, files)
        paths = list_catalog_image_files_sorted(dest)
        if not paths:
            raise HTTPException(
                status_code=400,
                detail="No valid image files could be saved (try JPEG, PNG, WebP, or HEIC).",
            )
        # Multipart ``len(files)`` can disagree with saved image count; enforce on real paths (closes over-40 bypass).
        if err := ctbids_lot_photo_delta_error(current=n_existing, delta=len(paths)):
            raise HTTPException(status_code=400, detail=err)
    except HTTPException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    mx = (
        db.query(func.max(Photo.sort_order))
        .filter(Photo.lot_id == lot_id, Photo.account_id == current.account_id)
        .scalar()
    )
    base = (int(mx) if mx is not None else -1) + 1
    for i, path in enumerate(paths):
        db.add(
            Photo(
                account_id=current.account_id,
                user_id=current.id,
                lot_id=lot_id,
                original_name=path.name,
                storage_path=catalog_storage_key(path),
                sort_order=base + i,
            )
        )
    renormalize_photo_sort_orders(db, lot_id, current.account_id)
    _reconcile_lot_photo_count(db, lot_id, current.account_id)
    db.commit()
    recompute_sku_sanity_flags_for_sale(db, row.sale_id, current.account_id)
    db.commit()

    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == current.account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
        .all()
    )
    return [lot_photo_out(p, lot_id=lot_id) for p in photos]


@app.post("/lots/{lot_id}/photos/delete", response_model=LotOut)
def delete_lot_photos(
    lot_id: str,
    body: DeleteLotPhotosIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LotOut:
    """Permanently remove selected catalog photos from this lot (same sale must be open)."""
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    _assert_sale_editable_for_lot_moves(db, row.sale_id, current)

    ids = list(dict.fromkeys(body.photo_ids))
    to_delete = (
        db.query(Photo)
        .filter(
            Photo.lot_id == lot_id,
            Photo.account_id == current.account_id,
            Photo.id.in_(ids),
        )
        .all()
    )
    found_ids = {p.id for p in to_delete}
    if found_ids != set(ids):
        missing = sorted(set(ids) - found_ids)
        preview = ", ".join(missing[:5])
        more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
        raise HTTPException(
            status_code=400,
            detail=f"Photo id(s) not on this lot: {preview}{more}" if preview else "No matching photos on this lot.",
        )

    paths = [resolve_catalog_storage_path(p.storage_path) for p in to_delete]
    for ph in to_delete:
        db.delete(ph)
    renormalize_photo_sort_orders(db, lot_id, current.account_id)
    _reconcile_lot_photo_count(db, lot_id, current.account_id)
    db.commit()
    for path in paths:
        path.unlink(missing_ok=True)
    return _lot_out_for_id(db, current.account_id, lot_id)


@app.patch("/lots/{lot_id}/photos/reorder", response_model=list[LotPhotoOut])
def reorder_lot_photos(
    lot_id: str,
    body: ReorderLotPhotosIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LotPhotoOut]:
    """Set display order for all catalog photos on this lot (must list each id exactly once)."""
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    _assert_sale_editable_for_lot_moves(db, row.sale_id, current)
    existing = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == current.account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
        .all()
    )
    existing_ids = [p.id for p in existing]
    want = list(dict.fromkeys(body.photo_ids))
    if len(want) != len(existing_ids) or set(want) != set(existing_ids):
        raise HTTPException(
            status_code=400,
            detail="photo_ids must list every catalog photo on this lot exactly once, with no extras.",
        )
    for i, pid in enumerate(want):
        db.execute(
            update(Photo)
            .where(
                Photo.id == pid,
                Photo.lot_id == lot_id,
                Photo.account_id == current.account_id,
            )
            .values(sort_order=i)
        )
    db.commit()
    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == current.account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
        .all()
    )
    return [lot_photo_out(p, lot_id=lot_id) for p in photos]


@app.post("/lots/{target_lot_id}/photos/transfer", response_model=TransferPhotosOut)
def transfer_photos_to_lot(
    target_lot_id: str,
    body: TransferPhotosIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TransferPhotosOut:
    """
    Reassign selected catalog photos to ``target_lot_id``. Every photo must already belong to a lot
    in the **same sale** as the target; the sale must not be closed.
    """
    target = db.query(Lot).filter(Lot.id == target_lot_id, Lot.account_id == current.account_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target lot not found")
    _assert_sale_editable_for_lot_moves(db, target.sale_id, current)

    ids = list(dict.fromkeys(body.photo_ids))  # stable dedupe
    n_target = int(
        db.query(func.count())
        .select_from(Photo)
        .where(Photo.lot_id == target_lot_id, Photo.account_id == current.account_id)
        .scalar()
        or 0
    )
    to_move = 0
    for pid in ids:
        ph = db.query(Photo).filter(Photo.id == pid, Photo.account_id == current.account_id).first()
        if ph and ph.lot_id != target_lot_id:
            to_move += 1
    if err := ctbids_lot_photo_delta_error(current=n_target, delta=to_move):
        raise HTTPException(status_code=400, detail=err)

    touched_lots: set[str] = {target_lot_id}
    moved = 0
    mx = (
        db.query(func.max(Photo.sort_order))
        .filter(Photo.lot_id == target_lot_id, Photo.account_id == current.account_id)
        .scalar()
    )
    base = (int(mx) if mx is not None else -1) + 1
    slot = 0
    for pid in ids:
        ph = db.query(Photo).filter(Photo.id == pid, Photo.account_id == current.account_id).first()
        if not ph:
            raise HTTPException(status_code=404, detail=f"Photo not found: {pid}")
        if ph.lot_id is None:
            raise HTTPException(status_code=400, detail=f"Photo {pid} is not attached to a lot yet.")
        if ph.lot_id == target_lot_id:
            continue
        src_lot = db.query(Lot).filter(Lot.id == ph.lot_id, Lot.account_id == current.account_id).first()
        if not src_lot:
            raise HTTPException(status_code=400, detail=f"Photo {pid} references a missing lot.")
        if src_lot.sale_id != target.sale_id:
            raise HTTPException(
                status_code=400,
                detail="Photos can only move between lots in the same sale.",
            )
        touched_lots.add(ph.lot_id)
        ph.lot_id = target_lot_id
        ph.user_id = current.id
        ph.sort_order = base + slot
        slot += 1
        moved += 1

    if moved == 0:
        raise HTTPException(
            status_code=400,
            detail="No photos were moved — they may already be on this lot.",
        )

    for lid in touched_lots:
        renormalize_photo_sort_orders(db, lid, current.account_id)
        _reconcile_lot_photo_count(db, lid, current.account_id)
    db.commit()
    recompute_sku_sanity_flags_for_sale(db, target.sale_id, current.account_id)
    db.commit()

    updated = [_lot_out_for_id(db, current.account_id, lid) for lid in sorted(touched_lots)]
    return TransferPhotosOut(moved=moved, updated_lots=updated)


@app.post("/lots/{source_lot_id}/photos/new-lot", response_model=SplitPhotosToNewLotOut, status_code=status.HTTP_201_CREATED)
def split_selected_photos_into_new_lot(
    source_lot_id: str,
    body: SplitPhotosToNewLotIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SplitPhotosToNewLotOut:
    """
    Create a new empty lot in the same sale and move the selected photos off ``source_lot_id``.
    At least one photo must remain on the source lot.
    """
    source = db.query(Lot).filter(Lot.id == source_lot_id, Lot.account_id == current.account_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Lot not found")
    _assert_sale_editable_for_lot_moves(db, source.sale_id, current)

    ids = list(dict.fromkeys(body.photo_ids))
    total = int(
        db.query(func.count())
        .select_from(Photo)
        .where(Photo.lot_id == source_lot_id, Photo.account_id == current.account_id)
        .scalar()
        or 0
    )
    if total < 2:
        raise HTTPException(
            status_code=400,
            detail="This lot needs at least two catalog photos to split a subset into a new lot.",
        )
    if len(ids) >= total:
        raise HTTPException(
            status_code=400,
            detail="Leave at least one photo on this lot — select fewer images, move extras to an existing lot instead.",
        )
    if CTBIDS_MAX_PHOTOS_PER_LOT is not None and len(ids) > CTBIDS_MAX_PHOTOS_PER_LOT:
        raise HTTPException(
            status_code=400,
            detail=(
                f"CTBids allows at most {CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing; "
                f"cannot move {len(ids)} photos into a new lot in one split."
            ),
        )

    title_default = f"New lot (split from {source.sku.strip()})" if (source.sku or "").strip() else "New lot (split)"
    new_title = ((body.auction_title or "").strip() or title_default)[:500]

    photos_to_move: list[Photo] = []
    for pid in ids:
        ph = db.query(Photo).filter(Photo.id == pid, Photo.account_id == current.account_id).first()
        if not ph:
            raise HTTPException(status_code=404, detail=f"Photo not found: {pid}")
        if ph.lot_id != source_lot_id:
            raise HTTPException(status_code=400, detail=f"Photo {pid} is not on this lot.")
        photos_to_move.append(ph)

    new_lot = Lot(
        id=str(uuid.uuid4()),
        account_id=current.account_id,
        user_id=current.id,
        sale_id=source.sale_id,
        sku=None,
        auction_title=new_title,
        professional_description="",
        specification_lines_json=json.dumps(
            ["Photos moved here from another listing (split). Edit SKU, title, and description."],
            ensure_ascii=True,
        ),
        brands_json=json.dumps([], ensure_ascii=True),
        labels_and_tags_json=json.dumps([], ensure_ascii=True),
        ct_category=None,
        ct_subcategory=None,
        is_completed=False,
        completed_at=None,
        starting_price=None,
        condition_text="",
        photo_count=0,
        sku_needs_review=True,
        sku_duplicate_in_sale=False,
        sku_text_mismatch=False,
    )
    db.add(new_lot)
    db.flush()

    for i, ph in enumerate(photos_to_move):
        ph.lot_id = new_lot.id
        ph.user_id = current.id
        ph.sort_order = i

    renormalize_photo_sort_orders(db, source_lot_id, current.account_id)
    _reconcile_lot_photo_count(db, source_lot_id, current.account_id)
    _reconcile_lot_photo_count(db, new_lot.id, current.account_id)
    db.commit()
    db.refresh(new_lot)
    db.refresh(source)
    recompute_sku_sanity_flags_for_sale(db, source.sale_id, current.account_id)
    db.commit()
    db.refresh(new_lot)
    db.refresh(source)

    return SplitPhotosToNewLotOut(
        new_lot=_lot_out_for_id(db, current.account_id, new_lot.id),
        source_lot=_lot_out_for_id(db, current.account_id, source_lot_id),
    )


@app.post("/lots/{target_lot_id}/merge", response_model=LotOut)
def merge_lot_into_target(
    target_lot_id: str,
    body: MergeLotsIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LotOut:
    """
    Move every photo from ``source_lot_id`` onto this lot, append a short merge note to specs,
    delete the source lot row, then return the updated target lot.
    """
    if body.source_lot_id == target_lot_id:
        raise HTTPException(status_code=400, detail="Cannot merge a lot into itself.")
    target = db.query(Lot).filter(Lot.id == target_lot_id, Lot.account_id == current.account_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target lot not found")
    source = (
        db.query(Lot)
        .filter(Lot.id == body.source_lot_id, Lot.account_id == current.account_id)
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Source lot not found")
    if source.sale_id != target.sale_id:
        raise HTTPException(status_code=400, detail="Lots must belong to the same sale to merge.")
    _assert_sale_editable_for_lot_moves(db, target.sale_id, current)

    n_target = int(
        db.query(func.count())
        .select_from(Photo)
        .where(Photo.lot_id == target.id, Photo.account_id == current.account_id)
        .scalar()
        or 0
    )
    n_source = int(
        db.query(func.count())
        .select_from(Photo)
        .where(Photo.lot_id == source.id, Photo.account_id == current.account_id)
        .scalar()
        or 0
    )
    if err := ctbids_lot_photo_delta_error(current=n_target, delta=n_source):
        raise HTTPException(status_code=400, detail=err)

    note = (
        f"Merged lot {source.sku or source.id}: "
        f"{(source.auction_title or '').strip()[:200]}"
    ).strip()
    specs = json.loads(target.specification_lines_json or "[]")
    if not isinstance(specs, list):
        specs = []
    specs.append(note)
    target.specification_lines_json = json.dumps(specs, ensure_ascii=True)

    if body.prefer_source_sku and (source.sku or "").strip():
        target.sku = source.sku
        target.sku_needs_review = bool(source.sku_needs_review)
    elif not (target.sku or "").strip() and (source.sku or "").strip():
        target.sku = source.sku
        target.sku_needs_review = bool(source.sku_needs_review)

    db.execute(
        update(Photo)
        .where(Photo.lot_id == source.id, Photo.account_id == current.account_id)
        .values(lot_id=target.id, user_id=current.id)
    )
    db.delete(source)
    renormalize_photo_sort_orders(db, target.id, current.account_id)
    _reconcile_lot_photo_count(db, target.id, current.account_id)
    db.commit()
    db.refresh(target)
    recompute_sku_sanity_flags_for_sale(db, target.sale_id, current.account_id)
    db.commit()
    db.refresh(target)
    return _lot_out_for_id(db, current.account_id, target.id)


@app.get("/lots/{lot_id}/photos/{photo_id}/thumbnail")
def lot_photo_thumbnail(
    lot_id: str,
    photo_id: str,
    max_px: int = 320,
    token: str = Depends(oauth2_scheme),
) -> Response:
    """One catalog photo as JPEG (downscaled). Order matches ``GET …/photos`` and extension upload sequence."""
    # Cap high enough for CT Seller / marketplace re-uploads; still bounded to limit abuse.
    max_px = min(2048, max(64, int(max_px)))
    with SessionLocal() as db:
        user = user_from_access_token(token, db)
        row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == user.account_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Lot not found")
        ph = (
            db.query(Photo)
            .filter(
                Photo.id == photo_id,
                Photo.lot_id == lot_id,
                Photo.account_id == user.account_id,
            )
            .first()
        )
        if not ph:
            raise HTTPException(status_code=404, detail="Photo not found")
        src = resolve_catalog_storage_path(ph.storage_path)
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
        headers={"Cache-Control": "private, max-age=3600"},
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
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Photo not found")
    src = resolve_catalog_storage_path(row.storage_path)
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
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == current.account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
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
                src = resolve_catalog_storage_path(ph.storage_path)
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
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
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
    recompute_sku_sanity_flags_for_sale(db, row.sale_id, current.account_id)
    db.commit()
    db.refresh(row)
    has = lot_id in lot_ids_with_linked_photos(db, current.account_id, [lot_id])
    peer = sale_lot_photo_counts(db, row.sale_id, current.account_id)
    many = photo_count_is_sale_outlier(row.photo_count, peer)
    return lot_to_schema(row, has_thumbnail=has, many_photos_review=many)


@app.post("/lots/{lot_id}/ai-regenerate", response_model=LotOut)
def regenerate_lot_with_ai(
    lot_id: str,
    body: LotAiRegenerateIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LotOut:
    """
    Re-run catalog vision on this lot's image files (optionally a checked subset), merge like a single listing,
    then replace AI-derived fields on the existing row. Does not move photos.
    """
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    _assert_sale_editable_for_lot_moves(db, row.sale_id, current)
    try:
        run_lot_ai_regenerate_and_save(
            db,
            account_id=current.account_id,
            user_id=current.id,
            lot_id=lot_id,
            photo_ids=body.photo_ids if body.photo_ids else None,
            preserve_sku=body.preserve_sku,
        )
    except ValueError as exc:
        msg = str(exc)
        code = status.HTTP_503_SERVICE_UNAVAILABLE if "GOOGLE_API_KEY" in msg else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=msg) from exc
    return _lot_out_for_id(db, current.account_id, lot_id)


@app.delete("/lots/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lot(
    lot_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    sale_id = row.sale_id
    db.query(Photo).filter(
        Photo.lot_id == lot_id,
        Photo.account_id == current.account_id,
    ).delete()
    db.delete(row)
    db.commit()
    recompute_sku_sanity_flags_for_sale(db, sale_id, current.account_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/lots/purge-all", status_code=status.HTTP_204_NO_CONTENT)
def purge_all_lots(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Remove this user's lots, photos, jobs, and uploaded batch folders under their account (dev reset)."""
    _require_owner(current)
    aid = current.account_id
    upload_folders = [
        j.input_folder
        for j in db.query(ProcessingJob)
        .filter(ProcessingJob.account_id == aid)
        .all()
    ]
    for ex in db.query(SalePhotoZipExport).filter(SalePhotoZipExport.account_id == aid).all():
        if ex.zip_path:
            Path(ex.zip_path).unlink(missing_ok=True)
    db.query(SalePhotoZipExport).filter(SalePhotoZipExport.account_id == aid).delete()
    db.query(Photo).filter(Photo.account_id == aid).delete()
    db.query(Lot).filter(Lot.account_id == aid).delete()
    db.query(ProcessingJob).filter(ProcessingJob.account_id == aid).delete()
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
    if (
        bool(single_lot_mode)
        and CTBIDS_MAX_PHOTOS_PER_LOT is not None
        and len(files) > CTBIDS_MAX_PHOTOS_PER_LOT
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"CTBids allows at most {CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing; "
                f"this upload has {len(files)} files."
            ),
        )
    dest = new_batch_dir(current.account_id)
    await save_uploaded_files(dest, files)
    _manual_sku = (single_lot_manual_sku or "").strip() or None
    job = ProcessingJob(
        account_id=current.account_id,
        user_id=current.id,
        sale_id=sale.id,
        status=JobStatus.queued,
        input_folder=str(dest.resolve()),
        inherit_sku_from_subfolder=bool(inherit_sku_from_subfolder),
        single_lot_mode=bool(single_lot_mode),
        single_lot_manual_sku=_manual_sku,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_out(job)


def _queued_job_upload_folder(account_id: str, job: ProcessingJob) -> Path:
    """Resolve ``job.input_folder`` under this account's upload root, or raise."""
    root = (UPLOAD_ROOT / account_id).resolve()
    folder = Path(job.input_folder).resolve()
    try:
        folder.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid job upload path") from exc
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail="Job upload folder is missing")
    return folder


@app.post("/jobs/{job_id}/upload-files", response_model=JobOut)
async def append_files_to_queued_job(
    job_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    files: list[UploadFile] = File(),
) -> JobOut:
    """Append more images to a **queued** browser-upload job (same folder as the first ``/jobs/upload``)."""
    job = (
        db.query(ProcessingJob)
        .filter(ProcessingJob.id == job_id, ProcessingJob.account_id == current.account_id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.queued:
        raise HTTPException(
            status_code=409,
            detail="Job must be queued to add files (wait if it is still processing, or cancel first).",
        )
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    dest = _queued_job_upload_folder(current.account_id, job)
    if bool(getattr(job, "single_lot_mode", False)) and CTBIDS_MAX_PHOTOS_PER_LOT is not None:
        existing = count_catalog_image_files(dest)
        if existing + len(files) > CTBIDS_MAX_PHOTOS_PER_LOT:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"CTBids allows at most {CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing. "
                    f"This job already has {existing} image(s); adding {len(files)} would exceed the limit."
                ),
            )
    await save_uploaded_files(dest, files)
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

    q = db.query(ProcessingJob).filter(ProcessingJob.account_id == current.account_id)
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
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == JobStatus.failed:
        job.status = JobStatus.queued
        job.error_message = None
        job.cancel_requested = False
        db.commit()
        db.refresh(job)
    elif job.status != JobStatus.queued:
        raise HTTPException(status_code=409, detail="Job must be queued or failed to run")
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
        if folder.is_dir() and not upload_folder_has_linked_photos(db, current.account_id, folder):
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
) -> EventSourceResponse:
    """
    Server-Sent Events for one processing job. Auth: ``Authorization: Bearer`` or query ``access_token``
    (EventSource cannot set custom headers in all browsers).
    Events are delivered only on the channel ``(user_id, job_id)`` matching the authenticated user and this job.
    """
    # Short-lived session: do not use Depends(get_db) on this route — the response stays open for the whole
    # SSE stream and would pin a QueuePool connection until disconnect (same issue as get_current_user_sse).
    with SessionLocal() as db:
        job = (
            db.query(ProcessingJob)
            .filter(
                ProcessingJob.id == job_id,
                ProcessingJob.account_id == current.account_id,
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
    q = db.query(Lot).filter(Lot.account_id == current.account_id)
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
    thumb_ids = lot_ids_with_linked_photos(db, current.account_id, lot_ids)
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


def _sale_zip_export_out(row: SalePhotoZipExport) -> SaleZipExportOut:
    ready = bool(
        row.status == JobStatus.completed
        and row.zip_path
        and Path(row.zip_path).is_file()
    )
    return SaleZipExportOut(
        id=row.id,
        sale_id=row.sale_id,
        status=row.status.value,
        total_photos_expected=int(row.total_photos_expected),
        photo_files_written=int(row.photo_files_written),
        error_message=row.error_message,
        expires_at=row.expires_at,
        created_at=row.created_at,
        ready=ready,
    )


def _reconcile_stale_sale_zip_export(db: Session, row: SalePhotoZipExport) -> None:
    now = datetime.now(timezone.utc)
    if export_tasks.is_sale_zip_export_running(row.id):
        return
    if row.status == JobStatus.queued:
        created = row.created_at
        if created and (now - created).total_seconds() > 180:
            row.status = JobStatus.failed
            row.error_message = row.error_message or "Export never started; try again."
            db.commit()
        return
    if row.status != JobStatus.processing:
        return
    if not row.updated_at:
        return
    if (now - row.updated_at).total_seconds() > 1200:
        row.status = JobStatus.failed
        if not row.error_message:
            row.error_message = "Export stopped unexpectedly (server restart or crash). Start again."
        db.commit()


@app.post("/exports/sale-photos-zip", response_model=SaleZipExportOut)
def start_sale_photos_zip_export(
    sale_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaleZipExportOut:
    """Queue a background ZIP of all catalog photos for lots in this sale (600–3000+ images)."""
    sale = _sale_for_user(sale_id, current, db)
    cleanup_expired_sale_zip_exports(db, current.account_id)
    in_progress = (
        db.query(SalePhotoZipExport)
        .filter(
            SalePhotoZipExport.sale_id == sale.id,
            SalePhotoZipExport.account_id == current.account_id,
            SalePhotoZipExport.status.in_((JobStatus.queued, JobStatus.processing)),
        )
        .order_by(SalePhotoZipExport.created_at.desc())
        .first()
    )
    if in_progress is not None:
        _reconcile_stale_sale_zip_export(db, in_progress)
        db.refresh(in_progress)
        if in_progress.status in (JobStatus.queued, JobStatus.processing):
            return _sale_zip_export_out(in_progress)

    n = int(
        count_sale_catalog_photos(db, sale.id, current.account_id)
    )
    if n <= 0:
        raise HTTPException(status_code=400, detail="No catalog photos in this sale to export.")
    row = SalePhotoZipExport(
        account_id=current.account_id,
        user_id=current.id,
        sale_id=sale.id,
        status=JobStatus.queued,
        total_photos_expected=n,
        photo_files_written=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        export_tasks.start_sale_zip_export(row.id, current.account_id)
    except RuntimeError:
        row.status = JobStatus.failed
        row.error_message = "Another export thread conflict; try again."
        db.commit()
        db.refresh(row)
    return _sale_zip_export_out(row)


@app.get("/exports/sale-photos-zip/{export_id}", response_model=SaleZipExportOut)
def get_sale_photos_zip_export(
    export_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaleZipExportOut:
    row = (
        db.query(SalePhotoZipExport)
        .filter(
            SalePhotoZipExport.id == export_id,
            SalePhotoZipExport.account_id == current.account_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Export not found")
    _reconcile_stale_sale_zip_export(db, row)
    db.refresh(row)
    return _sale_zip_export_out(row)


@app.get("/exports/sale-photos-zip/{export_id}/download")
def download_sale_photos_zip_export(
    export_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    row = (
        db.query(SalePhotoZipExport)
        .filter(
            SalePhotoZipExport.id == export_id,
            SalePhotoZipExport.account_id == current.account_id,
        )
        .first()
    )
    if row is None or row.status != JobStatus.completed or not row.zip_path:
        raise HTTPException(status_code=404, detail="ZIP not ready or export not found")
    p = Path(row.zip_path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="ZIP file missing on disk")
    sale = db.query(Sale).filter(Sale.id == row.sale_id).first()
    slug = re.sub(r"[^\w\-]+", "_", (sale.name if sale else "sale")[:60]).strip("_") or "sale"
    return FileResponse(str(p), media_type="application/zip", filename=f"sale-{slug}-photos.zip")

