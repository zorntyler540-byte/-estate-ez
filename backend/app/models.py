from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class JobStatus(str, enum.Enum):
    """Catalog / export job lifecycle.

    ``receiving`` = multipart upload still in progress (do not start Gemini yet).
    ``queued`` = ready for a worker / in-process runner (set by ``POST /jobs/{id}/run``).
    """

    receiving = "receiving"
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class Sale(Base):
    """A client-facing sale / auction event; lots and jobs are scoped to one sale."""

    __tablename__ = "sales"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Numeric sale id on seller.ctbids.com (maps this catalog sale → CT Seller auction).
    ctseller_sale_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # JSON list of up to 4 catalog Photo.id values used as CT Seller sale cover photos
    # (slot 0 = primary landscape, slots 1–3 = secondary).
    ctseller_cover_photo_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Hidden CT Seller reserve applied to lots in this sale when posting (and as the apply-all default).
    ctseller_reserve_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_code: Mapped[str] = mapped_column(String(40), default="beta_free", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Auction Watcher switches. Full-catalog plans ignore these.
    feature_analytics: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="1")
    feature_facebook: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="1")
    ctseller_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ctseller_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    ctseller_access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    ctseller_refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Facebook Page (Graph API). One Page per account in v1; Instagram would be a sibling connection.
    facebook_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    facebook_user_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    facebook_user_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    facebook_page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    facebook_page_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    facebook_page_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    facebook_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    facebook_token_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When true, new in-window Push-calendar slots are scheduled as Page posts without a second click.
    facebook_calendar_auto_approve: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="account")
    lots: Mapped[list["Lot"]] = relationship(back_populates="account")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(30), default="owner")
    # Client logins may upload photos only into this sale. Null for owners and cataloggers.
    # Plain column (no FK) so users and sales do not form a circular foreign key.
    assigned_sale_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Incremented on password change and logout-all so previously issued JWTs stop working.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Client logins only. Null means the login does not expire on its own.
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped["Account"] = relationship(back_populates="users")
    creator_jobs: Mapped[list["ProcessingJob"]] = relationship(back_populates="creator")


class ClientInvite(Base):
    """One-time client upload link. Only the SHA-256 of the token is stored."""

    __tablename__ = "client_invites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    sale_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Lot(Base):
    __tablename__ = "lots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True, nullable=False)
    sku: Mapped[str | None] = mapped_column(String(40), index=True)
    auction_title: Mapped[str] = mapped_column(String(500), default="")
    professional_description: Mapped[str] = mapped_column(Text, default="")
    specification_lines_json: Mapped[str] = mapped_column(Text, default="[]")
    brands_json: Mapped[str] = mapped_column(Text, default="[]")
    labels_and_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    # CT Seller item.tag search keywords (not apparel size tags in labels_and_tags_json).
    search_tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    ct_category: Mapped[str | None] = mapped_column(String(120))
    ct_subcategory: Mapped[str | None] = mapped_column(String(120))
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    starting_price: Mapped[float | None] = mapped_column(Float)
    # Hidden CT Seller reserve. Sale apply-all stamps this; post uses lot then sale default.
    reserve_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    condition_text: Mapped[str] = mapped_column(String(255), default="")
    sku_needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sku_duplicate_in_sale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sku_text_mismatch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    photo_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_regenerate_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_regenerate_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ctseller_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ctseller_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # CT Seller / CT Bids auction this lot was posted to (can differ from Sale.ctseller_sale_id after moves).
    ctseller_sale_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Last known CT Seller itemstatus (Pending / Ready / Sold / Unsold / …).
    ctseller_item_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Hammer / winning bid when CT Seller reports the lot sold.
    ctseller_sold_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # ctseller (CT Bids) vs facebook (Marketplace) — never mix Facebook into CT Seller posts.
    listing_channel: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ctseller", server_default="ctseller"
    )
    # Keep niche lots off bulk CT Bids post until staff un-holds after group outreach.
    audience_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    audience_hunting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    audience_hunt_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    # Latest Watcher engagement snapshot (views, watchers, bids, bucket). No bidder PII.
    engagement_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    account: Mapped["Account"] = relationship(back_populates="lots")
    owner: Mapped["User"] = relationship()
    photos: Mapped[list["Photo"]] = relationship(back_populates="lot")
    videos: Mapped[list["LotVideo"]] = relationship(back_populates="lot")


