from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.services.plain_background import PLAIN_BG_MAX_PHOTOS


class HealthResponse(BaseModel):
    status: str
    enable_appraiser: bool = False
    appraiser_max_photos: int = 3
    gemini_pro_model: str = ""
    gemini_flash_model: str = ""
    catalog_upload_root: str = ""
    catalog_image_files_on_disk: int = 0
    catalog_r2_configured: bool = False


class RegisterRequest(BaseModel):
    account_name: str = Field(min_length=2)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserSummary(BaseModel):
    id: str
    account_id: str
    email: EmailStr
    role: str
    assigned_sale_id: str | None = None
    assigned_sale_name: str | None = None


class TeamMemberCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    role: str = Field(default="catalogger", pattern="^(owner|catalogger|client)$")
    # Required when role is client. Ignored for owner and catalogger.
    sale_id: str | None = None


class TeamMemberSaleUpdate(BaseModel):
    sale_id: str = Field(min_length=1)


class TeamMemberOut(BaseModel):
    id: str
    email: EmailStr
    role: str
    created_at: datetime
    assigned_sale_id: str | None = None
    assigned_sale_name: str | None = None


class TeamActivityPhotoTimeOut(BaseModel):
    at: datetime
    source: str = "upload"


class TeamActivityLotOut(BaseModel):
    lot_id: str
    sale_id: str
    sku: str | None = None
    auction_title: str = ""
    photo_count: int
    capture_minutes: float | None = None
    first_photo_at: datetime
    last_photo_at: datetime
    has_thumbnail: bool = True
    timing_source: str = "upload"
    photo_times: list[TeamActivityPhotoTimeOut] = []


class TeamActivityMemberOut(BaseModel):
    user_id: str
    email: EmailStr
    role: str
    photos_uploaded: int
    lots_created: int
    jobs_started: int
    active_hours: float = 0
    photos_per_hour: float = 0
    lots_per_hour: float = 0
    lots_posted: int = 0
    lots_sold: int = 0
    lots_unsold: int = 0
    lots_live: int = 0
    lots_pulled: int = 0
    sold_revenue: float = 0
    first_photo_at: datetime | None = None
    last_photo_at: datetime | None = None
    last_lot_at: datetime | None = None
    lots_photographed: int = 0
    median_minutes_per_lot: float | None = None
    mean_minutes_per_lot: float | None = None
    lots: list[TeamActivityLotOut] = Field(default_factory=list)


class TeamActivityTotalsOut(BaseModel):
    photos_uploaded: int
    lots_created: int
    jobs_started: int
    member_count: int
    active_hours: float = 0
    photos_per_hour: float = 0
    lots_per_hour: float = 0
    lots_posted: int = 0
    lots_sold: int = 0
    lots_unsold: int = 0
    lots_live: int = 0
    lots_pulled: int = 0
    sold_revenue: float = 0
    lots_photographed: int = 0
    median_minutes_per_lot: float | None = None
    mean_minutes_per_lot: float | None = None


class TeamActivityOut(BaseModel):
    since: datetime
    until: datetime
    members: list[TeamActivityMemberOut]
    totals: TeamActivityTotalsOut
    sold_items: list[SoldDocketItemOut] = Field(default_factory=list)
    sold_by_category: list[SoldCategoryRollupOut] = Field(default_factory=list)


class SoldDocketItemOut(BaseModel):
    lot_id: str
    sale_id: str
    sale_name: str = ""
    sku: str | None = None
    auction_title: str = ""
    category: str = "Uncategorized"
    subcategory: str | None = None
    outcome: str = "sold"
    sold_price: float = 0
    posted_at: datetime | None = None
    user_email: str = ""
    source: str = "catalog"
    ctseller_item_id: int | None = None


class SoldCategoryRollupOut(BaseModel):
    category: str
    posted_count: int = 0
    sold_count: int
    unsold_count: int = 0
    live_count: int = 0
    revenue: float
    avg_price: float
    min_price: float
    max_price: float
    sell_through: float = 0


class AdminAccountOut(BaseModel):
    id: str
    name: str
    owner_email: EmailStr | None = None
    plan_code: str
    is_active: bool
    created_at: datetime
    user_count: int = 0
    sale_count: int = 0
    lot_count: int = 0
    job_count: int = 0


class AdminAccountUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    is_active: bool | None = None
    plan_code: str | None = Field(default=None, min_length=2, max_length=40)


class AdminUsageAccountOut(BaseModel):
    account_id: str
    account_name: str
    jobs_started: int
    jobs_completed: int
    jobs_failed: int
    # Jobs still moving through the pipeline (receiving/queued/processing) in the window.
    jobs_in_progress: int = 0
    photos_uploaded: int
    lots_created: int
    lots_posted: int = 0
    lots_sold: int = 0
    lots_unsold: int = 0
    lots_live: int = 0
    lots_pulled: int = 0
    sold_revenue: float = 0.0
    avg_photos_per_job: float
    avg_job_duration_seconds: float
    # completed / (completed + failed), 0..1; 0 when no finished jobs.
    success_rate: float = 0.0
    est_input_tokens: int
    est_output_tokens: int
    # Cost split by model tier (Flash = triage + listing, Pro = author pass).
    est_flash_cost_usd: float = 0.0
    est_pro_cost_usd: float = 0.0
    est_total_cost_usd: float = 0.0
    # Unit economics for per-client pricing.
    est_cost_per_lot_usd: float = 0.0
    est_cost_per_photo_usd: float = 0.0
    avg_tokens_per_job: int = 0
    # Jobs whose cost is real metered usage (vs estimated). metered == jobs_started means fully measured.
    metered_jobs: int = 0
    # Distinct calendar days (UTC) with at least one job in the window.
    active_days: int = 0
    last_activity_at: datetime | None = None
    # Audience hunt (Gemini Pro + Google Search) — separate from catalog jobs.
    hunts_run: int = 0
    hunts_skipped: int = 0
    hunts_failed: int = 0
    hunt_groups_found: int = 0
    hunt_search_calls: int = 0
    hunt_pro_input_tokens: int = 0
    hunt_pro_output_tokens: int = 0
    hunt_pro_cost_usd: float = 0.0


class AdminUsageSummaryOut(BaseModel):
    since_days: int
    total_accounts: int
    totals: AdminUsageAccountOut
    by_account: list[AdminUsageAccountOut]


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)


class PasswordResetIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=72)


class SuggestionIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


class SuggestionSavedOut(BaseModel):
    saved_at: str
    subject: str = ""
    user_email: str = ""
    user_id: str = ""
    account_id: str = ""
    role: str = ""
    message: str = ""


class SaleOut(BaseModel):
    id: str
    account_id: str
    user_id: str
    name: str
    closed_at: datetime | None = None
    ctseller_sale_id: int | None = None
    ctseller_reserve_price: float | None = None
    created_at: datetime


class SaleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    ctseller_sale_id: int | None = None


class SaleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_closed: bool | None = None
    ctseller_sale_id: int | None = None
    clear_ctseller_sale_id: bool = False
    ctseller_reserve_price: float | None = Field(default=None, ge=0, le=1_000_000)
    clear_ctseller_reserve_price: bool = False


class SaleMergeIn(BaseModel):
    """Move every lot and job from ``source_sale_id`` into the path sale, then delete the source."""

    source_sale_id: str = Field(min_length=1, max_length=36)


class SaleMergeOut(BaseModel):
    target_sale_id: str
    target_sale_name: str
    source_sale_id: str
    source_sale_name: str
    lots_moved: int
    jobs_moved: int
    appraisals_moved: int = 0
    zip_exports_moved: int = 0
    duplicate_sku_count: int = 0


class RemoveSaleIn(BaseModel):
    """Must match the sale name exactly (trimmed) so a mis-click cannot wipe a catalog."""

    confirm_name: str = Field(min_length=1, max_length=255)


class RemoveSaleOut(BaseModel):
    deleted_sale_id: str
    deleted_sale_name: str
    lots_deleted: int
    jobs_deleted: int


