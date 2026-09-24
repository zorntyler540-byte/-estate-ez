from __future__ import annotations

import asyncio
import csv
import io
import os
import json
import logging
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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, nullslast, or_, select, update
from sqlalchemy.exc import TimeoutError as SATimeoutError
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
from app import appraisal_tasks, audience_tasks, export_tasks, job_tasks, regeneration_tasks
from app.models import (
    Account,
    AppraisalCheck,
    AudienceHuntEvent,
    JobSseEvent,
    JobStatus,
    Lot,
    LotVideo,
    Photo,
    ProcessingJob,
    Sale,
    SalePhotoZipExport,
    User,
)
from app.schemas import (
    CreateJobRequest,
    CTSellerCredentialsIn,
    CTSellerConditionOut,
    CTSellerRemoteSaleOut,
    CtsellerSaleSyncOut,
    CtSellerHistorySyncOut,
    CTSellerPostedAuctionOut,
    CTSellerPostedAuctionsOut,
    CTSellerPostedRefreshIn,
    CTSellerStatusOut,
    CatalogR2MigrateIn,
    CatalogR2MigrateOut,
    CatalogR2CorsOut,
    CatalogMediaUrlOut,
    SaleLotCoverUrlsOut,
    DeleteLotPhotosIn,
    RotateLotPhotosIn,
    RotateLotPhotosOut,
    HealthResponse,
    JobOut,
    LoginRequest,
    MergeLotsIn,
    LotOut,
    LotPostToCTSellerIn,
    LotPostToCTSellerOut,
    LotSearchTagsOut,
    LotMoveIn,
    LotBulkMoveIn,
    LotBulkMoveOut,
    LotUpdate,
    PasswordChangeIn,
    PasswordResetIn,
    RegisterRequest,
    SaleCreate,
    SaleMergeIn,
    SaleMergeOut,
    RemoveSaleIn,
    RemoveSaleOut,
    SaleCoverCandidateOut,
    SaleCoverCandidatesOut,
    SaleCoverPhotoSlotOut,
    SaleCoverPhotosOut,
    SaleCoverPhotosPushIn,
    SaleCoverPhotosPushOut,
    SaleCoverPhotosSelectIn,
    SaleCoverRemoteOut,
    SaleOut,
    SalePostToCTSellerIn,
    SalePostToCTSellerLotResult,
    SalePostToCTSellerOut,
    SaleReservePriceIn,
    SaleReservePriceLotResult,
    SaleReservePriceOut,
    SaleUpdate,
    SuggestionIn,
    SuggestionSavedOut,
    TokenResponse,
    TeamMemberCreate,
    TeamMemberOut,
    TeamActivityLotOut,
    TeamActivityMemberOut,
    TeamActivityOut,
    TeamActivityPhotoTimeOut,
    TeamActivityTotalsOut,
    SoldDocketItemOut,
    SoldCategoryRollupOut,
    AdminAccountOut,
    AdminAccountUpdateIn,
    AdminUsageAccountOut,
    AdminUsageSummaryOut,
    AppraisalCheckOut,
    AppraisalResultOut,
    UserSummary,
    LotAiRegenerateIn,
    LotAudienceHuntIn,
    SaleAudienceHuntIn,
    SaleAudienceHuntOut,
    SaleAudienceHuntLotResult,
    SaleAudienceHuntStatusOut,
    LotPhotoOut,
    LotVideoOut,
    DeleteLotVideosIn,
    ReorderLotPhotosIn,
    CatalogedMobileDraftLotOut,
    CatalogedMobileDraftsOut,
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
    access_token_should_refresh,
    build_item_payload,
    parse_sale_image_secondary_urls,
    persist_ctseller_tokens,
    pick_profile_sale_image,
    resolve_lot_reserve_price,
    seal_secret,
    unseal_secret,
)
from app.services.ctseller_posted import (
    posted_auction_counts,
    posted_outcome_counts,
    refresh_lot_ctseller_sale_ids,
)
from app.services import job_events
from app.services.lot_moves import move_lots_to_sale
from app.services.serialization import (
    lot_ids_with_linked_photos,
    lot_photo_out,
    lot_to_schema,
    lot_video_out,
    photo_count_is_sale_outlier,
    photo_count_outlier_by_lot_id,
    photo_health_from_loaded,
    sale_lot_photo_counts,
    upload_folder_has_linked_photos,
)
from app.services.sku_sanity import recompute_sku_sanity_flags_for_sale
from app.services.sale_merge import SaleMergeError, merge_catalog_sales
from app.services.suggestion_email import (
    read_recent_saved_suggestions,
    save_suggestion_local,
    send_suggestion_email,
    smtp_configured,
)
from app.services.photo_order import (
    renormalize_photo_sort_orders,
    reorder_photos_for_listing,
)
from app.services.mobile_drafts import cataloged_mobile_draft_lots
from app.services.photo_roles import normalize_catalog_photo_role, photos_for_ctbids_listing
from app.services.sale_covers import (
    SALE_COVER_SLOT_COUNT,
    cover_candidate_photos,
    load_cover_photos_by_ids,
    load_sale_lots_and_photos,
    parse_cover_photo_ids,
    persist_cover_photo_ids,
    photo_card_meta,
    photos_by_lot_id,
    photos_for_ids,
    candidate_is_hero,
    read_cover_image_bytes,
    validate_cover_photo_ids,
)
from app.services.ctseller_sale_sync import apply_ctseller_catalog_sale_sync
from app.services.roles import (
    ALLOWED_ROLES,
    can_delete_lot,
    can_run_audience_hunt,
    is_owner,
    normalize_role,
)
from app.services.team_activity import (
    ActivityWindowError,
    posted_lot_stats_for_account,
    resolve_activity_window,
    summarize_team_activity,
)
from app.services.listing_channels import (
    LISTING_CHANNEL_CTSELLER,
    LISTING_CHANNEL_FACEBOOK,
    is_facebook_listing_channel,
    normalize_listing_channel,
)
from app.services.sale_zip_export import (
    ZIP_KIND_ALL,
    ZIP_KIND_HEROES,
    cleanup_expired_sale_zip_exports,
    count_sale_catalog_photos,
    count_sale_hero_photos,
    normalize_sale_zip_kind,
)
from app.services.inventory_pdf import build_sale_inventory_pdf
from app.services.thumbnail_image import thumbnail_jpeg_bytes, thumbnail_jpeg_bytes_first_available
from app.catalog_storage import (
    delete_catalog_photo,
    migrate_local_photos_to_r2,
    persist_catalog_photo,
    persist_catalog_video,
    purge_catalog_photo_thumbs,
    resolve_catalog_storage_path,
    signed_catalog_url,
    upload_root,
)
from app.object_storage import r2_configured as catalog_r2_configured
from app.appraisal_storage import persist_appraisal_photo_bytes, resolve_appraisal_storage_path
from app.services.image_preprocess import compress_upload_bytes_if_image
from app.services.photo_captured_at import captured_at_from_epoch_ms, captured_at_from_path
from app.services.photo_rotate import rotate_image_bytes
from app.upload_storage import UPLOAD_ROOT, new_batch_dir, save_uploaded_files
from app.catalog_limits import (
    CTBIDS_MAX_PHOTOS_PER_LOT,
    count_catalog_image_files,
    ctbids_lot_photo_delta_error,
    is_catalog_video_filename,
    list_catalog_image_files_sorted,
    lot_video_delta_error,
    lot_video_size_error,
    video_content_type,
)

app = FastAPI(title=settings.app_name)
_LOG = logging.getLogger("app.main")


@app.exception_handler(SATimeoutError)
async def _db_pool_timeout_handler(_request: Request, _exc: SATimeoutError) -> JSONResponse:
    """QueuePool checkout timed out — do not dump a SQLAlchemy traceback per queued request."""
    _LOG.warning("Database pool busy (QueuePool timeout) — returning 503")
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Server is busy receiving photos. Wait a moment and refresh the dashboard."
        },
    )


def _sale_out(row: Sale) -> SaleOut:
    return SaleOut(
        id=row.id,
        account_id=row.account_id,
        user_id=row.user_id,
        name=row.name,
        closed_at=row.closed_at,
        ctseller_sale_id=getattr(row, "ctseller_sale_id", None),
        ctseller_reserve_price=getattr(row, "ctseller_reserve_price", None),
        created_at=row.created_at,
    )


def _ctseller_client_for_account(
    account: Account, db: Session, *, probe: bool = True, force: bool = False
) -> CTSellerClient:
    """Return an authenticated CT Seller client, refreshing or re-logging in as needed.

    ``probe=False`` skips the extra ``list_open_sales`` round-trip (dashboard sale picker
    already lists sales next). Still refreshes when the stored JWT is expired so the
    dashboard does not tell the owner to reconnect. ``force=True`` skips the stored
    access token (used after a 401).
    """
    access = unseal_secret(getattr(account, "ctseller_access_token_enc", None))
    refresh = unseal_secret(getattr(account, "ctseller_refresh_token_enc", None))
    username = (getattr(account, "ctseller_username", None) or "").strip()
    password = unseal_secret(getattr(account, "ctseller_password_enc", None))
    if not username or not password:
        raise HTTPException(
            status_code=400,
            detail="Connect your CT Seller account on Account → CT Seller first",
        )
    stale = force or access_token_should_refresh(access)
    client = CTSellerClient(access_token=None if stale else (access or None))
    if access and not stale:
        if not probe:
            return client
        try:
            client.list_open_sales()
            return client
        except CTSellerError:
            stale = True

    if refresh:
        try:
            tokens = client.refresh_tokens(refresh)
            persist_ctseller_tokens(account, tokens)
            db.add(account)
            db.commit()
            return client
        except CTSellerError:
            pass
    try:
        tokens = client.login(username, password, keep_logged_in=True)
    except CTSellerError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc) or "CT Seller login failed — update credentials on Account",
        ) from exc
    persist_ctseller_tokens(account, tokens)
    db.add(account)
    db.commit()
    return client


def _ctseller_after_expired_session(account_id: str) -> CTSellerClient:
    """Re-open a short DB session and force refresh / password login after a 401."""
    with SessionLocal() as db2:
        account = db2.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        return _ctseller_client_for_account(account, db2, force=True)