class Photo(Base):
    __tablename__ = "photos"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    lot_id: Mapped[str | None] = mapped_column(ForeignKey("lots.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(600), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Triage role from pipeline: hero|item_front|angle_only|tape|unusable (nullable for legacy rows).
    photo_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Listing-sort extras from Stage-1 triage (nullable for lots cataloged before these columns).
    distinct_item_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_primary_front: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    shows_whole_lot: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    shows_measuring_scale: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    label_legibility: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Original catalog file was saved before a Janet furniture plain-background pass.
    plain_bg_saved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # Camera / shutter time when known (EXIF or phone capture). created_at is server ingest.
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lot: Mapped["Lot"] = relationship(back_populates="photos")
    owner: Mapped["User"] = relationship()


class LotVideo(Base):
    """Phone/web clip attached to a catalog lot. Not sent through Gemini. Post to CT Seller sends it to the item media tab."""

    __tablename__ = "lot_videos"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    lot_id: Mapped[str] = mapped_column(ForeignKey("lots.id"), index=True, nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(600), nullable=False)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False, default="video/mp4")
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lot: Mapped["Lot"] = relationship(back_populates="videos")
    owner: Mapped["User"] = relationship()


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True, nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    input_folder: Mapped[str] = mapped_column(String(600), nullable=False)
    inherit_sku_from_subfolder: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    single_lot_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    single_lot_manual_sku: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Phone lot-draft UUID from Expo Queue/Sync (``POST /jobs/upload``). Optional for older app builds.
    client_draft_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    listing_channel: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ctseller", server_default="ctseller"
    )
    analyzed_photos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_photos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    est_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    est_output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Actual Gemini token usage captured from response.usage_metadata, split by model tier so cost
    # can be priced exactly (Flash = triage/listing, Pro = author). 0 for jobs cataloged before metering.
    actual_flash_input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    actual_flash_output_tokens: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    actual_pro_input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    actual_pro_output_tokens: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    # Live dashboard progress (worker writes these; API reads them — in-memory SSE cannot cross Railway services).
    progress_phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    progress_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    progress_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    creator: Mapped["User"] = relationship(back_populates="creator_jobs")


class JobSseEvent(Base):
    """Durable catalog-job SSE payloads so the API can stream progress the worker published."""

    __tablename__ = "job_sse_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppraisalCheck(Base):
    """Job-site spot appraisal (separate from catalog lots/photos)."""

    __tablename__ = "appraisal_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True, nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, nullable=False)
    scene_note: Mapped[str | None] = mapped_column(Text)
    photo_paths_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    original_names_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text)
    est_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    est_output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SalePhotoZipExport(Base):
    """Background-built ZIP of all catalog photos for a sale (large downloads)."""

    __tablename__ = "sale_photo_zip_exports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True, nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, nullable=False)
    zip_path: Mapped[str | None] = mapped_column(String(700), nullable=True)
    total_photos_expected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    photo_files_written: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ``all`` = every catalog photo in SKU+title folders. ``heroes`` = one group/hero shot per lot.
    kind: Mapped[str] = mapped_column(String(20), default="all", nullable=False, server_default="all")
    # When set, ZIP only includes lots posted to this CT Seller auction (not every lot in the Estate-EZ sale).
    filter_ctseller_sale_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # When true, ignore sale_id and include every catalog on the account posted to filter_ctseller_sale_id.
    across_sales: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CtSellerHistoryItem(Base):
    """One CT Seller item from a pulled sale-history snapshot (closed or still open)."""

    __tablename__ = "ctseller_history_items"
    __table_args__ = (
        UniqueConstraint("account_id", "ctseller_item_id", name="uq_ctseller_history_item"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    ctseller_sale_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    ctseller_item_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    sale_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    sale_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    sale_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(80), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(120), nullable=False, default="Uncategorized")
    subcategory: Mapped[str | None] = mapped_column(String(120), nullable=True)
    item_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sold_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    lot_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EngagementSnapshot(Base):
    """Latest live counts for one CT Bids item. Replaced on each Watcher refresh.

    Bidder names, emails, phones, buyer ids, and invoices are not stored.
    """

    __tablename__ = "engagement_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "ctseller_sale_id",
            "ctseller_item_id",
            name="uq_engagement_snapshot_item",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    ctseller_sale_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    ctseller_item_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    sku: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    item_url: Mapped[str] = mapped_column(String(600), nullable=False, default="")
    item_status: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    watchers: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    bids: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    reserve: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    bucket: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    engagement_score: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    momentum: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    late_surge: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    trajectory: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    rescue_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    push_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lot_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    sale_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    auction_url: Mapped[str] = mapped_column(String(600), nullable=False, default="")
    sale_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AudienceHuntEvent(Base):
    """One audience hunt attempt (skip, success, or Gemini failure) for admin cost analytics."""

    __tablename__ = "audience_hunt_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    lot_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    sale_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    skipped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    skip_reason: Mapped[str | None] = mapped_column(String(400), nullable=True)
    worthy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    recommend_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    group_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    search_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    actual_pro_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    actual_pro_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FacebookPagePost(Base):
    """One Facebook Page feed post (or scheduled post) for catalog lot(s).

    Push-calendar rows keep an intended spreadsheet platform; v1 still stores a
    Page feed post here. Native Instagram / Stories / TikTok publishers should
    get their own tables rather than reuse this row.
    """

    __tablename__ = "facebook_page_posts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    lot_id: Mapped[str | None] = mapped_column(ForeignKey("lots.id"), index=True, nullable=True)
    lot_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    photo_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    facebook_page_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    facebook_post_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    caption: Mapped[str] = mapped_column(Text, nullable=False, default="")
    include_follow_hook: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="lot", server_default="lot")
    calendar_key: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    calendar_sale_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="failed", server_default="failed")
    scheduled_publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TrackedLink(Base):
    """Short Estate-EZ redirect for a CT Bids URL used in a Facebook caption.

    Destination is stored at create time. ``/go/<code>`` only redirects to this
    URL — never to a caller-supplied location.
    """

    __tablename__ = "tracked_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    sale_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    lot_id: Mapped[str | None] = mapped_column(ForeignKey("lots.id"), index=True, nullable=True)
    facebook_post_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    calendar_key: Mapped[str] = mapped_column(String(120), nullable=False, default="", server_default="")
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="page_post", server_default="page_post")
    destination_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="lot", server_default="lot")
    destination_url: Mapped[str] = mapped_column(String(600), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FacebookOAuthState(Base):
    """One-time Facebook Login state bound to the owner who clicked Connect."""

    __tablename__ = "facebook_oauth_states"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrackedLinkDailyCount(Base):
    """Pacific-day click totals per tracked link (report source of truth)."""

    __tablename__ = "tracked_link_daily_counts"
    __table_args__ = (UniqueConstraint("link_id", "day_pacific", name="uq_tracked_link_daily"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    link_id: Mapped[str] = mapped_column(ForeignKey("tracked_links.id"), index=True, nullable=False)
    account_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    sale_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    lot_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    facebook_post_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    calendar_key: Mapped[str] = mapped_column(String(120), nullable=False, default="", server_default="")
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="page_post", server_default="page_post")
    destination_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="lot", server_default="lot")
    day_pacific: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class TrackedLinkClick(Base):
    """One counted redirect through a tracked Facebook link. No raw IPs."""

    __tablename__ = "tracked_link_clicks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    link_id: Mapped[str] = mapped_column(ForeignKey("tracked_links.id"), index=True, nullable=False)
    account_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    sale_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    lot_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    facebook_post_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    calendar_key: Mapped[str] = mapped_column(String(120), nullable=False, default="", server_default="")
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="page_post", server_default="page_post")
    destination_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="lot", server_default="lot")
    destination_url: Mapped[str] = mapped_column(String(600), nullable=False)
    referrer: Mapped[str] = mapped_column(String(400), nullable=False, default="", server_default="")
    clicked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class OpsSettings(Base):
    """Per-account rates for franchise operations quotes (not catalog Account fields)."""

    __tablename__ = "ops_settings"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    location_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    owner_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    bill_rate: Mapped[float] = mapped_column(Float, nullable=False, default=75)
    labor_target: Mapped[float] = mapped_column(Float, nullable=False, default=0.33)
    burden_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)
    royalty_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.06)
    deposit_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)
    card_fee_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.04)
    commission_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.35)
    minimum_fee: Mapped[float] = mapped_column(Float, nullable=False, default=2500)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OpsFactor(Base):
    """Cubic-feet-per-hour pace factor for one service × density cell."""

    __tablename__ = "ops_factors"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    service: Mapped[str] = mapped_column(String(32), primary_key=True)
    density: Mapped[str] = mapped_column(String(16), primary_key=True)
    factor: Mapped[float] = mapped_column(Float, nullable=False)


