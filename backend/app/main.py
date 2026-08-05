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
from fastapi.responses import FileResponse, RedirectResponse
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
from app import appraisal_tasks, export_tasks, job_tasks, regeneration_tasks
from app.models import Account, AppraisalCheck, JobStatus, Lot, Photo, ProcessingJob, Sale, SalePhotoZipExport, User
from app.schemas import (
    CreateJobRequest,
    CTSellerCredentialsIn,
    CTSellerConditionOut,
    CTSellerRemoteSaleOut,
    CTSellerStatusOut,
    CatalogR2MigrateIn,
    CatalogR2MigrateOut,
    CatalogR2CorsOut,
    CatalogMediaUrlOut,
    SaleLotCoverUrlsOut,
    DeleteLotPhotosIn,
    HealthResponse,
    JobOut,
    LoginRequest,
    MergeLotsIn,
    LotOut,
    LotPostToCTSellerIn,
    LotPostToCTSellerOut,
    LotUpdate,
    PasswordChangeIn,
    PasswordResetIn,
    RegisterRequest,
    SaleCreate,
    SaleOut,
    SalePostToCTSellerIn,
    SalePostToCTSellerLotResult,
    SalePostToCTSellerOut,
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
    AppraisalCheckOut,
    AppraisalResultOut,
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
from app.services.ctseller import (
    CTSellerClient,
    CTSellerError,
    build_item_payload,
    seal_secret,
    unseal_secret,
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
from app.services.photo_roles import filter_photos_excluding_sku_tape
from app.services.sale_zip_export import cleanup_expired_sale_zip_exports, count_sale_catalog_photos
from app.services.thumbnail_image import thumbnail_jpeg_bytes, thumbnail_jpeg_bytes_first_available
from app.catalog_storage import (
    delete_catalog_photo,
    migrate_local_photos_to_r2,
    persist_catalog_photo,
    resolve_catalog_storage_path,
    signed_catalog_url,
    upload_root,
)
from app.object_storage import r2_configured as catalog_r2_configured
from app.appraisal_storage import persist_appraisal_photo_bytes, resolve_appraisal_storage_path
from app.services.image_preprocess import compress_upload_bytes_if_image
from app.upload_storage import UPLOAD_ROOT, new_batch_dir, save_uploaded_files
from app.catalog_limits import (
    CTBIDS_MAX_PHOTOS_PER_LOT,
    count_catalog_image_files,
    ctbids_lot_photo_delta_error,
    list_catalog_image_files_sorted,
)

app = FastAPI(title=settings.app_name)


def _sale_out(row: Sale) -> SaleOut:
    return SaleOut(
        id=row.id,
        account_id=row.account_id,
        user_id=row.user_id,
        name=row.name,
        closed_at=row.closed_at,
        ctseller_sale_id=getattr(row, "ctseller_sale_id", None),
        created_at=row.created_at,
    )


def _ctseller_client_for_account(account: Account, db: Session) -> CTSellerClient:
    """Return an authenticated CT Seller client, refreshing or re-logging in as needed."""
    access = unseal_secret(getattr(account, "ctseller_access_token_enc", None))
    refresh = unseal_secret(getattr(account, "ctseller_refresh_token_enc", None))
    username = (getattr(account, "ctseller_username", None) or "").strip()
    password = unseal_secret(getattr(account, "ctseller_password_enc", None))
    if not username or not password:
        raise HTTPException(
            status_code=400,
            detail="Connect your CT Seller account on Account → CT Seller first",
        )
    client = CTSellerClient(access_token=access or None)
    if access:
        try:
            client.list_open_sales()
            return client
        except CTSellerError:
            pass
    if refresh:
        try:
            new_access = client.refresh(refresh)
            account.ctseller_access_token_enc = seal_secret(new_access)
            db.add(account)
            db.commit()
            return client
        except CTSellerError:
            pass
    try:
        tokens = client.login(username, password)
    except CTSellerError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc) or "CT Seller login failed — update credentials on Account",
        ) from exc
    account.ctseller_access_token_enc = seal_secret(tokens.access_token)
    if tokens.refresh_token:
        account.ctseller_refresh_token_enc = seal_secret(tokens.refresh_token)
    db.add(account)
    db.commit()
    return client


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
    row = (
        db.query(Lot)
        .filter(Lot.id == lot_id, Lot.account_id == account_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    db.refresh(row)
    has = lot_id in lot_ids_with_linked_photos(db, account_id, [lot_id])
    peer = sale_lot_photo_counts(db, row.sale_id, account_id)
    many = photo_count_is_sale_outlier(row.photo_count, peer)
    out = lot_to_schema(row, has_thumbnail=has, many_photos_review=many)
    regen = regeneration_tasks.lot_regenerate_is_active(row)
    err = regeneration_tasks.pop_regeneration_error(db, row) if not regen else None
    if regen or err:
        return out.model_copy(update={"ai_regenerating": regen, "ai_regenerate_error": err})
    return out


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


def _count_catalog_image_files(max_scan: int = 200_000) -> int:
    """Bounded count of catalog image files under ``upload_root()`` (for ops / recovery checks)."""
    root = upload_root()
    if not root.is_dir():
        return 0
    exts = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
    n = 0
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in exts:
            n += 1
            if n >= max_scan:
                break
    return n


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    root = upload_root()
    return HealthResponse(
        status="ok",
        enable_appraiser=settings.enable_appraiser,
        appraiser_max_photos=settings.appraiser_max_photos,
        gemini_pro_model=settings.gemini_pro_model,
        gemini_flash_model=settings.gemini_flash_model,
        catalog_upload_root=str(root),
        catalog_image_files_on_disk=_count_catalog_image_files(),
        catalog_r2_configured=catalog_r2_configured(),
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


@app.post("/integrations/r2/migrate-catalog", response_model=CatalogR2MigrateOut)
def migrate_catalog_to_r2(
    payload: CatalogR2MigrateIn | None = None,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CatalogR2MigrateOut:
    """
    Copy existing local catalog photos into R2 (same keys). Run once after enabling R2
    while files still exist on the API disk/volume. Safe to re-run.
    """
    if not catalog_r2_configured():
        raise HTTPException(status_code=400, detail="R2 is not configured on this API")
    body = payload or CatalogR2MigrateIn()
    account_id = current.account_id
    if body.account_id and body.account_id != current.account_id:
        _require_master_admin(current)
        account_id = body.account_id.strip()
    rows = (
        db.query(Photo.id, Photo.storage_path)
        .filter(Photo.account_id == account_id)
        .order_by(Photo.created_at.asc())
        .all()
    )
    photo_rows = [(str(r.id), r.storage_path) for r in rows]
    try:
        result = migrate_local_photos_to_r2(
            photo_rows,
            limit=body.limit,
            offset=body.offset,
            skip_if_remote=True,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CatalogR2MigrateOut(
        uploaded=int(result["uploaded"]),
        skipped_already_in_r2=int(result["skipped_already_in_r2"]),
        skipped_missing_local=int(result["skipped_missing_local"]),
        failed=int(result["failed"]),
        errors=list(result.get("errors") or []),
        r2_configured=True,
        offset=int(result.get("offset") or 0),
        next_offset=result.get("next_offset"),  # type: ignore[arg-type]
        total_photos=int(result.get("total_photos") or 0),
        done=bool(result.get("done")),
    )


@app.get("/integrations/r2/ping")
def r2_ping(current: User = Depends(get_current_user)) -> dict[str, str]:
    """Verify R2 credentials can write to the configured bucket."""
    if not catalog_r2_configured():
        raise HTTPException(status_code=400, detail="R2 is not configured on this API")
    try:
        from app.object_storage import ping

        ping()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"R2 ping failed: {exc}") from exc
    return {"status": "ok"}


@app.get("/integrations/r2/cors", response_model=CatalogR2CorsOut)
def r2_cors_get(current: User = Depends(get_current_user)) -> CatalogR2CorsOut:
    """Show current R2 bucket CORS rules (empty if none)."""
    if not catalog_r2_configured():
        raise HTTPException(status_code=400, detail="R2 is not configured on this API")
    try:
        from app.object_storage import get_bucket_cors

        rules = get_bucket_cors()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not read R2 CORS: {exc}") from exc
    origins: list[str] = []
    for rule in rules:
        for o in rule.get("AllowedOrigins") or []:
            if o and o not in origins:
                origins.append(str(o))
    return CatalogR2CorsOut(origins=origins, rules=rules)


@app.post("/integrations/r2/cors", response_model=CatalogR2CorsOut)
def r2_cors_apply(current: User = Depends(get_current_user)) -> CatalogR2CorsOut:
    """
    Set R2 bucket CORS from ``CORS_ORIGINS`` (+ local Next.js defaults) so the web app
    can ``fetch()`` signed photo URLs for the viewer/crop tools.
    """
    if not catalog_r2_configured():
        raise HTTPException(status_code=400, detail="R2 is not configured on this API")
    role = (current.role or "").strip().lower()
    if role != "owner" and not is_master_admin_email(current.email):
        raise HTTPException(status_code=403, detail="Owner required")
    origins = cors_allow_origins()
    try:
        from app.object_storage import get_bucket_cors, put_bucket_cors

        applied = put_bucket_cors(origins)
        rules = get_bucket_cors()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail=(
                f"Could not set R2 CORS: {exc}. "
                "The API token may need bucket Admin / Edit CORS permission on Cloudflare."
            ),
        ) from exc
    return CatalogR2CorsOut(origins=applied, rules=rules)


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
    return [_sale_out(r) for r in rows]


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


@app.get("/sales/{sale_id}/lot-cover-urls", response_model=SaleLotCoverUrlsOut)
def sale_lot_cover_urls(
    sale_id: str,
    max_px: int = Query(160, ge=64, le=800, description="Long edge for cached JPEG thumb."),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaleLotCoverUrlsOut:
    """
    One round-trip of presigned R2 cover URLs for every lot in the sale.

    Used by the mobile Lots grid so the phone loads images from Cloudflare instead of
    N authenticated ``/lots/{id}/thumbnail`` hits through Railway.
    """
    if not catalog_r2_configured():
        raise HTTPException(status_code=400, detail="R2 is not configured")
    _sale_for_user(sale_id, current, db)
    lot_ids = [
        r[0]
        for r in db.query(Lot.id)
        .filter(Lot.sale_id == sale_id, Lot.account_id == current.account_id)
        .all()
    ]
    if not lot_ids:
        return SaleLotCoverUrlsOut(expires_in=3600, urls={})

    photos = (
        db.query(Photo)
        .filter(Photo.account_id == current.account_id, Photo.lot_id.in_(lot_ids))
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
        .all()
    )
    first_path: dict[str, str] = {}
    for ph in photos:
        lid = ph.lot_id
        if not lid or lid in first_path:
            continue
        first_path[lid] = ph.storage_path

    expires = 3600
    px = int(max_px)
    urls: dict[str, str] = {}
    for lid, storage_path in first_path.items():
        try:
            # Do not generate missing thumbs here — that blocked the mobile grid for minutes.
            urls[lid] = signed_catalog_url(
                storage_path,
                max_px=px,
                expires_seconds=expires,
                generate_if_missing=False,
            )
        except Exception:  # noqa: BLE001
            continue
    return SaleLotCoverUrlsOut(expires_in=expires, urls=urls)


@app.post("/sales", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
def create_sale(
    payload: SaleCreate, current: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> SaleOut:
    remote_id: int | None = None
    if payload.ctseller_sale_id is not None:
        remote_id = int(payload.ctseller_sale_id)
        if remote_id <= 0:
            raise HTTPException(status_code=400, detail="ctseller_sale_id must be a positive integer")
        existing = (
            db.query(Sale)
            .filter(Sale.account_id == current.account_id, Sale.ctseller_sale_id == remote_id)
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"CT Seller auction #{remote_id} is already mapped to sale \"{existing.name}\"",
            )
    row = Sale(
        account_id=current.account_id,
        user_id=current.id,
        name=payload.name.strip(),
        ctseller_sale_id=remote_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _sale_out(row)


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
    if payload.clear_ctseller_sale_id:
        row.ctseller_sale_id = None
    elif payload.ctseller_sale_id is not None:
        row.ctseller_sale_id = int(payload.ctseller_sale_id)
    db.commit()
    db.refresh(row)
    return _sale_out(row)


@app.post("/sales/{sale_id}/fill-blank-conditions")
def fill_blank_lot_conditions(
    sale_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """Set empty lot condition_text to CT Seller default GOOD for this sale."""
    from app.services.ctseller_conditions import CT_SELLER_CONDITION_DEFAULT

    sale = _sale_for_user(sale_id, current, db)
    rows = (
        db.query(Lot)
        .filter(Lot.account_id == current.account_id, Lot.sale_id == sale.id)
        .all()
    )
    updated = 0
    for lot in rows:
        if (lot.condition_text or "").strip():
            continue
        lot.condition_text = CT_SELLER_CONDITION_DEFAULT
        db.add(lot)
        updated += 1
    if updated:
        db.commit()
    return {"updated": updated, "total": len(rows)}


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


@app.get("/integrations/ctseller", response_model=CTSellerStatusOut)
def ctseller_status(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CTSellerStatusOut:
    account = db.query(Account).filter(Account.id == current.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    username = (getattr(account, "ctseller_username", None) or "").strip() or None
    has_pw = bool(unseal_secret(getattr(account, "ctseller_password_enc", None)))
    return CTSellerStatusOut(connected=bool(username and has_pw), username=username)


@app.put("/integrations/ctseller/credentials", response_model=CTSellerStatusOut)
def ctseller_connect(
    payload: CTSellerCredentialsIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CTSellerStatusOut:
    """Store CT Seller login (encrypted) after verifying credentials."""
    _require_owner(current)
    account = db.query(Account).filter(Account.id == current.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    client = CTSellerClient()
    try:
        tokens = client.login(payload.username.strip(), payload.password)
    except CTSellerError as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "CT Seller login failed") from exc
    account.ctseller_username = payload.username.strip()
    account.ctseller_password_enc = seal_secret(payload.password)
    account.ctseller_access_token_enc = seal_secret(tokens.access_token)
    account.ctseller_refresh_token_enc = (
        seal_secret(tokens.refresh_token) if tokens.refresh_token else None
    )
    db.add(account)
    db.commit()
    return CTSellerStatusOut(connected=True, username=account.ctseller_username)


@app.delete("/integrations/ctseller/credentials", response_model=CTSellerStatusOut)
def ctseller_disconnect(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CTSellerStatusOut:
    _require_owner(current)
    account = db.query(Account).filter(Account.id == current.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    account.ctseller_username = None
    account.ctseller_password_enc = None
    account.ctseller_access_token_enc = None
    account.ctseller_refresh_token_enc = None
    db.add(account)
    db.commit()
    return CTSellerStatusOut(connected=False, username=None)


@app.get("/integrations/ctseller/conditions", response_model=list[CTSellerConditionOut])
def ctseller_list_conditions(
    current: User = Depends(get_current_user),
) -> list[CTSellerConditionOut]:
    """CT Seller Item Condition dropdown values (public static catalog)."""
    from app.services.ctseller_conditions import ct_condition_rows

    _ = current  # auth required
    return [CTSellerConditionOut(**row) for row in ct_condition_rows()]


@app.get("/integrations/ctseller/sales", response_model=list[CTSellerRemoteSaleOut])
def ctseller_list_sales(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CTSellerRemoteSaleOut]:
    account = db.query(Account).filter(Account.id == current.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        client = _ctseller_client_for_account(account, db)
        rows = client.list_open_sales()
    except CTSellerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [
        CTSellerRemoteSaleOut(
            id=r.id,
            title=r.title,
            status=r.status,
            starttime=r.starttime,
            endtime=r.endtime,
        )
        for r in rows
    ]


def _post_one_lot_to_ctseller(
    *,
    db: Session,
    lot: Lot,
    client: CTSellerClient,
    remote_sale_id: int,
    location_id: object,
    item_receipt_method: object,
    force: bool = False,
    allowed_areas: list[str] | None = None,
    tax: object = None,
    buyers_premium: object = None,
) -> LotPostToCTSellerOut:
    """Create one CT Seller item + photos for a catalog lot. Raises HTTPException or CTSellerError."""
    if lot.ctseller_item_id and not force:
        raise HTTPException(
            status_code=409,
            detail=f"Already posted to CT Seller as item {lot.ctseller_item_id}. Pass force=true to post again.",
        )
    sku = (lot.sku or "").strip()
    if not sku:
        raise HTTPException(status_code=400, detail="Lot needs a SKU before posting to CT Seller")
    if not (lot.auction_title or "").strip():
        raise HTTPException(status_code=400, detail="Lot needs an auction title before posting")

    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot.id, Photo.account_id == lot.account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc())
        .all()
    )
    listing_photos = filter_photos_excluding_sku_tape(photos)
    if photos and not listing_photos:
        raise HTTPException(
            status_code=400,
            detail=(
                "Only SKU tape photos on this lot — add a product photo before posting to CT Seller"
            ),
        )
    photo_paths: list[tuple[Photo, Path]] = []
    for ph in listing_photos:
        path = resolve_catalog_storage_path(ph.storage_path)
        if path is not None and path.is_file():
            photo_paths.append((ph, path))
    if listing_photos and not photo_paths:
        raise HTTPException(
            status_code=400,
            detail="Lot photos are missing on disk — cannot post to CT Seller",
        )

    areas = allowed_areas
    if areas is None:
        try:
            areas = client.list_areas()
        except CTSellerError:
            areas = None

    material: str | None = None
    try:
        specs = json.loads(lot.specification_lines_json or "[]")
        if isinstance(specs, list):
            for line in specs:
                s = str(line or "").strip()
                if s.lower().startswith("material:"):
                    material = s.split(":", 1)[-1].strip() or None
                    break
    except (TypeError, ValueError, json.JSONDecodeError):
        material = None

    item = build_item_payload(
        sale_id=int(remote_sale_id),
        location_id=location_id,
        sku=sku,
        title=lot.auction_title or sku,
        description=lot.professional_description or "",
        category=(lot.ct_category or "").strip() or "Household Goods Other",
        condition=(lot.condition_text or "").strip() or "GOOD",
        starting_price=lot.starting_price,
        item_receipt_method=item_receipt_method,  # type: ignore[arg-type]
        allowed_areas=areas,
        tax=tax if tax is not None else None,
        buyers_premium=buyers_premium if buyers_premium is not None else None,
        material=material,
    )
    item_id = client.create_item(item)
    uploaded = 0
    for idx, (ph, path) in enumerate(photo_paths):
        raw = path.read_bytes()
        name = Path(ph.original_name or path.name).stem or f"image{idx + 1}"
        client.add_item_image(
            item_id=item_id,
            image_bytes=raw,
            name=name[:80],
            display_order=idx + 1,
            is_display_image=(idx == 0),
        )
        uploaded += 1

    now = datetime.now(timezone.utc)
    lot.ctseller_item_id = int(item_id)
    lot.ctseller_posted_at = now
    db.add(lot)
    db.commit()
    db.refresh(lot)

    return LotPostToCTSellerOut(
        lot_id=lot.id,
        ctseller_item_id=int(item_id),
        ctseller_sale_id=int(remote_sale_id),
        photos_uploaded=uploaded,
        ctseller_posted_at=now,
    )


@app.post("/lots/{lot_id}/post-to-ctseller", response_model=LotPostToCTSellerOut)
def post_lot_to_ctseller(
    lot_id: str,
    payload: LotPostToCTSellerIn | None = None,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LotPostToCTSellerOut:
    """
    Create the lot as an item on seller.ctbids.com (title, description, category, photos).

    Uses unofficial CT Seller APIs — same endpoints the Seller SPA uses. Requires CT Seller
    credentials on the account and a mapped CT Seller sale id.
    """
    body = payload or LotPostToCTSellerIn()
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")

    sale = _sale_for_user(lot.sale_id, current, db)
    remote_sale_id = body.ctseller_sale_id if body.ctseller_sale_id is not None else sale.ctseller_sale_id
    if remote_sale_id is None:
        raise HTTPException(
            status_code=400,
            detail="Map this sale to a CT Seller auction (pick a sale when posting, or set ctseller_sale_id on the sale)",
        )

    account = db.query(Account).filter(Account.id == current.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    try:
        client = _ctseller_client_for_account(account, db)
        detail = client.sale_detail(int(remote_sale_id))
        out = _post_one_lot_to_ctseller(
            db=db,
            lot=lot,
            client=client,
            remote_sale_id=int(remote_sale_id),
            location_id=detail.get("locationid"),
            item_receipt_method=detail.get("saleitemreceiptmethod"),
            force=body.force,
            tax=detail.get("tax"),
            buyers_premium=detail.get("buyerspremium"),
        )
    except CTSellerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read lot photos: {exc}") from exc

    if body.save_sale_mapping and sale.ctseller_sale_id != int(remote_sale_id):
        sale.ctseller_sale_id = int(remote_sale_id)
        db.add(sale)
        db.commit()

    return out


@app.post("/sales/{sale_id}/post-to-ctseller", response_model=SalePostToCTSellerOut)
def post_sale_lots_to_ctseller(
    sale_id: str,
    payload: SalePostToCTSellerIn | None = None,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SalePostToCTSellerOut:
    """
    Bulk-post catalog lots in this sale to CT Seller (sequential).

    Default: lots that have SKU + title and are not yet posted (``only_unposted``).
    Up to ``limit`` lots per request (max 100). Already-posted lots are skipped unless ``force``.
    """
    body = payload or SalePostToCTSellerIn()
    sale = _sale_for_user(sale_id, current, db)
    remote_sale_id = body.ctseller_sale_id if body.ctseller_sale_id is not None else sale.ctseller_sale_id
    if remote_sale_id is None:
        raise HTTPException(
            status_code=400,
            detail="Pick a CT Seller auction (ctseller_sale_id) or map it on this sale first",
        )

    account = db.query(Account).filter(Account.id == current.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    q = db.query(Lot).filter(Lot.account_id == current.account_id, Lot.sale_id == sale.id)
    if body.lot_ids:
        wanted = {x.strip() for x in body.lot_ids if (x or "").strip()}
        q = q.filter(Lot.id.in_(wanted))
    elif body.only_unposted and not body.force:
        q = q.filter(Lot.ctseller_item_id.is_(None))
    lots = q.order_by(Lot.created_at.asc()).all()
    if body.lot_ids:
        by_id = {r.id: r for r in lots}
        lots = [by_id[i] for i in body.lot_ids if i in by_id]

    try:
        client = _ctseller_client_for_account(account, db)
        detail = client.sale_detail(int(remote_sale_id))
        location_id = detail.get("locationid")
        irm = detail.get("saleitemreceiptmethod")
        try:
            allowed_areas = client.list_areas()
        except CTSellerError:
            allowed_areas = None
    except CTSellerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if body.save_sale_mapping and sale.ctseller_sale_id != int(remote_sale_id):
        sale.ctseller_sale_id = int(remote_sale_id)
        db.add(sale)
        db.commit()

    results: list[SalePostToCTSellerLotResult] = []
    posted = failed = skipped = 0
    processed = 0

    for lot in lots:
        if processed >= body.limit:
            break
        sku = (lot.sku or "").strip() or None
        already = bool(lot.ctseller_item_id)
        if already and not body.force:
            results.append(
                SalePostToCTSellerLotResult(
                    lot_id=lot.id,
                    sku=sku,
                    ok=False,
                    skipped=True,
                    error=f"Already posted as item {lot.ctseller_item_id}",
                    ctseller_item_id=lot.ctseller_item_id,
                )
            )
            skipped += 1
            continue
        if not sku or not (lot.auction_title or "").strip():
            results.append(
                SalePostToCTSellerLotResult(
                    lot_id=lot.id,
                    sku=sku,
                    ok=False,
                    skipped=True,
                    error="Missing SKU or auction title",
                )
            )
            skipped += 1
            continue

        processed += 1
        try:
            out = _post_one_lot_to_ctseller(
                db=db,
                lot=lot,
                client=client,
                remote_sale_id=int(remote_sale_id),
                location_id=location_id,
                item_receipt_method=irm,
                force=body.force,
                allowed_areas=allowed_areas,
                tax=detail.get("tax"),
                buyers_premium=detail.get("buyerspremium"),
            )
            results.append(
                SalePostToCTSellerLotResult(
                    lot_id=lot.id,
                    sku=sku,
                    ok=True,
                    ctseller_item_id=out.ctseller_item_id,
                    photos_uploaded=out.photos_uploaded,
                )
            )
            posted += 1
        except HTTPException as exc:
            detail_msg = exc.detail
            msg = detail_msg if isinstance(detail_msg, str) else str(detail_msg)
            results.append(
                SalePostToCTSellerLotResult(lot_id=lot.id, sku=sku, ok=False, error=msg)
            )
            failed += 1
        except CTSellerError as exc:
            results.append(
                SalePostToCTSellerLotResult(lot_id=lot.id, sku=sku, ok=False, error=str(exc))
            )
            failed += 1
        except OSError as exc:
            results.append(
                SalePostToCTSellerLotResult(
                    lot_id=lot.id, sku=sku, ok=False, error=f"Photo read failed: {exc}"
                )
            )
            failed += 1

    return SalePostToCTSellerOut(
        ctseller_sale_id=int(remote_sale_id),
        posted=posted,
        failed=failed,
        skipped=skipped,
        results=results,
    )


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


@app.get("/lots/{lot_id}/media-url", response_model=CatalogMediaUrlOut)
def lot_media_url(
    lot_id: str,
    max_px: int = Query(220, description="Long edge for cached JPEG thumb (required for lot cover)."),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CatalogMediaUrlOut:
    """Presigned R2 URL for the lot's first photo thumbnail (dashboard covers)."""
    if not catalog_r2_configured():
        raise HTTPException(status_code=400, detail="R2 is not configured")
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == current.account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
        .all()
    )
    if not photos:
        raise HTTPException(status_code=404, detail="No image linked to this lot")
    expires = 3600
    px = min(800, max(64, int(max_px)))
    last_err: Exception | None = None
    for ph in photos:
        try:
            url = signed_catalog_url(ph.storage_path, max_px=px, expires_seconds=expires)
            return CatalogMediaUrlOut(url=url, expires_in=expires, kind="thumb")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    detail = str(last_err) if last_err else "Image file missing"
    raise HTTPException(status_code=404, detail=detail)


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
        storage_paths = [p.storage_path for p in photos]
        paths = [resolve_catalog_storage_path(p.storage_path) for p in photos]

    if catalog_r2_configured():
        for sp in storage_paths:
            try:
                url = signed_catalog_url(sp, max_px=max_px, expires_seconds=3600)
                return RedirectResponse(url=url, status_code=302)
            except Exception:
                continue

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
        headers={"Cache-Control": "private, no-cache"},
    )


@app.get("/lots/{lot_id}", response_model=LotOut)
def get_lot(lot_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LotOut:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    return _lot_out_for_id(db, current.account_id, lot_id)


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
        photo_id, storage_path = persist_catalog_photo(current.account_id, path)
        db.add(
            Photo(
                id=photo_id,
                account_id=current.account_id,
                user_id=current.id,
                lot_id=lot_id,
                original_name=path.name,
                storage_path=storage_path,
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

    storage_paths = [ph.storage_path for ph in to_delete]
    for ph in to_delete:
        db.delete(ph)
    renormalize_photo_sort_orders(db, lot_id, current.account_id)
    _reconcile_lot_photo_count(db, lot_id, current.account_id)
    db.commit()
    for sp in storage_paths:
        delete_catalog_photo(sp)
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

    # Source keeps remaining photos but often the old AI title (e.g. muskets) no longer matches
    # what is left (e.g. jewelry). Force a review placeholder instead of a stale listing.
    source.auction_title = (
        f"Needs retitle after split ({source.sku.strip()})"
        if (source.sku or "").strip()
        else "Needs retitle after split"
    )[:500]
    source.professional_description = ""
    source.sku_needs_review = True
    src_specs = json.loads(source.specification_lines_json or "[]")
    if not isinstance(src_specs, list):
        src_specs = []
    note = (
        "Photos were split into another listing — regenerate AI or rewrite title/description "
        "to match the remaining photos."
    )
    if note not in src_specs:
        src_specs.append(note)
    source.specification_lines_json = json.dumps(src_specs, ensure_ascii=True)

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

    # Append source photos after the target gallery — do not keep their old 0..n indices
    # (renormalize would interleave and put a jewelry close-up first while title stays muskets).
    mx = db.query(func.max(Photo.sort_order)).filter(
        Photo.lot_id == target.id, Photo.account_id == current.account_id
    ).scalar()
    base = (int(mx) if mx is not None else -1) + 1
    source_photos = (
        db.query(Photo)
        .filter(Photo.lot_id == source.id, Photo.account_id == current.account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
        .all()
    )
    for slot, ph in enumerate(source_photos):
        ph.lot_id = target.id
        ph.user_id = current.id
        ph.sort_order = base + slot

    db.delete(source)
    renormalize_photo_sort_orders(db, target.id, current.account_id)
    _reconcile_lot_photo_count(db, target.id, current.account_id)
    db.commit()
    db.refresh(target)
    recompute_sku_sanity_flags_for_sale(db, target.sale_id, current.account_id)
    db.commit()
    db.refresh(target)
    return _lot_out_for_id(db, current.account_id, target.id)


@app.get("/lots/{lot_id}/photos/{photo_id}/media-url", response_model=CatalogMediaUrlOut)
def lot_photo_media_url(
    lot_id: str,
    photo_id: str,
    max_px: int | None = Query(
        None,
        description="When set, return a signed URL for a cached JPEG thumbnail at this long edge.",
    ),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CatalogMediaUrlOut:
    """
    Presigned R2 URL for the photo (or a cached thumb). Browsers load from R2 — no Railway image egress.
    Falls back to 400 when R2 is not configured (use /thumbnail or /download instead).
    """
    if not catalog_r2_configured():
        raise HTTPException(status_code=400, detail="R2 is not configured")
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    ph = (
        db.query(Photo)
        .filter(
            Photo.id == photo_id,
            Photo.lot_id == lot_id,
            Photo.account_id == current.account_id,
        )
        .first()
    )
    if not ph:
        raise HTTPException(status_code=404, detail="Photo not found")
    expires = 3600
    try:
        px = None if max_px is None else min(2048, max(64, int(max_px)))
        url = signed_catalog_url(ph.storage_path, max_px=px, expires_seconds=expires)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "Photo file missing") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not sign media URL: {exc}") from exc
    return CatalogMediaUrlOut(
        url=url,
        expires_in=expires,
        kind="thumb" if max_px is not None else "original",
    )


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
        storage_path = ph.storage_path
        src = resolve_catalog_storage_path(ph.storage_path)

    if catalog_r2_configured():
        try:
            url = signed_catalog_url(storage_path, max_px=max_px, expires_seconds=3600)
            return RedirectResponse(url=url, status_code=302)
        except Exception:
            pass  # fall through to local bytes

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
        headers={"Cache-Control": "private, no-cache, must-revalidate"},
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


@app.post("/lots/{lot_id}/photos/{photo_id}/replace", response_model=LotPhotoOut)
async def replace_lot_photo(
    lot_id: str,
    photo_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    file: UploadFile = File(),
) -> LotPhotoOut:
    """Overwrite one catalog photo file (after crop/rotate in the lot details viewer)."""
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    _assert_sale_editable_for_lot_moves(db, lot.sale_id, current)
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
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty image file")
    try:
        body, stor_name = await asyncio.to_thread(
            compress_upload_bytes_if_image,
            raw,
            row.original_name or file.filename or "photo.jpg",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    tmp_path = Path(tempfile.mkstemp(suffix=Path(stor_name).suffix or ".jpg")[1])
    try:
        tmp_path.write_bytes(body)
        _, storage_path = persist_catalog_photo(current.account_id, tmp_path, photo_id=row.id)
        row.storage_path = storage_path
        if stor_name:
            row.original_name = Path(stor_name).name
        db.commit()
        db.refresh(row)
    finally:
        tmp_path.unlink(missing_ok=True)
    return lot_photo_out(row, lot_id=lot_id)


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
        from app.services.ctseller_conditions import canonicalize_ct_condition

        raw = (patch.condition_text or "").strip()
        row.condition_text = canonicalize_ct_condition(raw) if raw else ""
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
    if regeneration_tasks.is_regenerating(db, lot_id=lot_id, account_id=current.account_id):
        raise HTTPException(status_code=409, detail="AI regenerate is already running for this lot.")
    try:
        regeneration_tasks.mark_lot_regenerate_started(
            db,
            lot_id=lot_id,
            account_id=current.account_id,
        )
    except RuntimeError as exc:
        if str(exc) == "regenerate_already_running":
            raise HTTPException(status_code=409, detail="AI regenerate is already running for this lot.") from exc
        raise
    except ValueError as exc:
        msg = str(exc)
        code = status.HTTP_503_SERVICE_UNAVAILABLE if "GOOGLE_API_KEY" in msg else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=msg) from exc
    try:
        regeneration_tasks.start_lot_regeneration_in_background(
            lot_id=lot_id,
            account_id=current.account_id,
            user_id=current.id,
            photo_ids=body.photo_ids if body.photo_ids else None,
            preserve_sku=body.preserve_sku,
        )
    except RuntimeError as exc:
        regeneration_tasks.finish_lot_regenerate(
            db,
            lot_id=lot_id,
            account_id=current.account_id,
            error=str(exc)[:2000] if str(exc) != "regenerate_already_running" else None,
        )
        if str(exc) == "regenerate_already_running":
            raise HTTPException(status_code=409, detail="AI regenerate is already running for this lot.") from exc
        raise
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


def _purge_catalog_data(
    db: Session,
    *,
    account_id: str,
    sale_id: str | None = None,
) -> None:
    """
    Delete lots, photos, jobs, and sale ZIP exports for an account or one sale.

    Does not delete ``Sale`` rows. Does **not** remove catalog JPEGs on disk — photos are stored
    inside job upload batch folders; deleting those folders would permanently destroy images even
    when Postgres is restored from backup.
    """
    lot_filter = [Lot.account_id == account_id]
    job_filter = [ProcessingJob.account_id == account_id]
    zip_filter = [SalePhotoZipExport.account_id == account_id]
    if sale_id is not None:
        lot_filter.append(Lot.sale_id == sale_id)
        job_filter.append(ProcessingJob.sale_id == sale_id)
        zip_filter.append(SalePhotoZipExport.sale_id == sale_id)

    lot_ids = [row[0] for row in db.query(Lot.id).filter(*lot_filter).all()]

    for ex in db.query(SalePhotoZipExport).filter(*zip_filter).all():
        if ex.zip_path:
            Path(ex.zip_path).unlink(missing_ok=True)
    db.query(SalePhotoZipExport).filter(*zip_filter).delete(synchronize_session=False)

    if lot_ids:
        db.query(Photo).filter(
            Photo.account_id == account_id,
            Photo.lot_id.in_(lot_ids),
        ).delete(synchronize_session=False)
    elif sale_id is None:
        db.query(Photo).filter(Photo.account_id == account_id).delete(synchronize_session=False)

    db.query(Lot).filter(*lot_filter).delete(synchronize_session=False)
    db.query(ProcessingJob).filter(*job_filter).delete(synchronize_session=False)
    db.commit()


@app.post("/sales/{sale_id}/purge-catalog", status_code=status.HTTP_204_NO_CONTENT)
def purge_sale_catalog(
    sale_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Remove lots, photos, and jobs for one sale only (other sales unchanged)."""
    _require_owner(current)
    _sale_for_user(sale_id, current, db)
    _purge_catalog_data(db, account_id=current.account_id, sale_id=sale_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/lots/purge-all", status_code=status.HTTP_204_NO_CONTENT)
def purge_all_lots(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Remove all lots, photos, and jobs for this account across every sale (dev / full reset)."""
    _require_owner(current)
    _purge_catalog_data(db, account_id=current.account_id, sale_id=None)
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
    changed = job_tasks.reconcile_stale_processing_jobs(db, current.account_id)
    if changed:
        db.commit()
    job_tasks.kick_queue_if_idle_async(current.account_id)

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
    job_tasks.kick_queue_if_idle(current.account_id)
    started = False
    try:
        started = job_tasks.start_job_in_background(job.id, current.account_id)
    except RuntimeError as exc:
        if str(exc) != "job_already_running":
            raise
        job_tasks.reconcile_memory_threads_for_account(current.account_id)
        try:
            started = job_tasks.start_job_in_background(job.id, current.account_id)
        except RuntimeError as retry_exc:
            if str(retry_exc) != "job_already_running":
                raise
            raise HTTPException(status_code=409, detail="Job is already running") from retry_exc
    if not started:
        job_tasks.kick_queue_if_idle(current.account_id)
        db.refresh(job)
        if job.status == JobStatus.processing:
            started = True
        else:
            return _job_out(job)
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
    woke = job_tasks.request_cancel(job_id)
    if not woke and not job_tasks.is_job_running(job_id):
        job.status = JobStatus.failed
        job.error_message = "Cancelled"
        job.cancel_requested = False
        db.commit()
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
    Events are delivered on the channel ``(account_id, job_id)`` so any authenticated user on the
    account can watch the same sale's catalog job (e.g. office dashboard while field staff upload).
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

    q = job_events.subscribe(current.account_id, job_id)

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
            job_events.unsubscribe(current.account_id, job_id, q)

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


def _require_appraiser_enabled() -> None:
    if not settings.enable_appraiser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Appraiser mode is not enabled on this server.",
        )


def _appraisal_result_from_json(raw: str | None) -> AppraisalResultOut | None:
    if not raw:
        return None
    try:
        return AppraisalResultOut.model_validate(json.loads(raw))
    except Exception:
        return None


def _appraisal_check_out(row: AppraisalCheck) -> AppraisalCheckOut:
    paths = json.loads(row.photo_paths_json or "[]")
    st = row.status.value if isinstance(row.status, JobStatus) else str(row.status)
    return AppraisalCheckOut(
        id=row.id,
        sale_id=row.sale_id,
        status=st,
        scene_note=row.scene_note,
        photo_count=len(paths),
        result=_appraisal_result_from_json(row.result_json),
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _maybe_restart_queued_appraisal(row: AppraisalCheck, account_id: str) -> None:
    if row.status != JobStatus.queued:
        return
    if appraisal_tasks.is_appraisal_check_running(row.id):
        return
    try:
        appraisal_tasks.start_appraisal_check(row.id, account_id)
    except RuntimeError:
        pass


@app.get("/appraiser/checks", response_model=list[AppraisalCheckOut])
def list_appraisal_checks(
    sale_id: str = Query(..., min_length=1),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AppraisalCheckOut]:
    _require_appraiser_enabled()
    _sale_for_user(sale_id, current, db)
    rows = (
        db.query(AppraisalCheck)
        .filter(AppraisalCheck.account_id == current.account_id, AppraisalCheck.sale_id == sale_id)
        .order_by(AppraisalCheck.created_at.desc())
        .limit(200)
        .all()
    )
    for row in rows:
        _maybe_restart_queued_appraisal(row, current.account_id)
    return [_appraisal_check_out(r) for r in rows]


@app.post("/appraiser/checks", response_model=AppraisalCheckOut, status_code=status.HTTP_201_CREATED)
async def create_appraisal_check(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    sale_id: str = Form(...),
    scene_note: str = Form(""),
    files: list[UploadFile] = File(...),
) -> AppraisalCheckOut:
    _require_appraiser_enabled()
    sale = _sale_for_user(sale_id, current, db)
    max_photos = settings.appraiser_max_photos
    if not files:
        raise HTTPException(status_code=400, detail="At least one photo is required.")
    if len(files) > max_photos:
        raise HTTPException(
            status_code=400,
            detail=f"Appraiser allows at most {max_photos} photos per check; received {len(files)}.",
        )

    check_id = str(uuid.uuid4())
    paths: list[str] = []
    names: list[str] = []
    for i, upload in enumerate(files):
        raw = await upload.read()
        if not raw:
            raise HTTPException(status_code=400, detail=f"Empty image file: {upload.filename or i + 1}")
        orig = upload.filename or f"photo-{i + 1}.jpg"
        try:
            body, stor_name = await asyncio.to_thread(compress_upload_bytes_if_image, raw, orig)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        suffix = Path(stor_name or orig).suffix or ".jpg"
        key = persist_appraisal_photo_bytes(
            current.account_id,
            check_id,
            index=i,
            data=body,
            suffix=suffix,
        )
        paths.append(key)
        names.append(Path(stor_name or orig).name)

    row = AppraisalCheck(
        id=check_id,
        account_id=current.account_id,
        user_id=current.id,
        sale_id=sale.id,
        status=JobStatus.queued,
        scene_note=(scene_note or "").strip() or None,
        photo_paths_json=json.dumps(paths),
        original_names_json=json.dumps(names),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        appraisal_tasks.start_appraisal_check(row.id, current.account_id)
    except RuntimeError as exc:
        row.status = JobStatus.failed
        row.error_message = str(exc)
        db.commit()
        db.refresh(row)
    return _appraisal_check_out(row)


@app.get("/appraiser/checks/{check_id}", response_model=AppraisalCheckOut)
def get_appraisal_check(
    check_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AppraisalCheckOut:
    _require_appraiser_enabled()
    row = (
        db.query(AppraisalCheck)
        .filter(AppraisalCheck.id == check_id, AppraisalCheck.account_id == current.account_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Appraisal check not found")
    _maybe_restart_queued_appraisal(row, current.account_id)
    db.refresh(row)
    return _appraisal_check_out(row)


@app.get("/appraiser/checks/{check_id}/photos/{photo_index}/thumbnail")
def appraisal_check_photo_thumbnail(
    check_id: str,
    photo_index: int,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    max_px: int = Query(480, ge=64, le=1200),
) -> Response:
    _require_appraiser_enabled()
    row = (
        db.query(AppraisalCheck)
        .filter(AppraisalCheck.id == check_id, AppraisalCheck.account_id == current.account_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Appraisal check not found")
    paths = json.loads(row.photo_paths_json or "[]")
    if photo_index < 0 or photo_index >= len(paths):
        raise HTTPException(status_code=404, detail="Photo not found")
    src = resolve_appraisal_storage_path(paths[photo_index])
    if not src.is_file():
        raise HTTPException(status_code=404, detail="Photo file missing")
    try:
        body = thumbnail_jpeg_bytes(src, max_px)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Thumbnail generation failed") from exc
    return Response(content=body, media_type="image/jpeg")