def _sale_for_user(sale_id: str, current: User, db: Session) -> Sale:
    row = (
        db.query(Sale)
        .filter(Sale.id == sale_id, Sale.account_id == current.account_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Sale not found")
    return row


def _sale_mapped_to_ctseller(db: Session, account_id: str, ctseller_sale_id: int) -> Sale | None:
    if ctseller_sale_id <= 0:
        return None
    return (
        db.query(Sale)
        .filter(Sale.account_id == account_id, Sale.ctseller_sale_id == int(ctseller_sale_id))
        .first()
    )


def _cover_catalog_sale(
    *,
    sale_id: str,
    ctseller_sale_id: int | None,
    current: User,
    db: Session,
) -> Sale:
    """Where the 4 covers are stored/pushed — the catalog linked to the CT Seller auction."""
    if ctseller_sale_id is not None:
        mapped = _sale_mapped_to_ctseller(db, current.account_id, int(ctseller_sale_id))
        if mapped:
            return mapped
    return _sale_for_user(sale_id, current, db)


def _require_owner(current: User) -> None:
    if not is_owner(current):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required")


def _require_can_delete_lot(current: User) -> None:
    if not can_delete_lot(current.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required to delete lots",
        )


def _require_can_run_audience_hunt(current: User) -> None:
    if not can_run_audience_hunt(current.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required to run audience hunts",
        )


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
    video_n = (
        db.query(func.count())
        .select_from(LotVideo)
        .where(LotVideo.lot_id == lot_id, LotVideo.account_id == account_id)
        .scalar()
    )
    out = out.model_copy(update={"video_count": int(video_n or 0)})
    regen = regeneration_tasks.lot_regenerate_is_active(row)
    err = regeneration_tasks.pop_regeneration_error(db, row) if not regen else None
    return _lot_out_with_runtime(out, row, db=db, pop_hunt_error=True, ai_regenerating=regen, ai_regenerate_error=err)


def _lot_out_with_runtime(
    out: LotOut,
    row: Lot,
    *,
    db: Session | None = None,
    pop_hunt_error: bool = False,
    ai_regenerating: bool | None = None,
    ai_regenerate_error: str | None = None,
) -> LotOut:
    hunting = audience_tasks.lot_hunt_is_active(row)
    hunt_err = None
    if pop_hunt_error and db is not None and not hunting:
        hunt_err = audience_tasks.pop_hunt_error(db, row)
    updates: dict = {
        "audience_hunting": hunting,
        "audience_hunt_phase": audience_tasks.lot_hunt_phase(row.id) if hunting else None,
        "audience_hunt_error": hunt_err,
    }
    if ai_regenerating or ai_regenerate_error:
        updates["ai_regenerating"] = bool(ai_regenerating)
        updates["ai_regenerate_error"] = ai_regenerate_error
    return out.model_copy(update=updates)


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
        listing_channel=normalize_listing_channel(getattr(job, "listing_channel", None)),
        progress_phase=getattr(job, "progress_phase", None),
        progress_done=int(getattr(job, "progress_done", 0) or 0),
        progress_total=int(getattr(job, "progress_total", 0) or 0),
        progress_file_name=getattr(job, "progress_file_name", None),
    )


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins(),
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
    _ensure_sale_zip_kind_column()
    _ensure_lot_audience_columns()
    _ensure_photo_listing_columns()
    _ensure_lot_search_tags_column()
    _ensure_sale_cover_photo_ids_column()
    _ensure_reserve_price_columns()
    _ensure_lot_ctseller_sale_id_column()


def _ensure_sale_zip_kind_column() -> None:
    """Local SQLite ``create_all`` does not ALTER existing tables — add ``kind`` if missing."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "sale_photo_zip_exports" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("sale_photo_zip_exports")}
    if "kind" in cols:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE sale_photo_zip_exports ADD COLUMN kind VARCHAR(20) DEFAULT 'all' NOT NULL"
            )
        )


def nullable_datetime_ddl(dialect_name: str | None = None) -> str:
    """Postgres has no DATETIME type; SQLite accepts DATETIME."""
    name = (dialect_name or engine.dialect.name or "").lower()
    if name.startswith("postgres"):
        return "TIMESTAMP WITH TIME ZONE"
    return "DATETIME"


def _ensure_lot_audience_columns() -> None:
    """Local SQLite ``create_all`` does not ALTER existing lots tables."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "lots" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("lots")}
    stmts: list[str] = []
    if "audience_hold" not in cols:
        stmts.append("ALTER TABLE lots ADD COLUMN audience_hold BOOLEAN DEFAULT 0 NOT NULL")
    if "audience_hunting_at" not in cols:
        stmts.append(f"ALTER TABLE lots ADD COLUMN audience_hunting_at {nullable_datetime_ddl()}")
    if "audience_hunt_error" not in cols:
        stmts.append("ALTER TABLE lots ADD COLUMN audience_hunt_error TEXT")
    if "audience_json" not in cols:
        stmts.append("ALTER TABLE lots ADD COLUMN audience_json TEXT DEFAULT '{}' NOT NULL")
    if not stmts:
        return
    with engine.begin() as conn:
        for stmt in stmts:
            conn.execute(text(stmt))


def _ensure_photo_listing_columns() -> None:
    """Local SQLite ``create_all`` does not ALTER existing photos tables."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "photos" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("photos")}
    stmts: list[str] = []
    if "distinct_item_id" not in cols:
        stmts.append("ALTER TABLE photos ADD COLUMN distinct_item_id VARCHAR(64)")
    if "is_primary_front" not in cols:
        stmts.append("ALTER TABLE photos ADD COLUMN is_primary_front BOOLEAN")
    if "shows_whole_lot" not in cols:
        stmts.append("ALTER TABLE photos ADD COLUMN shows_whole_lot BOOLEAN")
    if "shows_measuring_scale" not in cols:
        stmts.append("ALTER TABLE photos ADD COLUMN shows_measuring_scale BOOLEAN")
    if "label_legibility" not in cols:
        stmts.append("ALTER TABLE photos ADD COLUMN label_legibility VARCHAR(16)")
    if "captured_at" not in cols:
        stmts.append(f"ALTER TABLE photos ADD COLUMN captured_at {nullable_datetime_ddl()}")
    if not stmts:
        return
    with engine.begin() as conn:
        for stmt in stmts:
            conn.execute(text(stmt))


def _ensure_lot_search_tags_column() -> None:
    """Local SQLite ``create_all`` does not ALTER existing lots tables."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "lots" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("lots")}
    if "search_tags_json" in cols:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE lots ADD COLUMN search_tags_json TEXT DEFAULT '[]' NOT NULL"))


def _ensure_sale_cover_photo_ids_column() -> None:
    """Local SQLite ``create_all`` does not ALTER existing sales tables."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "sales" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("sales")}
    if "ctseller_cover_photo_ids" in cols:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE sales ADD COLUMN ctseller_cover_photo_ids TEXT"))


def _ensure_reserve_price_columns() -> None:
    """Local SQLite ``create_all`` does not ALTER existing sales/lots tables."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "sales" in insp.get_table_names():
        sale_cols = {c["name"] for c in insp.get_columns("sales")}
        if "ctseller_reserve_price" not in sale_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE sales ADD COLUMN ctseller_reserve_price FLOAT"))
    if "lots" in insp.get_table_names():
        lot_cols = {c["name"] for c in insp.get_columns("lots")}
        if "reserve_price" not in lot_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE lots ADD COLUMN reserve_price FLOAT"))


def _ensure_lot_ctseller_sale_id_column() -> None:
    """Local SQLite ``create_all`` does not ALTER existing lots / zip export tables."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "lots" in insp.get_table_names():
        lot_cols = {c["name"] for c in insp.get_columns("lots")}
        if "ctseller_sale_id" not in lot_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE lots ADD COLUMN ctseller_sale_id INTEGER"))
        if "ctseller_item_status" not in lot_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE lots ADD COLUMN ctseller_item_status VARCHAR(40)"))
        if "ctseller_sold_price" not in lot_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE lots ADD COLUMN ctseller_sold_price FLOAT"))
    if "sale_photo_zip_exports" in insp.get_table_names():
        zip_cols = {c["name"] for c in insp.get_columns("sale_photo_zip_exports")}
        if "filter_ctseller_sale_id" not in zip_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE sale_photo_zip_exports ADD COLUMN filter_ctseller_sale_id INTEGER"))


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
    role = normalize_role(payload.role)
    if role not in ALLOWED_ROLES:
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


@app.get("/team/activity", response_model=TeamActivityOut)
def team_activity(
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    since_days: int = Query(7, ge=1, le=366),
    include_lots: bool = Query(True),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TeamActivityOut:
    """Photos, lots, catalog jobs, CT Bids posted/sold, and per-lot shoot time. Owners only."""
    _require_owner(current)
    try:
        start, end = resolve_activity_window(since=since, until=until, since_days=since_days)
    except ActivityWindowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = summarize_team_activity(
        db, current.account_id, start, end, include_lots=include_lots
    )
    return TeamActivityOut(
        since=summary.since,
        until=summary.until,
        members=[
            TeamActivityMemberOut(
                user_id=m.user_id,
                email=m.email,
                role=m.role,
                photos_uploaded=m.photos_uploaded,
                lots_created=m.lots_created,
                jobs_started=m.jobs_started,
                active_hours=m.active_hours,
                photos_per_hour=m.photos_per_hour,
                lots_per_hour=m.lots_per_hour,
                lots_posted=m.lots_posted,
                lots_sold=m.lots_sold,
                lots_unsold=m.lots_unsold,
                lots_live=m.lots_live,
                lots_pulled=m.lots_pulled,
                sold_revenue=m.sold_revenue,
                first_photo_at=m.first_photo_at,
                last_photo_at=m.last_photo_at,
                last_lot_at=m.last_lot_at,
                lots_photographed=m.lots_photographed,
                median_minutes_per_lot=m.median_minutes_per_lot,
                mean_minutes_per_lot=m.mean_minutes_per_lot,
                lots=[
                    TeamActivityLotOut(
                        lot_id=lot.lot_id,
                        sale_id=lot.sale_id,
                        sku=lot.sku,
                        auction_title=lot.auction_title,
                        photo_count=lot.photo_count,
                        capture_minutes=lot.capture_minutes,
                        first_photo_at=lot.first_photo_at,
                        last_photo_at=lot.last_photo_at,
                        has_thumbnail=lot.has_thumbnail,
                        timing_source=lot.timing_source,
                        photo_times=[
                            TeamActivityPhotoTimeOut(at=pt.at, source=pt.source)
                            for pt in lot.photo_times
                        ],
                    )
                    for lot in m.lots
                ],
            )
            for m in summary.members
        ],
        totals=TeamActivityTotalsOut(
            photos_uploaded=summary.photos_uploaded,
            lots_created=summary.lots_created,
            jobs_started=summary.jobs_started,
            member_count=len(summary.members),
            active_hours=summary.active_hours,
            photos_per_hour=summary.photos_per_hour,
            lots_per_hour=summary.lots_per_hour,
            lots_posted=summary.lots_posted,
            lots_sold=summary.lots_sold,
            lots_unsold=summary.lots_unsold,
            lots_live=summary.lots_live,
            lots_pulled=summary.lots_pulled,
            sold_revenue=summary.sold_revenue,
            lots_photographed=summary.lots_photographed,
            median_minutes_per_lot=summary.median_minutes_per_lot,
            mean_minutes_per_lot=summary.mean_minutes_per_lot,
        ),
        sold_items=[
            SoldDocketItemOut(
                lot_id=item.lot_id,
                sale_id=item.sale_id,
                sale_name=item.sale_name,
                sku=item.sku,
                auction_title=item.auction_title,
                category=item.category,
                subcategory=item.subcategory,
                outcome=item.outcome,
                sold_price=item.sold_price,
                posted_at=item.posted_at,
                user_email=item.user_email,
                source=item.source,
                ctseller_item_id=item.ctseller_item_id,
            )
            for item in summary.sold_items
        ],
        sold_by_category=[
            SoldCategoryRollupOut(
                category=row.category,
                posted_count=row.posted_count,
                sold_count=row.sold_count,
                unsold_count=row.unsold_count,
                live_count=row.live_count,
                revenue=row.revenue,
                avg_price=row.avg_price,
                min_price=row.min_price,
                max_price=row.max_price,
                sell_through=row.sell_through,
            )
            for row in summary.sold_by_category
        ],
    )


@app.post("/team/activity/ctseller-history", response_model=CtSellerHistorySyncOut)
def pull_team_ctseller_history(
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    since_days: int = Query(7, ge=1, le=366),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CtSellerHistorySyncOut:
    """Pull closed (and currently open) CT Seller auctions into Sale results. Owners only."""
    _require_owner(current)
    try:
        start, end = resolve_activity_window(since=since, until=until, since_days=since_days)
    except ActivityWindowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    account = db.query(Account).filter(Account.id == current.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    from app.db import SessionLocal
    from app.services.ctseller_history import pull_ctseller_sale_history

    account_id = account.id
    try:
        client = _ctseller_client_for_account(account, db, probe=False)
        db.close()
        try:
            inner = SessionLocal()
            try:
                result = pull_ctseller_sale_history(inner, account_id, client, start, end)
                inner.commit()
            finally:
                inner.close()
        except CTSellerError as exc:
            if exc.status_code not in (401, 403):
                raise
            client = _ctseller_after_expired_session(account_id)
            inner = SessionLocal()
            try:
                result = pull_ctseller_sale_history(inner, account_id, client, start, end)
                inner.commit()
            finally:
                inner.close()
    except CTSellerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CtSellerHistorySyncOut(
        ended_sales=result.ended_sales,
        open_sales=result.open_sales,
        items_upserted=result.items_upserted,
        matched_lots=result.matched_lots,
    )


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
    if not is_owner(current) and not is_master_admin_email(current.email):
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
    # Pipeline token model (mirror of app.services.pipeline_integration estimate constants):
    # Flash triage runs per photo; a Flash listing pass and a Pro author pass run per finished lot.
    from app.services.pipeline_integration import (
        EST_PIPELINE_INPUT_TOKENS_PER_PHOTO,
        EST_PIPELINE_LISTING_INPUT,
        EST_PIPELINE_LISTING_OUTPUT,
        EST_PIPELINE_OUTPUT_TOKENS_PER_PHOTO,
        EST_PIPELINE_PRO_INPUT,
        EST_PIPELINE_PRO_OUTPUT,
    )

    flash_in_rate = float(settings.gemini_flash_input_cost_per_million)
    flash_out_rate = float(settings.gemini_flash_output_cost_per_million)
    pro_in_rate = float(settings.gemini_pro_input_cost_per_million)
    pro_out_rate = float(settings.gemini_pro_output_cost_per_million)

    def _cost(tokens: int, rate_per_million: float) -> float:
        return (tokens / 1_000_000.0) * rate_per_million

    since_dt = datetime.now(timezone.utc) - timedelta(days=since_days)
    until_dt = datetime.now(timezone.utc)
    in_progress_statuses = {JobStatus.receiving, JobStatus.queued, JobStatus.processing}
    from app.services.audience_hunt import hunt_pro_cost_usd, summarize_hunt_events

    hunt_events = (
        db.query(AudienceHuntEvent)
        .filter(AudienceHuntEvent.created_at >= since_dt)
        .all()
    )
    hunts_by_account: dict[str, list] = {}
    for ev in hunt_events:
        hunts_by_account.setdefault(ev.account_id, []).append(ev)

    def _account_row(account_id: str, account_name: str, jobs: list) -> AdminUsageAccountOut:
        jobs_started = len(jobs)
        jobs_completed = sum(1 for j in jobs if j.status == JobStatus.completed)
        jobs_failed = sum(1 for j in jobs if j.status == JobStatus.failed)
        jobs_in_progress = sum(1 for j in jobs if j.status in in_progress_statuses)
        analyzed_photos = sum(int(getattr(j, "analyzed_photos", 0) or 0) for j in jobs)

        durations = [
            max(0.0, (j.updated_at - j.created_at).total_seconds())
            for j in jobs
            if j.updated_at and j.created_at and j.status == JobStatus.completed
        ]
        active_days = len({j.created_at.date() for j in jobs if j.created_at})
        last_activity = max(
            (j.updated_at or j.created_at for j in jobs if (j.updated_at or j.created_at)),
            default=None,
        )

        photos_uploaded = int(
            db.query(func.count()).select_from(Photo)
            .filter(Photo.account_id == account_id, Photo.created_at >= since_dt).scalar() or 0
        )
        lots_created = int(
            db.query(func.count()).select_from(Lot)
            .filter(Lot.account_id == account_id, Lot.created_at >= since_dt).scalar() or 0
        )
        posted = posted_lot_stats_for_account(db, account_id, since_dt, until_dt)

        # Prefer real metered usage (response.usage_metadata) per job; fall back to the estimate
        # model for jobs cataloged before metering existed. Priced per tier (Flash vs Pro).
        flash_in = flash_out = pro_in = pro_out = 0
        metered_jobs = 0
        for j in jobs:
            a_fi = int(getattr(j, "actual_flash_input_tokens", 0) or 0)
            a_fo = int(getattr(j, "actual_flash_output_tokens", 0) or 0)
            a_pi = int(getattr(j, "actual_pro_input_tokens", 0) or 0)
            a_po = int(getattr(j, "actual_pro_output_tokens", 0) or 0)
            if a_fi or a_fo or a_pi or a_po:
                flash_in += a_fi
                flash_out += a_fo
                pro_in += a_pi
                pro_out += a_po
                metered_jobs += 1
            else:
                n = int(getattr(j, "analyzed_photos", 0) or 0)
                done = 1 if j.status == JobStatus.completed else 0
                flash_in += n * EST_PIPELINE_INPUT_TOKENS_PER_PHOTO + done * EST_PIPELINE_LISTING_INPUT
                flash_out += n * EST_PIPELINE_OUTPUT_TOKENS_PER_PHOTO + done * EST_PIPELINE_LISTING_OUTPUT
                pro_in += done * EST_PIPELINE_PRO_INPUT
                pro_out += done * EST_PIPELINE_PRO_OUTPUT

        flash_cost = _cost(flash_in, flash_in_rate) + _cost(flash_out, flash_out_rate)
        pro_cost = _cost(pro_in, pro_in_rate) + _cost(pro_out, pro_out_rate)
        hunt = summarize_hunt_events(hunts_by_account.get(account_id, []))
        hunt_cost = hunt_pro_cost_usd(
            pro_in=int(hunt["hunt_pro_input_tokens"]),
            pro_out=int(hunt["hunt_pro_output_tokens"]),
            in_rate=pro_in_rate,
            out_rate=pro_out_rate,
        )
        pro_cost = round(pro_cost + hunt_cost, 4)
        total_cost = flash_cost + pro_cost
        est_input_tokens = flash_in + pro_in + int(hunt["hunt_pro_input_tokens"])
        est_output_tokens = flash_out + pro_out + int(hunt["hunt_pro_output_tokens"])
        finished = jobs_completed + jobs_failed
        last_hunt = hunt.get("last_hunt_at")
        if last_hunt is not None and (last_activity is None or last_hunt > last_activity):
            last_activity = last_hunt

        return AdminUsageAccountOut(
            account_id=account_id,
            account_name=account_name,
            jobs_started=jobs_started,
            jobs_completed=jobs_completed,
            jobs_failed=jobs_failed,
            jobs_in_progress=jobs_in_progress,
            photos_uploaded=photos_uploaded,
            lots_created=lots_created,
            lots_posted=posted.posted,
            lots_sold=posted.sold,
            lots_unsold=posted.unsold,
            lots_live=posted.live,
            lots_pulled=posted.pulled,
            sold_revenue=posted.sold_revenue,
            avg_photos_per_job=round(photos_uploaded / jobs_started, 2) if jobs_started else 0.0,
            avg_job_duration_seconds=round(mean(durations), 1) if durations else 0.0,
            success_rate=round(jobs_completed / finished, 3) if finished else 0.0,
            est_input_tokens=est_input_tokens,
            est_output_tokens=est_output_tokens,
            est_flash_cost_usd=round(flash_cost, 4),
            est_pro_cost_usd=round(pro_cost, 4),
            est_total_cost_usd=round(total_cost, 4),
            metered_jobs=metered_jobs,
            est_cost_per_lot_usd=round(total_cost / lots_created, 4) if lots_created else 0.0,
            est_cost_per_photo_usd=round(total_cost / photos_uploaded, 4) if photos_uploaded else 0.0,
            avg_tokens_per_job=int((est_input_tokens + est_output_tokens) / jobs_started) if jobs_started else 0,
            active_days=active_days,
            last_activity_at=last_activity,
            hunts_run=int(hunt["hunts_run"]),
            hunts_skipped=int(hunt["hunts_skipped"]),
            hunts_failed=int(hunt["hunts_failed"]),
            hunt_groups_found=int(hunt["hunt_groups_found"]),
            hunt_search_calls=int(hunt["hunt_search_calls"]),
            hunt_pro_input_tokens=int(hunt["hunt_pro_input_tokens"]),
            hunt_pro_output_tokens=int(hunt["hunt_pro_output_tokens"]),
            hunt_pro_cost_usd=hunt_cost,
        )

    accounts = db.query(Account).order_by(Account.created_at.asc()).all()
    all_jobs = (
        db.query(ProcessingJob)
        .filter(ProcessingJob.created_at >= since_dt)
        .all()
    )
    jobs_by_account: dict[str, list] = {}
    for j in all_jobs:
        jobs_by_account.setdefault(j.account_id, []).append(j)

    rows = [_account_row(a.id, a.name, jobs_by_account.get(a.id, [])) for a in accounts]

    # Totals aggregate the per-account rows (photos/lots are per-account DB counts) and recompute
    # rates from all jobs so the "All accounts" line stays internally consistent.
    def _sum(attr: str) -> int:
        return int(sum(getattr(r, attr) for r in rows))

    total_jobs = _sum("jobs_started")
    total_completed = _sum("jobs_completed")
    total_failed = _sum("jobs_failed")
    total_photos = _sum("photos_uploaded")
    total_lots = _sum("lots_created")
    total_in = _sum("est_input_tokens")
    total_out = _sum("est_output_tokens")
    total_flash = round(sum(r.est_flash_cost_usd for r in rows), 4)
    total_pro = round(sum(r.est_pro_cost_usd for r in rows), 4)
    total_cost = round(total_flash + total_pro, 4)
    total_durations = [
        max(0.0, (j.updated_at - j.created_at).total_seconds())
        for j in all_jobs
        if j.updated_at and j.created_at and j.status == JobStatus.completed
    ]
    total_finished = total_completed + total_failed
    job_last = max(
        (j.updated_at or j.created_at for j in all_jobs if (j.updated_at or j.created_at)),
        default=None,
    )
    hunt_last = max((r.last_activity_at for r in rows if r.last_activity_at), default=None)
    last_all = job_last
    if hunt_last is not None and (last_all is None or hunt_last > last_all):
        last_all = hunt_last
    totals = AdminUsageAccountOut(
        account_id="all",
        account_name="All accounts",
        jobs_started=total_jobs,
        jobs_completed=total_completed,
        jobs_failed=total_failed,
        jobs_in_progress=_sum("jobs_in_progress"),
        photos_uploaded=total_photos,
        lots_created=total_lots,
        lots_posted=_sum("lots_posted"),
        lots_sold=_sum("lots_sold"),
        lots_unsold=_sum("lots_unsold"),
        lots_live=_sum("lots_live"),
        lots_pulled=_sum("lots_pulled"),
        sold_revenue=round(sum(r.sold_revenue for r in rows), 2),
        avg_photos_per_job=round(total_photos / total_jobs, 2) if total_jobs else 0.0,
        avg_job_duration_seconds=round(mean(total_durations), 1) if total_durations else 0.0,
        success_rate=round(total_completed / total_finished, 3) if total_finished else 0.0,
        est_input_tokens=total_in,
        est_output_tokens=total_out,
        est_flash_cost_usd=total_flash,
        est_pro_cost_usd=total_pro,
        est_total_cost_usd=total_cost,
        est_cost_per_lot_usd=round(total_cost / total_lots, 4) if total_lots else 0.0,
        est_cost_per_photo_usd=round(total_cost / total_photos, 4) if total_photos else 0.0,
        avg_tokens_per_job=int((total_in + total_out) / total_jobs) if total_jobs else 0,
        metered_jobs=_sum("metered_jobs"),
        active_days=len({j.created_at.date() for j in all_jobs if j.created_at}),
        last_activity_at=last_all,
        hunts_run=_sum("hunts_run"),
        hunts_skipped=_sum("hunts_skipped"),
        hunts_failed=_sum("hunts_failed"),
        hunt_groups_found=_sum("hunt_groups_found"),
        hunt_search_calls=_sum("hunt_search_calls"),
        hunt_pro_input_tokens=_sum("hunt_pro_input_tokens"),
        hunt_pro_output_tokens=_sum("hunt_pro_output_tokens"),
        hunt_pro_cost_usd=round(sum(r.hunt_pro_cost_usd for r in rows), 4),
    )
    return AdminUsageSummaryOut(
        since_days=since_days,
        total_accounts=len(rows),
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
    _require_owner(current)
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


def _ctseller_remote_sale_outs(rows: list) -> list[CTSellerRemoteSaleOut]:
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


def _account_sales_out(db: Session, account_id: str) -> list[SaleOut]:
    rows = (
        db.query(Sale)
        .filter(Sale.account_id == account_id)
        .order_by(Sale.created_at.asc())
        .all()
    )
    return [_sale_out(r) for r in rows]


@app.post("/sales/sync-from-ctseller", response_model=CtsellerSaleSyncOut)
def sync_sales_from_ctseller(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CtsellerSaleSyncOut:
    """Create catalog sales for open CT Seller auctions; refresh names on mapped open sales."""
    account = db.query(Account).filter(Account.id == current.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    username = (getattr(account, "ctseller_username", None) or "").strip()
    password = unseal_secret(getattr(account, "ctseller_password_enc", None))
    if not username or not password:
        return CtsellerSaleSyncOut(
            connected=False,
            sales=_account_sales_out(db, current.account_id),
        )
    try:
        client = _ctseller_client_for_account(account, db, probe=False)
        account_id = account.id
        user_id = current.id
        try:
            remote = client.list_open_sales()
        except CTSellerError as exc:
            if exc.status_code not in (401, 403):
                raise
            client = _ctseller_after_expired_session(account_id)
            remote = client.list_open_sales()
    except HTTPException as exc:
        if exc.status_code == 400:
            return CtsellerSaleSyncOut(
                connected=False,
                sales=_account_sales_out(db, current.account_id),
            )
        raise
    except CTSellerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    stats = apply_ctseller_catalog_sale_sync(
        db,
        account_id=account_id,
        user_id=user_id,
        remote_sales=remote,
    )
    db.commit()
    return CtsellerSaleSyncOut(
        connected=True,
        created=stats.created,
        renamed=stats.renamed,
        already_mapped=stats.already_mapped,
        remote_count=len(remote),
        remote_sales=_ctseller_remote_sale_outs(remote),
        sales=_account_sales_out(db, account_id),
    )


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
    lots = (
        db.query(Lot)
        .filter(Lot.sale_id == sale_id, Lot.account_id == current.account_id)
        .order_by(Lot.created_at.asc(), Lot.id.asc())
        .all()
    )
    lot_meta = [
        (lot.id, lot.sku, lot.auction_title or "", int(lot.photo_count or 0)) for lot in lots
    ]
    photo_rows: list[tuple[str | None, str | None]] = []
    if lot_meta:
        photo_rows = list(
            db.query(Photo.lot_id, Photo.storage_path)
            .filter(
                Photo.account_id == current.account_id,
                Photo.lot_id.in_([row[0] for row in lot_meta]),
            )
            .all()
        )
    db.close()
    return SalePhotoHealthOut(lots=photo_health_from_loaded(lot_meta, photo_rows))


@app.get("/sales/{sale_id}/cataloged-mobile-drafts", response_model=CatalogedMobileDraftsOut)
def sale_cataloged_mobile_drafts(
    sale_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CatalogedMobileDraftsOut:
    """Draft ids whose ``mobile-<draft>-NN.jpg`` photos already exist as catalog rows on this sale."""
    _sale_for_user(sale_id, current, db)
    mapped = cataloged_mobile_draft_lots(db, current.account_id, sale_id)
    return CatalogedMobileDraftsOut(
        draft_ids=[row.draft_id for row in mapped],
        lots=[
            CatalogedMobileDraftLotOut(
                draft_id=row.draft_id,
                lot_id=row.lot_id,
                sku=row.sku,
                photo_count=row.photo_count,
                auction_title=row.auction_title,
            )
            for row in mapped
        ],
    )


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
    if payload.clear_ctseller_reserve_price:
        row.ctseller_reserve_price = None
    elif payload.ctseller_reserve_price is not None:
        row.ctseller_reserve_price = float(payload.ctseller_reserve_price)
    db.commit()
    db.refresh(row)
    return _sale_out(row)


_SALE_COVER_SLOT_LABELS = (
    "Primary cover",
    "Secondary 1",
    "Secondary 2",
    "Secondary 3",
)


def _sale_cover_remote(client: CTSellerClient, remote_sale_id: int) -> SaleCoverRemoteOut | None:
    rows = client.list_sale_images(sale_id=int(remote_sale_id))
    profile = pick_profile_sale_image(rows)
    if not profile:
        return None
    remote_id = profile.get("id")
    try:
        parsed_id = int(remote_id) if remote_id is not None else None
    except (TypeError, ValueError):
        parsed_id = None
    primary = str(profile.get("url") or profile.get("image") or "").strip() or None
    if primary and primary.startswith("data:"):
        primary = None
    secondaries = [
        url
        for url in parse_sale_image_secondary_urls(profile.get("itemimageurl"))
        if url and not url.startswith("data:")
    ]
    return SaleCoverRemoteOut(id=parsed_id, primary_url=primary, secondary_urls=secondaries)


def _cover_slots_out(
    selected: list[Photo],
    lots_by_id: dict[str, Lot],
    *,
    sales_by_id: dict[str, Sale] | None = None,
    current_sale_id: str | None = None,
) -> list[SaleCoverPhotoSlotOut]:
    slots: list[SaleCoverPhotoSlotOut] = []
    for idx in range(SALE_COVER_SLOT_COUNT):
        photo = selected[idx] if idx < len(selected) else None
        meta = (
            photo_card_meta(
                photo,
                lots_by_id,
                sales_by_id=sales_by_id,
                current_sale_id=current_sale_id,
            )
            if photo is not None
            else {}
        )
        slots.append(
            SaleCoverPhotoSlotOut(
                slot=idx + 1,
                label=_SALE_COVER_SLOT_LABELS[idx],
                photo_id=meta.get("photo_id") if photo is not None else None,
                lot_id=meta.get("lot_id") if photo is not None else None,
                sku=meta.get("sku") if photo is not None else None,
                auction_title=str(meta.get("auction_title") or "") if photo is not None else "",
                photo_role=meta.get("photo_role") if photo is not None else None,
                thumb_url=meta.get("thumb_url") if photo is not None else None,
                sale_id=meta.get("sale_id") if photo is not None else None,
                sale_name=str(meta.get("sale_name") or "") if photo is not None else "",
                from_other_sale=bool(meta.get("from_other_sale")) if photo is not None else False,
            )
        )
    return slots


def _sale_cover_photos_payload(
    *,
    sale: Sale,
    lots: list[Lot],
    photos: list[Photo],
    sales_by_id: dict[str, Sale] | None = None,
    remote: SaleCoverRemoteOut | None = None,
    remote_error: str | None = None,
) -> SaleCoverPhotosOut:
    lots_by_id = {lot.id: lot for lot in lots}
    saved = parse_cover_photo_ids(getattr(sale, "ctseller_cover_photo_ids", None))
    selected = photos_for_ids(photos, saved)
    return SaleCoverPhotosOut(
        sale_id=sale.id,
        ctseller_sale_id=sale.ctseller_sale_id,
        catalog_sale_name=(sale.name or "").strip(),
        selected_photo_ids=saved,
        suggested_photo_ids=[],
        slots=_cover_slots_out(
            selected,
            lots_by_id,
            sales_by_id=sales_by_id,
            current_sale_id=sale.id,
        ),
        remote=remote,
        remote_error=remote_error,
    )


@app.get("/sales/{sale_id}/cover-photos", response_model=SaleCoverPhotosOut)
def get_sale_cover_photos(
    sale_id: str,
    include_remote: bool = Query(False, description="Also list covers already on CT Seller."),
    ctseller_sale_id: int | None = Query(
        None, description="CT Seller auction from the dashboard dropdown — covers follow this, not Change sale."
    ),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaleCoverPhotosOut:
    """Saved 4 sale covers plus what CT Seller already has. Slots stay empty until staff pick."""
    sale = _cover_catalog_sale(
        sale_id=sale_id, ctseller_sale_id=ctseller_sale_id, current=current, db=db
    )
    saved = parse_cover_photo_ids(getattr(sale, "ctseller_cover_photo_ids", None))
    lots, photos, sales_by_id = load_cover_photos_by_ids(db, current.account_id, saved)
    remote: SaleCoverRemoteOut | None = None
    remote_error: str | None = None
    remote_lookup = ctseller_sale_id if ctseller_sale_id is not None else sale.ctseller_sale_id
    if include_remote and remote_lookup:
        account = db.query(Account).filter(Account.id == current.account_id).first()
        if account:
            try:
                client = _ctseller_client_for_account(account, db, probe=False)
                remote = _sale_cover_remote(client, int(remote_lookup))
            except CTSellerError as exc:
                remote_error = str(exc)
            except Exception as exc:  # noqa: BLE001
                remote_error = str(exc)
    return _sale_cover_photos_payload(
        sale=sale,
        lots=lots,
        photos=photos,
        sales_by_id=sales_by_id,
        remote=remote,
        remote_error=remote_error,
    )


@app.get("/sales/{sale_id}/cover-candidates", response_model=SaleCoverCandidatesOut)
def get_sale_cover_candidates(
    sale_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaleCoverCandidatesOut:
    """Photos from the open website catalog. Saved covers still follow the CT Seller auction."""
    sale = _sale_for_user(sale_id, current, db)
    lots, photos = load_sale_lots_and_photos(db, sale)
    lots_by_id = {lot.id: lot for lot in lots}
    sales_by_id = {sale.id: sale}
    by_lot = photos_by_lot_id(photos)
    cards: list[SaleCoverCandidateOut] = []
    for photo in cover_candidate_photos(lots, photos):
        meta = photo_card_meta(
            photo,
            lots_by_id,
            sales_by_id=sales_by_id,
            current_sale_id=sale.id,
        )
        cards.append(
            SaleCoverCandidateOut(
                photo_id=photo.id,
                lot_id=photo.lot_id or "",
                sku=meta.get("sku"),
                auction_title=str(meta.get("auction_title") or ""),
                photo_role=meta.get("photo_role"),
                is_hero=candidate_is_hero(photo, by_lot.get(photo.lot_id or "", [])),
                suggested=False,
                # Cheap HMAC of the original — do not generate 220px thumbs here.
                thumb_url=meta.get("thumb_url"),
                sale_id=meta.get("sale_id"),
                sale_name=str(meta.get("sale_name") or ""),
                from_other_sale=bool(meta.get("from_other_sale")),
            )
        )
    return SaleCoverCandidatesOut(
        sale_id=sale.id,
        ctseller_sale_id=sale.ctseller_sale_id,
        catalog_sale_name=(sale.name or "").strip(),
        suggested_photo_ids=[],
        candidates=cards,
    )


@app.put("/sales/{sale_id}/cover-photos", response_model=SaleCoverPhotosOut)
def select_sale_cover_photos(
    sale_id: str,
    payload: SaleCoverPhotosSelectIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaleCoverPhotosOut:
    """Remember up to 4 catalog photos as this sale's CT Seller covers."""
    sale = _cover_catalog_sale(
        sale_id=sale_id,
        ctseller_sale_id=payload.ctseller_sale_id,
        current=current,
        db=db,
    )
    lots, photos, sales_by_id = load_cover_photos_by_ids(
        db, current.account_id, payload.photo_ids
    )
    try:
        selected = validate_cover_photo_ids(
            photo_ids=payload.photo_ids, catalog_photos=photos, require_four=False
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    persist_cover_photo_ids(sale, selected)
    db.add(sale)
    db.commit()
    db.refresh(sale)
    return _sale_cover_photos_payload(
        sale=sale, lots=lots, photos=photos, sales_by_id=sales_by_id
    )


@app.post("/sales/{sale_id}/cover-photos/push", response_model=SaleCoverPhotosPushOut)
def push_sale_cover_photos(
    sale_id: str,
    payload: SaleCoverPhotosPushIn | None = None,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaleCoverPhotosPushOut:
    """Upload the 4 selected catalog photos as CT Seller sale covers."""
    body = payload or SaleCoverPhotosPushIn()
    remote_hint = body.ctseller_sale_id
    sale = _cover_catalog_sale(
        sale_id=sale_id, ctseller_sale_id=remote_hint, current=current, db=db
    )
    remote_sale_id = remote_hint if remote_hint is not None else sale.ctseller_sale_id
    if remote_sale_id is None:
        raise HTTPException(
            status_code=400,
            detail="Pick a CT Seller auction first (same picker used to post lots)",
        )
    wanted = body.photo_ids if body.photo_ids is not None else parse_cover_photo_ids(
        getattr(sale, "ctseller_cover_photo_ids", None)
    )
    if not wanted:
        raise HTTPException(
            status_code=400,
            detail="Pick 4 cover photos on the dashboard first",
        )
    _lots, photos, _sales_by_id = load_cover_photos_by_ids(db, current.account_id, wanted)
    try:
        selected_ids = validate_cover_photo_ids(
            photo_ids=wanted, catalog_photos=photos, require_four=True
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    selected = photos_for_ids(photos, selected_ids)
    if len(selected) != SALE_COVER_SLOT_COUNT:
        raise HTTPException(
            status_code=400,
            detail="Need 4 listing photos from your catalog — pick extras from this sale or another catalog",
        )

    account = db.query(Account).filter(Account.id == current.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    try:
        images = [read_cover_image_bytes(photo) for photo in selected]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read cover photos: {exc}") from exc

    try:
        client = _ctseller_client_for_account(account, db, probe=False)
        try:
            existing = pick_profile_sale_image(client.list_sale_images(sale_id=int(remote_sale_id)))
        except CTSellerError as exc:
            if exc.status_code not in (401, 403):
                raise
            client = _ctseller_client_for_account(account, db, force=True)
            existing = pick_profile_sale_image(client.list_sale_images(sale_id=int(remote_sale_id)))
        existing_id: int | None = None
        if existing and existing.get("id") is not None:
            try:
                existing_id = int(existing["id"])
            except (TypeError, ValueError):
                existing_id = None
        result = client.upsert_sale_cover_photos(
            sale_id=int(remote_sale_id),
            primary_jpeg=images[0],
            secondary_jpegs=images[1:4],
            existing_id=existing_id,
        )
    except CTSellerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    persist_cover_photo_ids(sale, selected_ids)
    if body.save_sale_mapping and sale.ctseller_sale_id != int(remote_sale_id):
        sale.ctseller_sale_id = int(remote_sale_id)
    db.add(sale)
    db.commit()

    remote_id = existing_id
    data = result.get("data") if isinstance(result, dict) else None
    row = None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        row = data[0]
    elif isinstance(data, dict):
        row = data
    if row and row.get("id") is not None:
        try:
            remote_id = int(row["id"])
        except (TypeError, ValueError):
            pass

    return SaleCoverPhotosPushOut(
        sale_id=sale.id,
        ctseller_sale_id=int(remote_sale_id),
        updated=existing_id is not None,
        remote_id=remote_id,
        selected_photo_ids=selected_ids,
    )


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


@app.post("/sales/{sale_id}/reserve-price", response_model=SaleReservePriceOut)
def set_sale_reserve_price(
    sale_id: str,
    payload: SaleReservePriceIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaleReservePriceOut:
    """Save a sale-wide reserve and optionally stamp lots / update posted CT Seller items."""
    sale = _sale_for_user(sale_id, current, db)
    reserve = payload.reserve_price
    if reserve is not None:
        reserve = float(reserve)
        if reserve <= 0:
            reserve = None
    if payload.update_sale_default:
        sale.ctseller_reserve_price = reserve
        db.add(sale)

    lots_q = db.query(Lot).filter(Lot.account_id == current.account_id)
    if payload.lot_ids:
        wanted = {str(x).strip() for x in payload.lot_ids if str(x).strip()}
        lots = lots_q.filter(Lot.id.in_(wanted)).all() if wanted else []
    elif payload.ctseller_sale_id:
        lots = lots_q.filter(
            Lot.ctseller_sale_id == int(payload.ctseller_sale_id),
            Lot.ctseller_item_id.isnot(None),
        ).all()
    else:
        lots = lots_q.filter(Lot.sale_id == sale.id).all()
    if payload.only_without_reserve:
        lots = [lot for lot in lots if lot.reserve_price is None]

    lots_updated = 0
    if payload.apply_to_lots:
        for lot in lots:
            if lot.reserve_price != reserve:
                lot.reserve_price = reserve
                db.add(lot)
                lots_updated += 1
    db.commit()

    results: list[SaleReservePriceLotResult] = []
    posted_updated = 0
    posted_failed = 0
    posted_skipped = 0
    if payload.push_posted:
        posted_lots = [lot for lot in lots if lot.ctseller_item_id]
        if posted_lots:
            account = db.query(Account).filter(Account.id == current.account_id).first()
            if not account:
                raise HTTPException(status_code=404, detail="Account not found")
            try:
                client = _ctseller_client_for_account(account, db, probe=False)
            except CTSellerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            for lot in posted_lots:
                try:
                    client.update_item_reserve(
                        item_id=int(lot.ctseller_item_id),
                        reserve_price=resolve_lot_reserve_price(lot, sale),
                    )
                    posted_updated += 1
                    results.append(
                        SaleReservePriceLotResult(
                            lot_id=lot.id,
                            sku=lot.sku,
                            ok=True,
                            ctseller_item_id=int(lot.ctseller_item_id),
                        )
                    )
                except CTSellerError as exc:
                    if exc.status_code in (401, 403):
                        try:
                            client = _ctseller_client_for_account(account, db, force=True)
                            client.update_item_reserve(
                                item_id=int(lot.ctseller_item_id),
                                reserve_price=resolve_lot_reserve_price(lot, sale),
                            )
                            posted_updated += 1
                            results.append(
                                SaleReservePriceLotResult(
                                    lot_id=lot.id,
                                    sku=lot.sku,
                                    ok=True,
                                    ctseller_item_id=int(lot.ctseller_item_id),
                                )
                            )
                            continue
                        except CTSellerError as retry_exc:
                            exc = retry_exc
                    posted_failed += 1
                    results.append(
                        SaleReservePriceLotResult(
                            lot_id=lot.id,
                            sku=lot.sku,
                            ok=False,
                            error=str(exc),
                            ctseller_item_id=int(lot.ctseller_item_id) if lot.ctseller_item_id else None,
                        )
                    )
        posted_skipped = len(lots) - len(posted_lots)

    return SaleReservePriceOut(
        sale_id=sale.id,
        reserve_price=reserve,
        lots_updated=lots_updated,
        posted_updated=posted_updated,
        posted_failed=posted_failed,
        posted_skipped=posted_skipped,
        results=results,
    )


@app.post("/sales/{sale_id}/sort-photos-for-listing")
def sort_sale_photos_for_listing(
    sale_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """
    Re-sort every lot's photos in this sale into listing order.

    New lots: group shots, then each item as a chapter, then labels, then measurements, tape last.
    Lots without listing-meta columns keep the older role-bucket sort.
    Staff can still drag photos afterward.
    """
    sale = _sale_for_user(sale_id, current, db)
    lots = (
        db.query(Lot)
        .filter(Lot.account_id == current.account_id, Lot.sale_id == sale.id)
        .all()
    )
    fixed = 0
    for lot in lots:
        if reorder_photos_for_listing(db, lot.id, current.account_id):
            fixed += 1
    if fixed:
        db.commit()
    return {"lots_total": len(lots), "lots_fixed": fixed}


@app.post("/lots/{lot_id}/photos/sort-for-listing", response_model=list[LotPhotoOut])
def sort_lot_photos_for_listing(
    lot_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LotPhotoOut]:
    """Re-sort this lot's photos into listing order (group shots, item chapters, labels, measurements)."""
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    _assert_sale_editable_for_lot_moves(db, row.sale_id, current)
    if reorder_photos_for_listing(db, lot_id, current.account_id):
        db.commit()
    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == current.account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
        .all()
    )
    return [lot_photo_out(p, lot_id=lot_id) for p in photos]


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


@app.post("/sales/{sale_id}/remove", response_model=RemoveSaleOut)
def remove_sale(
    sale_id: str,
    payload: RemoveSaleIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RemoveSaleOut:
    """
    Remove one Estate-EZ catalog sale from Change sale.

    Deletes that sale's lots, photos, videos, and jobs on this website only.
    Does not change CT Bids / CT Seller auctions. Requires the owner to type the sale name.
    """
    _require_owner(current)
    row = _sale_for_user(sale_id, current, db)
    want = (payload.confirm_name or "").strip()
    have = (row.name or "").strip()
    if not want or want != have:
        raise HTTPException(
            status_code=400,
            detail="Sale name did not match — nothing was deleted.",
        )
    busy = (
        db.query(func.count())
        .select_from(ProcessingJob)
        .where(
            ProcessingJob.sale_id == row.id,
            ProcessingJob.account_id == current.account_id,
            ProcessingJob.status.in_((JobStatus.receiving, JobStatus.queued, JobStatus.processing)),
        )
        .scalar()
    )
    if int(busy or 0) > 0:
        raise HTTPException(
            status_code=409,
            detail="Wait until uploads for this sale finish (or cancel them) before removing it.",
        )
    lot_n = int(
        db.query(func.count())
        .select_from(Lot)
        .where(Lot.sale_id == row.id, Lot.account_id == current.account_id)
        .scalar()
        or 0
    )
    job_n = int(
        db.query(func.count())
        .select_from(ProcessingJob)
        .where(ProcessingJob.sale_id == row.id, ProcessingJob.account_id == current.account_id)
        .scalar()
        or 0
    )
    name = have
    sid = row.id
    _purge_catalog_data(db, account_id=current.account_id, sale_id=sid)
    leftover = db.query(Sale).filter(Sale.id == sid, Sale.account_id == current.account_id).first()
    if leftover:
        db.delete(leftover)
        db.commit()
    return RemoveSaleOut(
        deleted_sale_id=sid,
        deleted_sale_name=name,
        lots_deleted=lot_n,
        jobs_deleted=job_n,
    )


@app.post("/sales/{target_sale_id}/merge", response_model=SaleMergeOut)
def merge_sales(
    target_sale_id: str,
    payload: SaleMergeIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaleMergeOut:
    """Move lots and jobs from ``source_sale_id`` into this sale, then delete the source catalog sale."""
    _require_owner(current)
    try:
        result = merge_catalog_sales(
            db,
            account_id=current.account_id,
            target_sale_id=target_sale_id,
            source_sale_id=payload.source_sale_id,
        )
    except SaleMergeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return SaleMergeOut(
        target_sale_id=result.target_sale_id,
        target_sale_name=result.target_sale_name,
        source_sale_id=result.source_sale_id,
        source_sale_name=result.source_sale_name,
        lots_moved=result.lots_moved,
        jobs_moved=result.jobs_moved,
        appraisals_moved=result.appraisals_moved,
        zip_exports_moved=result.zip_exports_moved,
        duplicate_sku_count=result.duplicate_sku_count,
    )


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
        # Auth/token refresh may briefly use this session; release it before listing sales over HTTP.
        client = _ctseller_client_for_account(account, db, probe=False)
        account_id = account.id
        db.close()
        try:
            rows = client.list_open_sales()
        except CTSellerError as exc:
            if exc.status_code not in (401, 403):
                raise
            client = _ctseller_after_expired_session(account_id)
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


@app.post("/sales/{sale_id}/refresh-ctseller-auctions", response_model=CTSellerPostedAuctionsOut)
def refresh_ctseller_posted_auctions(
    sale_id: str,
    payload: CTSellerPostedRefreshIn | None = None,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CTSellerPostedAuctionsOut:
    """Look up which CT Bids auction each posted lot lives on (this catalog, plus a focused live sale)."""
    sale = _sale_for_user(sale_id, current, db)
    body = payload or CTSellerPostedRefreshIn()
    try:
        focus_id = int(body.ctseller_sale_id) if body.ctseller_sale_id is not None else 0
    except (TypeError, ValueError):
        focus_id = 0
    lots = (
        db.query(Lot)
        .filter(Lot.sale_id == sale.id, Lot.account_id == current.account_id)
        .all()
    )
    posted_by_id = {
        lot.id: lot for lot in lots if lot.ctseller_item_id or lot.ctseller_posted_at
    }
    extra_ids: list[int] = []
    mapped = getattr(sale, "ctseller_sale_id", None)
    try:
        mapped_n = int(mapped) if mapped is not None else 0
    except (TypeError, ValueError):
        mapped_n = 0
    if mapped_n > 0:
        extra_ids.append(mapped_n)
    if focus_id > 0:
        extra_ids.append(focus_id)
        for lot in (
            db.query(Lot)
            .filter(
                Lot.account_id == current.account_id,
                Lot.ctseller_sale_id == focus_id,
                Lot.ctseller_item_id.isnot(None),
            )
            .all()
        ):
            posted_by_id[lot.id] = lot
    posted = list(posted_by_id.values())
    looked_up = 0
    failed = 0
    title_by_id: dict[int, str] = {}
    need_client = bool(posted) or focus_id > 0
    if need_client:
        account = db.query(Account).filter(Account.id == current.account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        try:
            client = _ctseller_client_for_account(account, db, probe=False)
            if focus_id > 0:
                extra_ids.append(focus_id)
                try:
                    focus_item_ids: set[int] = set()
                    for row in client.list_sale_items(int(focus_id)):
                        try:
                            iid = int(row.get("id"))
                        except (TypeError, ValueError, AttributeError):
                            continue
                        if iid > 0:
                            focus_item_ids.add(iid)
                    if focus_item_ids:
                        for lot in (
                            db.query(Lot)
                            .filter(
                                Lot.account_id == current.account_id,
                                Lot.ctseller_item_id.in_(list(focus_item_ids)),
                            )
                            .all()
                        ):
                            posted_by_id[lot.id] = lot
                except CTSellerError:
                    pass
            posted = list(posted_by_id.values())
            if posted:
                looked_up, failed = refresh_lot_ctseller_sale_ids(
                    db, posted, client, extra_sale_ids=extra_ids
                )
            try:
                for row in client.list_open_sales():
                    title_by_id[int(row.id)] = row.title
            except CTSellerError:
                pass
        except CTSellerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        posted = list(posted_by_id.values())
    ranked, unknown = posted_auction_counts(posted)
    outcomes = posted_outcome_counts(posted)
    auctions = [
        CTSellerPostedAuctionOut(
            ctseller_sale_id=sid,
            title=title_by_id.get(sid) or f"CT Bids sale #{sid}",
            posted_count=n,
        )
        for sid, n in ranked
    ]
    return CTSellerPostedAuctionsOut(
        looked_up=looked_up,
        failed=failed,
        unknown_count=unknown,
        sold_count=outcomes.get("sold", 0),
        unsold_count=outcomes.get("unsold", 0),
        live_count=outcomes.get("live", 0),
        auctions=auctions,
    )


def _search_tags_for_lot_row(lot: Lot) -> list[str]:
    from app.services.search_tags import parse_search_tags_json, search_tags_for_lot_fields

    try:
        specs = json.loads(lot.specification_lines_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        specs = []
    try:
        brands = json.loads(lot.brands_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        brands = []
    try:
        labels = json.loads(lot.labels_and_tags_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        labels = []
    if not isinstance(specs, list):
        specs = []
    if not isinstance(brands, list):
        brands = []
    if not isinstance(labels, list):
        labels = []
    return search_tags_for_lot_fields(
        stored=parse_search_tags_json(getattr(lot, "search_tags_json", None)),
        title=lot.auction_title or "",
        description=lot.professional_description or "",
        brands=brands,
        specification_lines=specs,
        labels_and_tags=labels,
        category=lot.ct_category,
    )


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
    if is_facebook_listing_channel(getattr(lot, "listing_channel", None)):
        raise HTTPException(
            status_code=400,
            detail="This lot is marked for Facebook Marketplace — it cannot be posted to CT Seller",
        )
    if lot_is_held_from_ctseller(lot) and not force:
        raise HTTPException(
            status_code=400,
            detail="This lot is held for collector groups — turn off Hold for groups, or pass force=true",
        )

    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot.id, Photo.account_id == lot.account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc())
        .all()
    )
    listing_photos = photos_for_ctbids_listing(photos)
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
        tag=_search_tags_for_lot_row(lot),
        reserve_price=resolve_lot_reserve_price(
            lot,
            db.query(Sale).filter(Sale.id == lot.sale_id, Sale.account_id == lot.account_id).first(),
        ),
    )
    resolved_tags = _search_tags_for_lot_row(lot)
    if resolved_tags:
        lot.search_tags_json = json.dumps(resolved_tags, ensure_ascii=True)
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
    lot.ctseller_sale_id = int(remote_sale_id)
    lot.ctseller_item_status = "Pending"
    lot.ctseller_sold_price = None
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
        try:
            detail = client.sale_detail(int(remote_sale_id))
        except CTSellerError as exc:
            if exc.status_code not in (401, 403):
                raise
            client = _ctseller_client_for_account(account, db, force=True)
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
    # Never bulk-post Facebook Marketplace furniture into CT Bids.
    q = q.filter(Lot.listing_channel != LISTING_CHANNEL_FACEBOOK)
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
        try:
            detail = client.sale_detail(int(remote_sale_id))
        except CTSellerError as exc:
            if exc.status_code not in (401, 403):
                raise
            client = _ctseller_client_for_account(account, db, force=True)
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
        if lot_is_held_from_ctseller(lot) and not body.force:
            results.append(
                SalePostToCTSellerLotResult(
                    lot_id=lot.id,
                    sku=sku,
                    ok=False,
                    skipped=True,
                    error="Held for collector groups",
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
    listing_channel: str | None = Query(
        None,
        description="When set to ctseller or facebook, only lots for that listing channel.",
    ),
    sku_flags: str | None = Query(
        None,
        description="When set to duplicate, text_mismatch, or any, only rows with that SKU sanity flag.",
    ),
    ctseller_sale_id: int | None = Query(
        None,
        description="When set, only lots posted to this CT Seller / CT Bids auction id.",
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

    if listing_channel is not None and str(listing_channel).strip():
        raw_ch = str(listing_channel).strip().lower()
        if raw_ch not in (LISTING_CHANNEL_CTSELLER, LISTING_CHANNEL_FACEBOOK):
            raise HTTPException(
                status_code=400,
                detail="listing_channel must be ctseller or facebook",
            )
        q = q.filter(Lot.listing_channel == normalize_listing_channel(raw_ch))

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

    if ctseller_sale_id is not None:
        q = q.filter(Lot.ctseller_item_id.isnot(None), Lot.ctseller_sale_id == int(ctseller_sale_id))

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
        _lot_out_with_runtime(
            lot_to_schema(
                r,
                has_thumbnail=r.id in thumb_ids,
                many_photos_review=photo_count_is_sale_outlier(r.photo_count, counts),
            ),
            r,
        )
        for r in rows
    ]


@app.get("/lots/{lot_id}/media-url", response_model=CatalogMediaUrlOut)
def lot_media_url(
    lot_id: str,
    max_px: int = Query(220, description="Long edge for cached JPEG thumb (required for lot cover)."),
    generate_if_missing: bool = Query(
        False,
        description="When true, build a missing thumb on Railway. Grids must leave this false.",
    ),
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
            # Sign only — generating missing thumbs here stalls lot/dashboard loads.
            url = signed_catalog_url(
                ph.storage_path,
                max_px=px,
                expires_seconds=expires,
                generate_if_missing=generate_if_missing,
            )
            return CatalogMediaUrlOut(url=url, expires_in=expires, kind="original")
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


@app.post("/lots/move-to-sale", response_model=LotBulkMoveOut)
def move_lots_between_sales(
    body: LotBulkMoveIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LotBulkMoveOut:
    """Move one or more lots to another open sale. Photos and videos stay on the lot."""
    dest = _assert_sale_editable_for_lot_moves(db, body.sale_id, current)
    try:
        moved_rows, skipped = move_lots_to_sale(
            db,
            account_id=current.account_id,
            lot_ids=body.lot_ids,
            destination=dest,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not moved_rows:
        raise HTTPException(
            status_code=400,
            detail="Those lots are already in that sale.",
        )
    return LotBulkMoveOut(
        destination_sale_id=dest.id,
        destination_sale_name=dest.name,
        moved_count=len(moved_rows),
        skipped_already_there=skipped,
        moved=[_lot_out_for_id(db, current.account_id, row.id) for row in moved_rows],
    )


@app.post("/lots/{lot_id}/move", response_model=LotOut)
def move_one_lot_between_sales(
    lot_id: str,
    body: LotMoveIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LotOut:
    dest = _assert_sale_editable_for_lot_moves(db, body.sale_id, current)
    try:
        moved_rows, skipped = move_lots_to_sale(
            db,
            account_id=current.account_id,
            lot_ids=[lot_id],
            destination=dest,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not moved_rows:
        detail = (
            "That lot is already in this sale."
            if skipped
            else "Lot could not be moved."
        )
        raise HTTPException(status_code=400, detail=detail)
    return _lot_out_for_id(db, current.account_id, lot_id)


@app.get("/lots/{lot_id}", response_model=LotOut)
def get_lot(lot_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LotOut:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    return _lot_out_for_id(db, current.account_id, lot_id)


@app.get("/lots/{lot_id}/photos", response_model=list[LotPhotoOut])
def list_lot_photos(
    lot_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    for_listing: bool = Query(
        False,
        description="If true, omit dedicated SKU-tape frames (same filter as CT Bids posting).",
    ),
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
    if for_listing:
        photos = photos_for_ctbids_listing(photos)
    return [lot_photo_out(p, lot_id=lot_id) for p in photos]


@app.post("/lots/{lot_id}/photos/upload", response_model=list[LotPhotoOut])
async def upload_lot_photos(
    lot_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    files: list[UploadFile] = File(),
    captured_at_ms: str | None = Form(None),
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
        await save_uploaded_files(dest, files, captured_at=captured_at_from_epoch_ms(captured_at_ms))
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
                captured_at=captured_at_from_path(path) or captured_at_from_epoch_ms(captured_at_ms),
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


@app.get("/lots/{lot_id}/videos", response_model=list[LotVideoOut])
def list_lot_videos(
    lot_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LotVideoOut]:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    videos = (
        db.query(LotVideo)
        .filter(LotVideo.lot_id == lot_id, LotVideo.account_id == current.account_id)
        .order_by(LotVideo.sort_order.asc(), LotVideo.created_at.asc(), LotVideo.id.asc())
        .all()
    )
    return [lot_video_out(v, lot_id=lot_id) for v in videos]


@app.post("/lots/{lot_id}/videos/upload", response_model=list[LotVideoOut])
async def upload_lot_videos(
    lot_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    files: list[UploadFile] = File(),
) -> list[LotVideoOut]:
    """Attach short clips to a catalog lot. Not sent through Gemini or posted to CT Bids."""
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    _assert_sale_editable_for_lot_moves(db, row.sale_id, current)
    if not files:
        raise HTTPException(status_code=400, detail="No videos uploaded")

    payloads: list[tuple[str, bytes]] = []
    for file in files:
        name = Path(file.filename or "clip.mp4").name
        if not is_catalog_video_filename(name):
            raise HTTPException(
                status_code=400,
                detail=f"{name} is not a video (use MP4, MOV, M4V, or WebM).",
            )
        raw = await file.read()
        if err := lot_video_size_error(len(raw)):
            raise HTTPException(status_code=400, detail=f"{name}: {err}")
        payloads.append((name, raw))

    existing = (
        db.query(func.count())
        .select_from(LotVideo)
        .where(LotVideo.lot_id == lot_id, LotVideo.account_id == current.account_id)
        .scalar()
    )
    n_existing = int(existing or 0)
    if err := lot_video_delta_error(current=n_existing, delta=len(payloads)):
        raise HTTPException(status_code=400, detail=err)

    mx = (
        db.query(func.max(LotVideo.sort_order))
        .filter(LotVideo.lot_id == lot_id, LotVideo.account_id == current.account_id)
        .scalar()
    )
    base = (int(mx) if mx is not None else -1) + 1
    for i, (name, raw) in enumerate(payloads):
        tmp_path = Path(tempfile.mkstemp(suffix=Path(name).suffix or ".mp4")[1])
        try:
            tmp_path.write_bytes(raw)
            video_id, storage_path = persist_catalog_video(current.account_id, tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        db.add(
            LotVideo(
                id=video_id,
                account_id=current.account_id,
                user_id=current.id,
                lot_id=lot_id,
                original_name=name,
                storage_path=storage_path,
                content_type=video_content_type(name),
                byte_size=len(raw),
                sort_order=base + i,
            )
        )
    db.commit()
    videos = (
        db.query(LotVideo)
        .filter(LotVideo.lot_id == lot_id, LotVideo.account_id == current.account_id)
        .order_by(LotVideo.sort_order.asc(), LotVideo.created_at.asc(), LotVideo.id.asc())
        .all()
    )
    return [lot_video_out(v, lot_id=lot_id) for v in videos]


@app.post("/lots/{lot_id}/videos/delete", response_model=LotOut)
def delete_lot_videos(
    lot_id: str,
    body: DeleteLotVideosIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LotOut:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    _assert_sale_editable_for_lot_moves(db, row.sale_id, current)
    ids = list(dict.fromkeys(body.video_ids))
    to_delete = (
        db.query(LotVideo)
        .filter(
            LotVideo.lot_id == lot_id,
            LotVideo.account_id == current.account_id,
            LotVideo.id.in_(ids),
        )
        .all()
    )
    found_ids = {v.id for v in to_delete}
    if found_ids != set(ids):
        missing = sorted(set(ids) - found_ids)
        preview = ", ".join(missing[:5])
        raise HTTPException(status_code=400, detail=f"Video id(s) not on this lot: {preview}")
    storage_paths = [v.storage_path for v in to_delete]
    for video in to_delete:
        db.delete(video)
    db.commit()
    for sp in storage_paths:
        delete_catalog_photo(sp)
    return _lot_out_for_id(db, current.account_id, lot_id)


@app.get("/lots/{lot_id}/videos/{video_id}/media-url", response_model=CatalogMediaUrlOut)
def lot_video_media_url(
    lot_id: str,
    video_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CatalogMediaUrlOut:
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    video = (
        db.query(LotVideo)
        .filter(
            LotVideo.id == video_id,
            LotVideo.lot_id == lot_id,
            LotVideo.account_id == current.account_id,
        )
        .first()
    )
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    expires = 3600
    try:
        url = signed_catalog_url(video.storage_path, max_px=None, expires_seconds=expires)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "Video file missing") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not sign video URL: {exc}") from exc
    return CatalogMediaUrlOut(url=url, expires_in=expires, kind="original")


@app.get("/lots/{lot_id}/videos/{video_id}/stream")
def stream_lot_video(
    lot_id: str,
    video_id: str,
    token: str = Depends(oauth2_scheme),
) -> Response:
    with SessionLocal() as db:
        user = user_from_access_token(token, db)
        row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == user.account_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Lot not found")
        video = (
            db.query(LotVideo)
            .filter(
                LotVideo.id == video_id,
                LotVideo.lot_id == lot_id,
                LotVideo.account_id == user.account_id,
            )
            .first()
        )
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        storage_path = video.storage_path
        content_type = video.content_type or video_content_type(video.original_name)
        filename = video.original_name

    if catalog_r2_configured():
        try:
            url = signed_catalog_url(storage_path, max_px=None, expires_seconds=3600)
            return RedirectResponse(url=url, status_code=302)
        except Exception:
            pass
    src = resolve_catalog_storage_path(storage_path)
    if not src.is_file():
        raise HTTPException(status_code=404, detail="Video file missing on disk")
    return FileResponse(
        str(src),
        media_type=content_type or "video/mp4",
        filename=filename,
    )


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
    generate_if_missing: bool = Query(
        False,
        description="When true, build a missing 220px thumb on Railway. Grids must leave this false.",
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
        url = signed_catalog_url(
            ph.storage_path,
            max_px=px,
            expires_seconds=expires,
            generate_if_missing=generate_if_missing,
        )
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
    """One catalog photo as JPEG (downscaled). Order matches ``GET …/photos``."""
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
        purge_catalog_photo_thumbs(row.storage_path)
        _, storage_path = persist_catalog_photo(current.account_id, tmp_path, photo_id=row.id)
        row.storage_path = storage_path
        if stor_name:
            row.original_name = Path(stor_name).name
        db.commit()
        db.refresh(row)
    finally:
        tmp_path.unlink(missing_ok=True)
    return lot_photo_out(row, lot_id=lot_id)


@app.post("/lots/{lot_id}/photos/rotate", response_model=RotateLotPhotosOut)
def rotate_lot_photos(
    lot_id: str,
    body: RotateLotPhotosIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RotateLotPhotosOut:
    """Bake EXIF then rotate selected catalog files (or every photo on the lot)."""
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    _assert_sale_editable_for_lot_moves(db, lot.sale_id, current)
    existing = (
        db.query(Photo)
        .filter(Photo.lot_id == lot_id, Photo.account_id == current.account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc(), Photo.id.asc())
        .all()
    )
    if not existing:
        raise HTTPException(status_code=400, detail="This lot has no catalog photos to rotate.")
    want_ids = list(dict.fromkeys(body.photo_ids or []))
    if want_ids:
        by_id = {p.id: p for p in existing}
        missing = [pid for pid in want_ids if pid not in by_id]
        if missing:
            preview = ", ".join(missing[:5])
            more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
            raise HTTPException(
                status_code=400,
                detail=f"Photo id(s) not on this lot: {preview}{more}",
            )
        targets = [by_id[pid] for pid in want_ids]
    else:
        targets = existing

    jpeg_q = int(settings.catalog_upload_jpeg_quality)
    rotated = 0
    failed = 0
    errors: list[str] = []
    for row in targets:
        src = resolve_catalog_storage_path(row.storage_path)
        if not src.is_file():
            failed += 1
            if len(errors) < 8:
                errors.append(f"{row.original_name}: file missing")
            continue
        try:
            out = rotate_image_bytes(src.read_bytes(), int(body.degrees), quality=jpeg_q)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            if len(errors) < 8:
                errors.append(f"{row.original_name}: {exc}")
            continue
        tmp_path = Path(tempfile.mkstemp(suffix=".jpg")[1])
        try:
            tmp_path.write_bytes(out)
            purge_catalog_photo_thumbs(row.storage_path)
            _, storage_path = persist_catalog_photo(current.account_id, tmp_path, photo_id=row.id)
            row.storage_path = storage_path
            stem = Path(row.original_name or "photo").stem or "photo"
            row.original_name = f"{stem}.jpg"
            rotated += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            if len(errors) < 8:
                errors.append(f"{row.original_name}: {exc}")
        finally:
            tmp_path.unlink(missing_ok=True)

    db.commit()
    for row in targets:
        db.refresh(row)
    return RotateLotPhotosOut(
        rotated=rotated,
        failed=failed,
        photos=[lot_photo_out(p, lot_id=lot_id) for p in existing],
        errors=errors,
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
    if patch.search_tags is not None:
        from app.services.search_tags import sanitize_search_tags

        evidence = " ".join(
            [
                row.auction_title or "",
                row.professional_description or "",
                row.specification_lines_json or "",
                row.brands_json or "",
                row.ct_category or "",
            ]
        )
        row.search_tags_json = json.dumps(
            sanitize_search_tags(patch.search_tags, evidence=evidence),
            ensure_ascii=True,
        )
    if patch.starting_price is not None:
        row.starting_price = patch.starting_price
    if "reserve_price" in patch.model_fields_set:
        raw = patch.reserve_price
        if raw is None:
            row.reserve_price = None
        else:
            value = float(raw)
            row.reserve_price = value if value > 0 else None
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
    if patch.audience_hold is not None:
        row.audience_hold = bool(patch.audience_hold)
    db.commit()
    db.refresh(row)
    recompute_sku_sanity_flags_for_sale(db, row.sale_id, current.account_id)
    db.commit()
    db.refresh(row)
    return _lot_out_for_id(db, current.account_id, lot_id)


@app.post("/lots/{lot_id}/search-tags/suggest", response_model=LotSearchTagsOut)
def suggest_lot_search_tags(
    lot_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LotSearchTagsOut:
    """Fill CT Bids search tags from title, brands, material, and era — no extra Gemini call."""
    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    from app.services.search_tags import suggest_search_tags

    try:
        specs = json.loads(row.specification_lines_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        specs = []
    try:
        brands = json.loads(row.brands_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        brands = []
    try:
        labels = json.loads(row.labels_and_tags_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        labels = []
    tags = suggest_search_tags(
        title=row.auction_title or "",
        description=row.professional_description or "",
        brands=brands if isinstance(brands, list) else [],
        specification_lines=specs if isinstance(specs, list) else [],
        labels_and_tags=labels if isinstance(labels, list) else [],
        category=row.ct_category,
    )
    return LotSearchTagsOut(lot_id=row.id, search_tags=tags)


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


@app.post("/lots/{lot_id}/audience-hunt", response_model=LotOut)
def hunt_lot_audience(
    lot_id: str,
    body: LotAudienceHuntIn | None = None,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LotOut:
    """Find public collector groups and draft outreach blurbs. Does not scrape or auto-post."""
    _require_can_run_audience_hunt(current)
    from app.services.audience_hunt import apply_skip_without_model, should_run_audience_hunt

    row = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == current.account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    force = bool(body.force) if body else False
    if audience_tasks.is_hunting(db, lot_id=lot_id, account_id=current.account_id):
        raise HTTPException(status_code=409, detail="Audience hunt is already running for this lot.")
    brands: list[str] = []
    try:
        brands = [str(b) for b in json.loads(row.brands_json or "[]")]
    except json.JSONDecodeError:
        brands = []
    hunt, reason = should_run_audience_hunt(
        title=row.auction_title or "",
        brands=brands,
        category=row.ct_category,
        force=force,
    )
    if not hunt:
        apply_skip_without_model(row, reason)
        db.commit()
        return _lot_out_for_id(db, current.account_id, lot_id)
    try:
        audience_tasks.mark_lot_hunt_started(db, lot_id=lot_id, account_id=current.account_id)
    except RuntimeError as exc:
        if str(exc) == "audience_hunt_already_running":
            raise HTTPException(status_code=409, detail="Audience hunt is already running for this lot.") from exc
        raise
    try:
        audience_tasks.start_audience_hunt_in_background(
            lot_id=lot_id,
            account_id=current.account_id,
            force=force,
        )
    except RuntimeError as exc:
        audience_tasks.finish_lot_hunt(
            db,
            lot_id=lot_id,
            account_id=current.account_id,
            error=str(exc)[:2000] if str(exc) != "audience_hunt_already_running" else None,
        )
        if str(exc) == "audience_hunt_already_running":
            raise HTTPException(status_code=409, detail="Audience hunt is already running for this lot.") from exc
        raise
    return _lot_out_for_id(db, current.account_id, lot_id)


@app.post("/sales/{sale_id}/audience-hunt", response_model=SaleAudienceHuntOut)
def hunt_sale_audience(
    sale_id: str,
    body: SaleAudienceHuntIn | None = None,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaleAudienceHuntOut:
    """Queue collector-group hunts for existing lots. Does not recatalog photos or auto-post."""
    _require_can_run_audience_hunt(current)
    from app.services.audience_hunt import plan_sale_audience_hunts

    payload = body or SaleAudienceHuntIn()
    sale = _sale_for_user(sale_id, current, db)
    q = db.query(Lot).filter(Lot.account_id == current.account_id, Lot.sale_id == sale.id)
    wanted: list[str] | None = None
    if payload.lot_ids:
        wanted = [x.strip() for x in payload.lot_ids if (x or "").strip()]
        q = q.filter(Lot.id.in_(wanted))
    lots = q.order_by(Lot.created_at.asc()).all()
    if wanted:
        by_id = {r.id: r for r in lots}
        lots = [by_id[i] for i in wanted if i in by_id]

    queued_ids, skipped_rows = plan_sale_audience_hunts(
        lots,
        force=payload.force,
        only_unhunted=payload.only_unhunted,
        limit=payload.limit,
    )
    results: list[SaleAudienceHuntLotResult] = [
        SaleAudienceHuntLotResult(lot_id=lot_id, sku=sku or None, skipped=True, error=reason)
        for lot_id, sku, reason in skipped_rows
    ]
    by_id = {r.id: r for r in lots}
    started: list[str] = []
    for lot_id in queued_ids:
        lot = by_id.get(lot_id)
        sku = (lot.sku or "").strip() or None if lot is not None else None
        if audience_tasks.is_hunting(db, lot_id=lot_id, account_id=current.account_id):
            results.append(
                SaleAudienceHuntLotResult(
                    lot_id=lot_id, sku=sku, skipped=True, error="Hunt already running"
                )
            )
            continue
        try:
            audience_tasks.mark_lot_hunt_started(db, lot_id=lot_id, account_id=current.account_id)
        except RuntimeError as exc:
            results.append(
                SaleAudienceHuntLotResult(lot_id=lot_id, sku=sku, skipped=True, error=str(exc))
            )
            continue
        started.append(lot_id)
        results.append(SaleAudienceHuntLotResult(lot_id=lot_id, sku=sku, queued=True))

    if started:
        sku_map = {
            lid: ((by_id[lid].sku or "").strip() or None) if lid in by_id else None
            for lid in started
        }
        audience_tasks.start_sequential_hunts_in_background(
            account_id=current.account_id,
            sale_id=sale.id,
            lot_ids=started,
            skus=sku_map,
            force=payload.force,
        )
    skipped_n = sum(1 for r in results if r.skipped)
    return SaleAudienceHuntOut(queued=len(started), skipped=skipped_n, results=results)


@app.get("/sales/{sale_id}/audience-hunt", response_model=SaleAudienceHuntStatusOut)
def sale_audience_hunt_status(
    sale_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaleAudienceHuntStatusOut:
    """Progress for a sale-wide hunt running in this API process."""
    _sale_for_user(sale_id, current, db)
    snap = audience_tasks.sale_hunt_status(sale_id=sale_id, account_id=current.account_id)
    return SaleAudienceHuntStatusOut.model_validate(snap)


@app.delete("/lots/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lot(
    lot_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    _require_can_delete_lot(current)
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
    Delete lots, photos, videos, jobs, appraisals, hunts, and sale ZIP exports for an account or one sale.

    Does not delete ``Sale`` rows. Does **not** remove catalog JPEGs on disk — photos are stored
    inside job upload batch folders; deleting those folders would permanently destroy images even
    when Postgres is restored from backup.
    """
    lot_filter = [Lot.account_id == account_id]
    job_filter = [ProcessingJob.account_id == account_id]
    zip_filter = [SalePhotoZipExport.account_id == account_id]
    appraisal_filter = [AppraisalCheck.account_id == account_id]
    hunt_filter = [AudienceHuntEvent.account_id == account_id]
    if sale_id is not None:
        lot_filter.append(Lot.sale_id == sale_id)
        job_filter.append(ProcessingJob.sale_id == sale_id)
        zip_filter.append(SalePhotoZipExport.sale_id == sale_id)
        appraisal_filter.append(AppraisalCheck.sale_id == sale_id)
        hunt_filter.append(AudienceHuntEvent.sale_id == sale_id)

    lot_ids = [row[0] for row in db.query(Lot.id).filter(*lot_filter).all()]
    job_ids = [row[0] for row in db.query(ProcessingJob.id).filter(*job_filter).all()]

    for ex in db.query(SalePhotoZipExport).filter(*zip_filter).all():
        if ex.zip_path:
            Path(ex.zip_path).unlink(missing_ok=True)
    db.query(SalePhotoZipExport).filter(*zip_filter).delete(synchronize_session=False)

    if lot_ids:
        db.query(LotVideo).filter(
            LotVideo.account_id == account_id,
            LotVideo.lot_id.in_(lot_ids),
        ).delete(synchronize_session=False)
        db.query(Photo).filter(
            Photo.account_id == account_id,
            Photo.lot_id.in_(lot_ids),
        ).delete(synchronize_session=False)
    elif sale_id is None:
        db.query(LotVideo).filter(LotVideo.account_id == account_id).delete(synchronize_session=False)
        db.query(Photo).filter(Photo.account_id == account_id).delete(synchronize_session=False)

    db.query(AppraisalCheck).filter(*appraisal_filter).delete(synchronize_session=False)
    db.query(AudienceHuntEvent).filter(*hunt_filter).delete(synchronize_session=False)
    if job_ids:
        db.query(JobSseEvent).filter(
            JobSseEvent.account_id == account_id,
            JobSseEvent.job_id.in_(job_ids),
        ).delete(synchronize_session=False)
    elif sale_id is None:
        db.query(JobSseEvent).filter(JobSseEvent.account_id == account_id).delete(synchronize_session=False)

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
    current: User = Depends(get_current_user_sse),
    sale_id: str = Form(),
    files: list[UploadFile] = File(),
    inherit_sku_from_subfolder: bool = Form(False),
    single_lot_mode: bool = Form(False),
    single_lot_manual_sku: str = Form(""),
    listing_channel: str = Form("ctseller"),
    captured_at_ms: str | None = Form(None),
) -> JobOut:
    """Queue a job from a folder chosen in the browser (files uploaded to server disk)."""
    account_id = current.account_id
    user_id = current.id
    inherit = bool(inherit_sku_from_subfolder)
    single = bool(single_lot_mode)
    channel = normalize_listing_channel(listing_channel)
    _manual_sku = (single_lot_manual_sku or "").strip() or None
    with SessionLocal() as db:
        sale = _sale_for_user(sale_id, current, db)
        _require_open_sale(sale)
        sale_pk = sale.id
        if (
            single
            and channel != LISTING_CHANNEL_FACEBOOK
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
    dest = new_batch_dir(account_id)
    # Pillow + R2 mirror can take tens of seconds — do not pin QueuePool during that I/O
    # (auth used a short-lived session so multipart receive also does not hold a connection).
    await save_uploaded_files(dest, files, captured_at=captured_at_from_epoch_ms(captured_at_ms))
    with SessionLocal() as db2:
        job = ProcessingJob(
            account_id=account_id,
            user_id=user_id,
            sale_id=sale_pk,
            # Hold Gemini until ``POST /jobs/{id}/run`` — mobile/web may still be appending files.
            status=JobStatus.receiving,
            input_folder=str(dest.resolve()),
            inherit_sku_from_subfolder=inherit,
            single_lot_mode=single,
            single_lot_manual_sku=_manual_sku,
            listing_channel=channel,
        )
        db2.add(job)
        db2.commit()
        db2.refresh(job)
        return _job_out(job)


def _owned_processing_job(db: Session, job_id: str, account_id: str) -> ProcessingJob | None:
    return (
        db.query(ProcessingJob)
        .filter(ProcessingJob.id == job_id, ProcessingJob.account_id == account_id)
        .first()
    )


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
    current: User = Depends(get_current_user_sse),
    files: list[UploadFile] = File(),
    captured_at_ms: str | None = Form(None),
) -> JobOut:
    """Append more images to a **receiving** (or legacy queued) upload job."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    account_id = current.account_id
    with SessionLocal() as db:
        job = (
            db.query(ProcessingJob)
            .filter(ProcessingJob.id == job_id, ProcessingJob.account_id == current.account_id)
            .first()
        )
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in (JobStatus.receiving, JobStatus.queued):
            raise HTTPException(
                status_code=409,
                detail="Job must still be receiving uploads to add files (wait if it is processing, or cancel first).",
            )
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
    # Compress + R2 per photo while the phone is still shooting — no pool connection during body I/O.
    await save_uploaded_files(dest, files, captured_at=captured_at_from_epoch_ms(captured_at_ms))
    with SessionLocal() as db2:
        job = _owned_processing_job(db2, job_id, account_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        # Keep ``receiving`` jobs from looking stale while the phone is still appending.
        job.updated_at = datetime.now(timezone.utc)
        db2.commit()
        db2.refresh(job)
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
        listing_channel=normalize_listing_channel(payload.listing_channel),
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
    # Leftover receiving/FAILED healing is debounced — this endpoint is polled every few seconds
    # by the dashboard and phone Sync. Full reconcile on every poll exhausted QueuePool in the field.
    changed = job_tasks.maybe_reconcile_jobs_for_list(db, current.account_id)
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
def run_job(
    job_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    force_in_process: bool = Query(
        False,
        description=(
            "When true (dashboard Run now), start catalog on this API process even if a "
            "dedicated worker normally owns jobs. Phone Sync should leave this false."
        ),
    ),
) -> JobOut:
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
        job.error_message = None
        job.cancel_requested = False
        db.commit()
    elif job.status == JobStatus.receiving:
        # Stay receiving until R2 staging finishes so the worker cannot claim a half-copied batch.
        pass
    elif job.status != JobStatus.queued:
        raise HTTPException(status_code=409, detail="Job must be receiving, queued, or failed to run")

    from app.job_batch_storage import count_staged_job_files, mirror_folder_to_r2

    # Dedicated worker mode: phone Sync leaves the row queued for ``app.worker``.
    # Dashboard **Run now** passes force_in_process so staff are not stuck if the worker is wedged.
    run_remote = not job_tasks.catalog_jobs_run_in_process() and not force_in_process
    folder = Path(job.input_folder)
    account_id = current.account_id
    # R2 of a full lot can take longer than pool_timeout. Return this connection first
    # (QueuePool 10+20 was exhausting while phones waited on /run).
    db.close()

    if run_remote:
        # The worker runs in a separate container with its own disk; it can only read this
        # batch from R2. Stage it durably and VERIFY it landed, so a storage/config problem
        # (e.g. wrong R2 key) surfaces as a real error the phone retries on — instead of a
        # silent "queued" job the worker can never read ("Input folder not found").
        local_images = count_catalog_image_files(folder)
        try:
            mirror_folder_to_r2(folder)
        except Exception:  # noqa: BLE001
            pass
        staged = count_staged_job_files(folder)
        with SessionLocal() as db2:
            job = _owned_processing_job(db2, job_id, account_id)
            if not job:
                raise HTTPException(status_code=404, detail="Job not found")
            if not catalog_r2_configured():
                job.status = JobStatus.failed
                job.error_message = (
                    "Photos could not be staged for the background worker: object storage (R2) is not "
                    "configured on the server. Your photos are safe on the phone — retry after storage is set up."
                )
                db2.commit()
                raise HTTPException(
                    status_code=503,
                    detail=job.error_message,
                )
            if staged == 0 or (local_images and staged < local_images):
                job.status = JobStatus.failed
                job.error_message = (
                    f"Photos could not be staged for the background worker ({staged} of {local_images} reached "
                    "storage). Your photos are safe on the phone — tap Sync to retry."
                )
                db2.commit()
                raise HTTPException(
                    status_code=503,
                    detail=job.error_message,
                )
            if job.status in (JobStatus.receiving, JobStatus.failed, JobStatus.queued):
                job.status = JobStatus.queued
                job.error_message = None
                db2.commit()
                db2.refresh(job)
            job_tasks.schedule_worker_claim_fallback(account_id, job.id)
            return _job_out(job)

    # In-process (or forced Run now): files are on this host; mirror best-effort for durability.
    try:
        mirror_folder_to_r2(folder)
    except Exception:
        pass

    with SessionLocal() as db2:
        job = _owned_processing_job(db2, job_id, account_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status in (JobStatus.receiving, JobStatus.failed):
            job.status = JobStatus.queued
            job.error_message = None
            db2.commit()
            db2.refresh(job)

        if force_in_process and not job_tasks.can_start_job_for_account(account_id):
            freed = job_tasks.requeue_stale_processing_for_account(
                db2, account_id, min_age_seconds=10 * 60
            )
            if freed:
                db2.commit()

        job_tasks.kick_queue_if_idle(account_id)
        started = False
        try:
            started = job_tasks.start_job_in_background(
                job.id, account_id, force_in_process=force_in_process
            )
        except RuntimeError as exc:
            if str(exc) != "job_already_running":
                raise
            job_tasks.reconcile_memory_threads_for_account(account_id)
            try:
                started = job_tasks.start_job_in_background(
                    job.id, account_id, force_in_process=force_in_process
                )
            except RuntimeError as retry_exc:
                if str(retry_exc) != "job_already_running":
                    raise
                raise HTTPException(status_code=409, detail="Job is already running") from retry_exc
        if not started:
            job_tasks.kick_queue_if_idle(account_id)
            db2.refresh(job)
            if job.status == JobStatus.processing:
                started = True
            else:
                if force_in_process:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Could not start catalog on the API — another job may still be processing. "
                            "Wait a minute, or restart the catalog worker service on Railway."
                        ),
                    )
                return _job_out(job)
        # Worker sets DB to processing asynchronously; return processing so the client
        # starts status polling immediately instead of waiting on the first GET /jobs refresh.
        db2.expire(job)
        db2.refresh(job)
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
    Events are persisted by the catalog worker and replayed from Postgres so the dashboard still
    sees live progress when Gemini runs in a separate Railway service (in-memory pub/sub cannot
    cross processes).
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

    account_id = current.account_id

    async def event_generator():
        last_id = 0
        yield {"data": json.dumps({"type": "connected", "job_id": job_id}, ensure_ascii=False)}
        await asyncio.sleep(0)
        while True:
            if await request.is_disconnected():
                break
            rows = await asyncio.to_thread(job_events.list_events_after, account_id, job_id, last_id)
            if not rows:
                await asyncio.sleep(1.0)
                continue
            stop = False
            for event_id, line in rows:
                last_id = event_id
                yield {"data": line}
                await asyncio.sleep(0)
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "status" and obj.get("status") in ("completed", "failed"):
                    stop = True
                    break
            if stop:
                break

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


@app.get("/exports/inventory-pdf")
def export_inventory_pdf(
    sale_id: str = Query(..., description="Catalog sale to print."),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """SKU + description + thumbnail sheet for one catalog sale."""
    sale = _sale_for_user(sale_id, current, db)
    body = build_sale_inventory_pdf(db, sale=sale, account_id=current.account_id)
    raw = re.sub(r"[^A-Za-z0-9._-]+", "-", (sale.name or "sale").strip())[:80].strip("-") or "sale"
    return Response(
        content=body,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{raw}-inventory.pdf"'},
    )


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
        kind=normalize_sale_zip_kind(getattr(row, "kind", None)),
        filter_ctseller_sale_id=getattr(row, "filter_ctseller_sale_id", None),
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
    kind: str = Query("all", description="all = every photo in SKU folders; heroes = one group shot per lot."),
    ctseller_sale_id: int | None = Query(
        None,
        description="When set, only lots posted to this CT Seller auction (heroes for one CT Bids sale).",
    ),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaleZipExportOut:
    """Queue a background ZIP of catalog photos for lots in this sale (600–3000+ images)."""
    sale = _sale_for_user(sale_id, current, db)
    raw_kind = str(kind or ZIP_KIND_ALL).strip().lower() or ZIP_KIND_ALL
    if raw_kind not in (ZIP_KIND_ALL, ZIP_KIND_HEROES):
        raise HTTPException(status_code=400, detail="kind must be 'all' or 'heroes'")
    zip_kind = raw_kind
    filter_sid = int(ctseller_sale_id) if ctseller_sale_id is not None else None
    if filter_sid is not None and filter_sid <= 0:
        raise HTTPException(status_code=400, detail="ctseller_sale_id must be a positive integer")
    cleanup_expired_sale_zip_exports(db, current.account_id)
    in_progress_q = db.query(SalePhotoZipExport).filter(
        SalePhotoZipExport.sale_id == sale.id,
        SalePhotoZipExport.account_id == current.account_id,
        SalePhotoZipExport.status.in_((JobStatus.queued, JobStatus.processing)),
        SalePhotoZipExport.kind == zip_kind,
    )
    if filter_sid is None:
        in_progress_q = in_progress_q.filter(SalePhotoZipExport.filter_ctseller_sale_id.is_(None))
    else:
        in_progress_q = in_progress_q.filter(SalePhotoZipExport.filter_ctseller_sale_id == filter_sid)
    in_progress = in_progress_q.order_by(SalePhotoZipExport.created_at.desc()).first()
    if in_progress is not None:
        _reconcile_stale_sale_zip_export(db, in_progress)
        db.refresh(in_progress)
        if in_progress.status in (JobStatus.queued, JobStatus.processing):
            return _sale_zip_export_out(in_progress)

    n = int(
        count_sale_hero_photos(db, sale.id, current.account_id, filter_sid)
        if zip_kind == ZIP_KIND_HEROES
        else count_sale_catalog_photos(db, sale.id, current.account_id, filter_sid)
    )
    if n <= 0:
        if filter_sid is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No posted lots with photos for that CT Bids sale in this catalog. "
                    "Use “Check CT Bids sales” first, then pick the auction those lots were posted to."
                ),
            )
        raise HTTPException(status_code=400, detail="No catalog photos in this sale to export.")
    row = SalePhotoZipExport(
        account_id=current.account_id,
        user_id=current.id,
        sale_id=sale.id,
        status=JobStatus.queued,
        total_photos_expected=n,
        photo_files_written=0,
        kind=zip_kind,
        filter_ctseller_sale_id=filter_sid,
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
    suffix = "heroes" if normalize_sale_zip_kind(getattr(row, "kind", None)) == ZIP_KIND_HEROES else "photos"
    filter_sid = getattr(row, "filter_ctseller_sale_id", None)
    if filter_sid:
        suffix = f"{suffix}-ctbids-{int(filter_sid)}"
    return FileResponse(str(p), media_type="application/zip", filename=f"sale-{slug}-{suffix}.zip")


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