class OpsEmployee(Base):
    """Crew pay rates used for settlement labor cost (not shown on the client proposal)."""

    __tablename__ = "ops_employees"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    pay_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OpsJob(Base):
    """A franchise operations job (consult / quote / settlement), not a catalog processing job."""

    __tablename__ = "ops_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    address: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    zip: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    cuft: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    density: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    liquidation: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    include_sorting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    include_cleanout: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    include_packing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    include_resettling: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    space_plan_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    space_plan_areas: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    space_plan_items: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    projected_proceeds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    projected_lots: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    auction_questions: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    pickup_days: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    pickup_staff: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    pickup_hours_per_day: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    sale_days: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    sale_staff: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    sale_hours_per_day: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    sale_id: Mapped[str | None] = mapped_column(
        ForeignKey("sales.id", ondelete="SET NULL"), index=True, nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="quoted", server_default="quoted")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    quote_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    settlement: Mapped["OpsSettlement | None"] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )


class OpsSettlement(Base):
    """Actual hours and proceeds for a settled operations job."""

    __tablename__ = "ops_settlements"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("ops_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    actual_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    in_person_proceeds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    online_proceeds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    buyer_premium: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    shipping: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    misc_revenue: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    direct_expenses: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    deposit_received: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    balance_received: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    hourly_labor_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    commission_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.35)
    minimum_fee: Mapped[float] = mapped_column(Float, nullable=False, default=2500)
    royalty_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.06)
    settled_on: Mapped[str] = mapped_column(String(10), nullable=False)

    job: Mapped["OpsJob"] = relationship(back_populates="settlement")


class OpsMonthFigures(Base):
    """Consultations and marketing spend for one calendar month."""

    __tablename__ = "ops_month_figures"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    consultations: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    marketing_spend: Mapped[float] = mapped_column(Float, nullable=False, default=0)