class LotOut(BaseModel):
    id: str
    account_id: str
    user_id: str
    sale_id: str
    sku: str | None
    auction_title: str
    professional_description: str
    specification_lines: list[str]
    brands: list[str]
    labels_and_tags: list[str]
    search_tags: list[str] = []
    ct_category: str | None = None
    ct_subcategory: str | None = None
    is_completed: bool = False
    completed_at: datetime | None = None
    starting_price: float | None
    reserve_price: float | None = None
    condition_text: str
    photo_count: int
    video_count: int = 0
    has_thumbnail: bool = False
    created_at: datetime
    sku_needs_review: bool = False
    many_photos_review: bool = False
    sku_duplicate_in_sale: bool = False
    sku_text_mismatch: bool = False
    # True when the auction title/description look unusable (placeholder or garbled tag OCR) and a
    # human should rewrite before export. Derived at read time; see ``listing_quality_issue``.
    listing_needs_review: bool = False
    listing_review_reason: str | None = None
    ai_regenerating: bool = False
    ai_regenerate_error: str | None = None
    ctseller_item_id: int | None = None
    ctseller_posted_at: datetime | None = None
    ctseller_sale_id: int | None = None
    ctseller_item_status: str | None = None
    ctseller_sold_price: float | None = None
    listing_channel: str = "ctseller"
    audience_hold: bool = False
    audience_hunting: bool = False
    audience_hunt_error: str | None = None
    audience_hunt_phase: str | None = None
    audience: dict | None = None
    engagement: "EngagementOut | None" = None


class LotUpdate(BaseModel):
    auction_title: str | None = None
    professional_description: str | None = None
    specification_lines: list[str] | None = None
    brands: list[str] | None = None
    labels_and_tags: list[str] | None = None
    search_tags: list[str] | None = None
    starting_price: float | None = None
    reserve_price: float | None = None
    condition_text: str | None = None
    is_completed: bool | None = None
    sku: str | None = None
    audience_hold: bool | None = None


class LotSearchTagsOut(BaseModel):
    lot_id: str
    search_tags: list[str]


class LotMoveIn(BaseModel):
    sale_id: str = Field(min_length=1, max_length=36)


class LotBulkMoveIn(BaseModel):
    lot_ids: list[str] = Field(min_length=1, max_length=200)
    sale_id: str = Field(min_length=1, max_length=36)


class LotBulkMoveOut(BaseModel):
    destination_sale_id: str
    destination_sale_name: str
    moved_count: int
    skipped_already_there: list[str] = []
    moved: list[LotOut]


class CTSellerCredentialsIn(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class CTSellerStatusOut(BaseModel):
    connected: bool
    username: str | None = None


class CTSellerSessionOut(BaseModel):
    """Desktop Watcher session: CT Seller JWTs only — never the CT Seller password."""

    access_token: str
    refresh_token: str = ""
    username: str | None = None
    connected: bool = True


class CTSellerRemoteSaleOut(BaseModel):
    id: int
    title: str
    status: str | None = None
    starttime: str | None = None
    endtime: str | None = None


class CtsellerSaleSyncOut(BaseModel):
    """Open CT Seller auctions mirrored into Estate-EZ Change sale tabs."""

    connected: bool = True
    created: int = 0
    renamed: int = 0
    already_mapped: int = 0
    remote_count: int = 0
    remote_sales: list[CTSellerRemoteSaleOut] = Field(default_factory=list)
    sales: list[SaleOut] = Field(default_factory=list)


class CtSellerHistorySyncOut(BaseModel):
    ended_sales: int = 0
    open_sales: int = 0
    items_upserted: int = 0
    matched_lots: int = 0


class CTSellerPostedRefreshIn(BaseModel):
    ctseller_sale_id: int | None = None


class CTSellerPostedAuctionOut(BaseModel):
    ctseller_sale_id: int
    title: str
    posted_count: int


class CTSellerPostedAuctionsOut(BaseModel):
    looked_up: int = 0
    failed: int = 0
    unknown_count: int = 0
    sold_count: int = 0
    unsold_count: int = 0
    live_count: int = 0
    auctions: list[CTSellerPostedAuctionOut] = []


class CTSellerConditionOut(BaseModel):
    condition: str
    description: str = ""


class LotPostToCTSellerIn(BaseModel):
    """Optional override; otherwise uses the sale's saved ctseller_sale_id."""

    ctseller_sale_id: int | None = None
    save_sale_mapping: bool = True
    force: bool = False


class LotPostToCTSellerOut(BaseModel):
    lot_id: str
    ctseller_item_id: int | None = None
    ctseller_sale_id: int
    photos_uploaded: int = 0
    videos_uploaded: int = 0
    video_note: str | None = None
    ctseller_posted_at: datetime | None = None
    queued: bool = False


class SalePostToCTSellerIn(BaseModel):
    """Bulk-post lots in a catalog sale to one CT Seller auction."""

    lot_ids: list[str] | None = None
    only_unposted: bool = True
    ctseller_sale_id: int | None = None
    save_sale_mapping: bool = True
    force: bool = False
    # Queue size only — posting runs in a background thread (not the HTTP request).
    limit: int = Field(default=100, ge=1, le=200)


class SalePostToCTSellerLotResult(BaseModel):
    lot_id: str
    sku: str | None = None
    ok: bool
    skipped: bool = False
    error: str | None = None
    ctseller_item_id: int | None = None
    photos_uploaded: int | None = None
    videos_uploaded: int | None = None
    video_note: str | None = None


class SalePostToCTSellerOut(BaseModel):
    ctseller_sale_id: int
    posted: int = 0
    failed: int = 0
    skipped: int = 0
    queued: int = 0
    results: list[SalePostToCTSellerLotResult]


class SaleCtSellerPhotosIn(BaseModel):
    """Replace photos on lots already posted to CT Seller. lot_ids is required."""

    lot_ids: list[str] = Field(min_length=1, max_length=200)


class SalePostToCTSellerStatusOut(BaseModel):
    """Live progress for a background CT Seller post (photos upload after the HTTP response)."""

    active: bool = False
    ctseller_sale_id: int | None = None
    total: int = 0
    done: int = 0
    posted: int = 0
    failed: int = 0
    skipped: int = 0
    queued: int = 0
    current_lot_id: str | None = None
    current_sku: str | None = None
    current_phase: str | None = None
    results: list[SalePostToCTSellerLotResult] = Field(default_factory=list)


class SaleCoverPhotoSlotOut(BaseModel):
    slot: int
    label: str
    photo_id: str | None = None
    lot_id: str | None = None
    sku: str | None = None
    auction_title: str = ""
    photo_role: str | None = None
    thumb_url: str | None = None
    sale_id: str | None = None
    sale_name: str = ""
    from_other_sale: bool = False


class SaleCoverCandidateOut(BaseModel):
    photo_id: str
    lot_id: str
    sku: str | None = None
    auction_title: str = ""
    photo_role: str | None = None
    is_hero: bool = False
    suggested: bool = False
    thumb_url: str | None = None
    sale_id: str | None = None
    sale_name: str = ""
    from_other_sale: bool = False


class SaleCoverRemoteOut(BaseModel):
    id: int | None = None
    primary_url: str | None = None
    secondary_urls: list[str] = []


class SaleCoverPhotosOut(BaseModel):
    sale_id: str
    ctseller_sale_id: int | None = None
    catalog_sale_name: str = ""
    selected_photo_ids: list[str] = []
    suggested_photo_ids: list[str] = []
    slots: list[SaleCoverPhotoSlotOut]
    remote: SaleCoverRemoteOut | None = None
    remote_error: str | None = None


class SaleCoverCandidatesOut(BaseModel):
    sale_id: str
    ctseller_sale_id: int | None = None
    catalog_sale_name: str = ""
    suggested_photo_ids: list[str] = []
    candidates: list[SaleCoverCandidateOut]


class SaleCoverPhotosSelectIn(BaseModel):
    photo_ids: list[str] = Field(default_factory=list, max_length=4)
    ctseller_sale_id: int | None = None


class SaleCoverPhotosPushIn(BaseModel):
    photo_ids: list[str] | None = Field(default=None, max_length=4)
    ctseller_sale_id: int | None = None
    save_sale_mapping: bool = True


class SaleReservePriceIn(BaseModel):
    reserve_price: float | None = Field(default=None, ge=0, le=1_000_000)
    apply_to_lots: bool = True
    push_posted: bool = False
    lot_ids: list[str] | None = None
    only_without_reserve: bool = False
    update_sale_default: bool = True
    ctseller_sale_id: int | None = None


class SaleReservePriceLotResult(BaseModel):
    lot_id: str
    sku: str | None = None
    ok: bool
    skipped: bool = False
    error: str | None = None
    ctseller_item_id: int | None = None


class SaleReservePriceOut(BaseModel):
    sale_id: str
    reserve_price: float | None
    lots_updated: int
    posted_updated: int = 0
    posted_failed: int = 0
    posted_skipped: int = 0
    results: list[SaleReservePriceLotResult] = []


class SaleCoverPhotosPushOut(BaseModel):
    sale_id: str
    ctseller_sale_id: int
    updated: bool
    remote_id: int | None = None
    selected_photo_ids: list[str]


class CatalogR2MigrateIn(BaseModel):
    """Upload local catalog files to R2. Safe to re-run."""

    limit: int = Field(default=500, ge=1, le=5000)
    offset: int = Field(default=0, ge=0, le=5_000_000)
    account_id: str | None = None  # master admin only; owners always migrate their own account


class CatalogR2MigrateOut(BaseModel):
    uploaded: int
    skipped_already_in_r2: int
    skipped_missing_local: int
    failed: int
    errors: list[str] = []
    r2_configured: bool = True
    offset: int = 0
    next_offset: int | None = None
    total_photos: int = 0
    done: bool = False


class CatalogMediaUrlOut(BaseModel):
    url: str
    expires_in: int = 3600
    kind: str = "original"  # original | thumb


class SaleLotCoverUrlsOut(BaseModel):
    """Presigned R2 cover thumbs for every lot in a sale (mobile/dashboard grids)."""

    expires_in: int = 3600
    urls: dict[str, str]  # lot_id -> https URL


class CatalogR2CorsOut(BaseModel):
    origins: list[str]
    rules: list[dict] = []


class CreateJobRequest(BaseModel):
    input_folder: str
    sale_id: str
    inherit_sku_from_subfolder: bool = False
    single_lot_mode: bool = False
    single_lot_manual_sku: str | None = None
    listing_channel: str = "ctseller"


class JobOut(BaseModel):
    id: str
    account_id: str
    user_id: str
    sale_id: str
    status: str
    input_folder: str
    error_message: str | None
    created_at: datetime
    inherit_sku_from_subfolder: bool = False
    single_lot_mode: bool = False
    single_lot_manual_sku: str | None = None
    listing_channel: str = "ctseller"
    progress_phase: str | None = None
    progress_done: int = 0
    progress_total: int = 0
    progress_file_name: str | None = None


class LotVideoOut(BaseModel):
    id: str
    lot_id: str
    original_name: str
    created_at: datetime
    sort_order: int = 0
    content_type: str = "video/mp4"
    byte_size: int = 0
    file_on_disk: bool = True


class DeleteLotVideosIn(BaseModel):
    video_ids: list[str] = Field(min_length=1, max_length=40)


class LotPhotoOut(BaseModel):
    id: str
    lot_id: str
    original_name: str
    created_at: datetime
    sort_order: int = 0
    """True when this frame was uploaded at least 2 hours after the lot's first photo."""
    added_later: bool = False
    """False when ``Photo.storage_path`` is missing or not a readable file on the API host."""
    file_on_disk: bool = True
    """Pipeline triage role when known (e.g. ``tape`` for estate SKU-tape frames)."""
    photo_role: str | None = None
    distinct_item_id: str | None = None
    is_primary_front: bool = False
    shows_whole_lot: bool = False
    shows_measuring_scale: bool = False
    label_legibility: str | None = None
    """True when a plain-background pass saved the original and it can be restored."""
    plain_background: bool = False


class LotPhotoHealthRow(BaseModel):
    lot_id: str
    sku: str | None
    auction_title: str
    photo_count: int
    linked_photo_count: int
    missing_count: int


class SalePhotoHealthOut(BaseModel):
    """Lots in a sale with at least one linked catalog photo file missing on disk."""

    lots: list[LotPhotoHealthRow]


class PhotoAddLotOut(BaseModel):
    """A lot that received more photos after it was already cataloged."""

    lot_id: str
    sale_id: str
    sku: str | None = None
    auction_title: str = ""
    added_count: int
    latest_added_at: datetime
    added_by: list[str] = []
    on_ctseller: bool = False


class PhotoAddsOut(BaseModel):
    """Lots in one sale that gained photos at least 2 hours after their first photo."""

    window_days: int
    lots: list[PhotoAddLotOut]


class CatalogedMobileDraftLotOut(BaseModel):
    """One phone draft that already became a catalog lot (filename ``mobile-<draft>-NN.jpg``)."""

    draft_id: str
    lot_id: str
    sku: str | None = None
    photo_count: int = 0
    auction_title: str = ""


class CatalogedMobileDraftsOut(BaseModel):
    """Phone draft ids that already have catalog photos on this sale (``mobile-<draft>-NN.jpg``)."""

    draft_ids: list[str]
    lots: list[CatalogedMobileDraftLotOut] = []


class ReorderLotPhotosIn(BaseModel):
    """Every catalog photo id for this lot exactly once, in desired display order (left-to-right, then rows)."""

    photo_ids: list[str] = Field(min_length=1, max_length=500)


class DeleteLotPhotosIn(BaseModel):
    """Remove selected catalog photos from this lot (files removed after DB commit)."""

    photo_ids: list[str] = Field(min_length=1, max_length=200)


class RotateLotPhotosIn(BaseModel):
    """Rotate catalog files on disk (90 clockwise, -90 counter-clockwise, or 180)."""

    degrees: Literal[90, -90, 180]
    photo_ids: list[str] | None = Field(
        default=None,
        max_length=200,
        description="When omitted or empty, rotate every catalog photo on this lot.",
    )


class RotateLotPhotosOut(BaseModel):
    rotated: int
    failed: int = 0
    photos: list[LotPhotoOut]
    errors: list[str] = Field(default_factory=list)


class PlainBackgroundIn(BaseModel):
    """Cut the piece out and place it on a flat color. Janet JL furniture lots only."""

    color: Literal["studio", "white", "gray"] = "studio"
    photo_ids: list[str] | None = Field(
        default=None,
        max_length=PLAIN_BG_MAX_PHOTOS,
        description="When omitted, only the first listing photo (not SKU tape) is changed. SKU tape ids are skipped.",
    )


class PlainBackgroundOut(BaseModel):
    updated: int
    failed: int = 0
    photos: list[LotPhotoOut]
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RestorePlainBackgroundIn(BaseModel):
    photo_ids: list[str] | None = Field(default=None, max_length=40)


class TransferPhotosIn(BaseModel):
    """Move catalog photos into another lot in the same sale (same account)."""

    photo_ids: list[str] = Field(min_length=1, max_length=200)


class TransferPhotosOut(BaseModel):
    moved: int
    updated_lots: list[LotOut]


class SplitPhotosToNewLotIn(BaseModel):
    """Move selected catalog photos off this lot into a newly created lot in the same sale."""

    photo_ids: list[str] = Field(min_length=1, max_length=200)
    auction_title: str | None = Field(
        default=None,
        max_length=500,
        description="Optional title for the new listing; defaults to a short split placeholder.",
    )


class SplitPhotosToNewLotOut(BaseModel):
    new_lot: LotOut
    source_lot: LotOut


class LotAiRegenerateIn(BaseModel):
    """Re-run Gemini on disk files already linked to this lot and overwrite listing text (same sale)."""

    photo_ids: list[str] | None = Field(
        default=None,
        max_length=200,
        description="When set, only these photos (in request order) are sent to the model; when omitted, all lot photos in catalog order.",
    )
    preserve_sku: bool = Field(
        default=False,
        description="When true, keep the lot's current estate-tape SKU and only refresh title/description/specs/etc.",
    )


class LotAudienceHuntIn(BaseModel):
    """Find public collector groups for this lot (Google Search). Does not recatalog photos."""

    force: bool = Field(
        default=False,
        description="Hunt even when the lot looks like everyday household goods.",
    )


class SaleAudienceHuntIn(BaseModel):
    """Hunt public collector groups for existing lots in a sale. Does not recatalog or auto-post."""

    lot_ids: list[str] | None = Field(
        default=None,
        description="When set, hunt these lots (household still skipped unless force). When omitted, hunt unhunted specialist lots in the sale.",
    )
    force: bool = False
    only_unhunted: bool = True
    limit: int = Field(default=40, ge=1, le=80)


class SaleAudienceHuntLotResult(BaseModel):
    lot_id: str
    sku: str | None = None
    queued: bool = False
    skipped: bool = False
    error: str | None = None


class SaleAudienceHuntOut(BaseModel):
    queued: int
    skipped: int
    results: list[SaleAudienceHuntLotResult]


class SaleAudienceHuntLotProgress(BaseModel):
    lot_id: str
    sku: str | None = None
    status: str = "queued"
    phase: str | None = None


class SaleAudienceHuntStatusOut(BaseModel):
    """Live progress for a sale-wide hunt (sequential Gemini + Search)."""

    active: bool = False
    total: int = 0
    done: int = 0
    current_lot_id: str | None = None
    current_sku: str | None = None
    current_phase: str | None = None
    lots: list[SaleAudienceHuntLotProgress] = Field(default_factory=list)


class MergeLotsIn(BaseModel):
    """Merge ``source_lot_id`` into this lot: all photos move here, then the source lot row is deleted."""

    source_lot_id: str = Field(min_length=1, max_length=40)
    prefer_source_sku: bool = False


class SaleZipExportOut(BaseModel):
    id: str
    sale_id: str
    status: str
    total_photos_expected: int
    photo_files_written: int
    error_message: str | None
    expires_at: datetime | None
    created_at: datetime
    ready: bool = False
    kind: str = "all"
    filter_ctseller_sale_id: int | None = None
    across_sales: bool = False


class AppraisalResultOut(BaseModel):
    summary: str = ""
    recommended_starting_price: float = 0.0
    price_low: float = 0.0
    price_high: float = 0.0
    high_value: bool = False
    high_value_rationale: str = ""
    listing_channel: str = "needs_review"
    listing_channel_rationale: str = ""
    recommended_ct_category: str = ""
    confidence: str = "medium"
    rationale: str = ""
    search_summary: str = ""
    items: list[dict] = Field(default_factory=list)


class EngagementItemIn(BaseModel):
    """One live lot. Extra fields are rejected so bidder PII cannot be stored."""

    model_config = ConfigDict(extra="forbid")

    ctseller_item_id: int
    sku: str = ""
    title: str = ""
    views: int = Field(default=0, ge=0)
    watchers: int = Field(default=0, ge=0)
    bids: int = Field(default=0, ge=0)
    price: float = Field(default=0, ge=0)
    reserve: float = Field(default=0, ge=0)
    item_url: str = ""
    item_status: str = ""


class EngagementSnapshotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ctseller_sale_id: int
    sale_title: str = ""
    auction_url: str = ""
    sale_ends_at: datetime | None = None
    captured_at: datetime | None = None
    items: list[EngagementItemIn] = Field(min_length=1, max_length=2000)


class EngagementOut(BaseModel):
    views: int = 0
    watchers: int = 0
    bids: int = 0
    price: float = 0
    reserve: float = 0
    item_url: str = ""
    captured_at: datetime | None = None
    bucket: str = ""
    engagement_score: str = ""
    momentum: str = ""
    notes: str = ""
    late_surge: str = ""
    trajectory: str = ""
    rescue_candidate: bool = False
    push_rank: int | None = None
    ctseller_sale_id: int | None = None
    ctseller_item_id: int | None = None


class EngagementQueueItem(EngagementOut):
    sku: str = ""
    title: str = ""
    item_status: str = ""
    lot_id: str | None = None
    has_thumbnail: bool = False
    photo_ids: list[str] = Field(default_factory=list)


class EngagementSaleSummary(BaseModel):
    ctseller_sale_id: int
    sale_title: str = ""
    auction_url: str = ""
    sale_ends_at: datetime | None = None
    captured_at: datetime | None = None
    item_count: int = 0
    rescue: int = 0
    push: int = 0
    protect: int = 0
    let_ride: int = 0


class EngagementSaleOut(EngagementSaleSummary):
    items: list[EngagementQueueItem] = Field(default_factory=list)


class SocialCalendarPost(BaseModel):
    order: int
    post_date: str
    local_time: str
    zone: str = "PT"
    platform: str
    hook: str = ""
    post_type: str = ""
    sku: str = ""
    title: str = ""
    item_url: str = ""
    lot_id: str | None = None
    photo_ids: list[str] = Field(default_factory=list)
    paste_copy: str = ""
    status: str = "Planned"
    preflight: str = ""


class EngagementIngestOut(BaseModel):
    ctseller_sale_id: int
    item_count: int
    matched_lots: int
    rescue: int
    push: int
    protect: int
    let_ride: int


class FacebookStatusOut(BaseModel):
    configured: bool
    connected: bool
    needs_reconnect: bool = False
    page_id: str | None = None
    page_name: str | None = None
    user_id: str | None = None
    connected_at: datetime | None = None
    token_error: str | None = None
    graph_api_version: str = ""
    oauth_scopes: list[str] = Field(default_factory=list)


class FacebookOAuthStartOut(BaseModel):
    url: str


class FacebookPageChoiceOut(BaseModel):
    id: str
    name: str
    can_post: bool = True
    selected: bool = False


class FacebookSelectPageIn(BaseModel):
    page_id: str = Field(min_length=1, max_length=64)


class FacebookExistingPostOut(BaseModel):
    id: str
    status: str
    facebook_post_id: str | None = None
    scheduled_publish_time: datetime | None = None
    posted_at: datetime | None = None
    created_at: datetime | None = None


class FacebookPreviewLotOut(BaseModel):
    lot_id: str
    sku: str | None = None
    title: str = ""
    item_url: str = ""
    sale_ends_at: datetime | None = None
    caption: str = ""
    photo_ids: list[str] = Field(default_factory=list)
    photo_count: int = 0
    already_posted: bool = False
    existing_posts: list[FacebookExistingPostOut] = Field(default_factory=list)
    error: str | None = None


class FacebookScheduleSuggestionOut(BaseModel):
    at: datetime
    label: str


class FacebookPreviewIn(BaseModel):
    lot_ids: list[str] = Field(min_length=1, max_length=40)
    include_follow_hook: bool = False


class FacebookPreviewOut(FacebookStatusOut):
    include_follow_hook: bool = False
    follow_hook_line: str = ""
    schedule_suggestions: list[FacebookScheduleSuggestionOut] = Field(default_factory=list)
    lots: list[FacebookPreviewLotOut] = Field(default_factory=list)


class FacebookPublishItemIn(BaseModel):
    lot_id: str = Field(min_length=1)
    caption: str = Field(min_length=1, max_length=8000)
    scheduled_publish_time: datetime | None = None


class FacebookPublishIn(BaseModel):
    confirm: bool = False
    force: bool = False
    include_follow_hook: bool = False
    source: str = Field(default="lot", max_length=32)
    items: list[FacebookPublishItemIn] = Field(min_length=1, max_length=40)


class FacebookPublishResultOut(BaseModel):
    lot_id: str
    ok: bool
    skipped: bool = False
    post_id: str | None = None
    facebook_post_id: str | None = None
    status: str | None = None
    scheduled_publish_time: datetime | None = None
    needs_reconnect: bool = False
    error: str | None = None


class FacebookPublishOut(BaseModel):
    queued: bool = False
    batch_id: str | None = None
    active: bool = False
    total: int = 0
    done: int = 0
    posted: int = 0
    failed: int = 0
    results: list[FacebookPublishResultOut] = Field(default_factory=list)


class FacebookPagePostOut(BaseModel):
    id: str
    lot_id: str | None = None
    lot_ids: list[str] = Field(default_factory=list)
    photo_ids: list[str] = Field(default_factory=list)
    facebook_page_id: str = ""
    facebook_post_id: str | None = None
    caption: str = ""
    include_follow_hook: bool = False
    source: str = "lot"
    status: str
    scheduled_publish_time: datetime | None = None
    posted_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime


class AppraisalCheckOut(BaseModel):
    id: str
    sale_id: str
    status: str
    scene_note: str | None = None
    photo_count: int = 0
    result: AppraisalResultOut | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


LotOut.model_rebuild()

