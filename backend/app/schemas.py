from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


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


class TeamMemberCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    role: str = Field(default="catalogger", pattern="^(owner|catalogger)$")


class TeamMemberOut(BaseModel):
    id: str
    email: EmailStr
    role: str
    created_at: datetime


class TeamActivityMemberOut(BaseModel):
    user_id: str
    email: EmailStr
    role: str
    photos_uploaded: int
    lots_created: int
    jobs_started: int


class TeamActivityTotalsOut(BaseModel):
    photos_uploaded: int
    lots_created: int
    jobs_started: int
    member_count: int


class TeamActivityOut(BaseModel):
    since: datetime
    until: datetime
    members: list[TeamActivityMemberOut]
    totals: TeamActivityTotalsOut


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
    listing_channel: str = "ctseller"
    audience_hold: bool = False
    audience_hunting: bool = False
    audience_hunt_error: str | None = None
    audience_hunt_phase: str | None = None
    audience: dict | None = None


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


class CTSellerCredentialsIn(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class CTSellerStatusOut(BaseModel):
    connected: bool
    username: str | None = None


class CTSellerRemoteSaleOut(BaseModel):
    id: int
    title: str
    status: str | None = None
    starttime: str | None = None
    endtime: str | None = None


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
    ctseller_item_id: int
    ctseller_sale_id: int
    photos_uploaded: int
    ctseller_posted_at: datetime


class SalePostToCTSellerIn(BaseModel):
    """Bulk-post lots in a catalog sale to one CT Seller auction."""

    lot_ids: list[str] | None = None
    only_unposted: bool = True
    ctseller_sale_id: int | None = None
    save_sale_mapping: bool = True
    force: bool = False
    limit: int = Field(default=50, ge=1, le=100)


class SalePostToCTSellerLotResult(BaseModel):
    lot_id: str
    sku: str | None = None
    ok: bool
    skipped: bool = False
    error: str | None = None
    ctseller_item_id: int | None = None
    photos_uploaded: int | None = None


class SalePostToCTSellerOut(BaseModel):
    ctseller_sale_id: int
    posted: int
    failed: int
    skipped: int
    results: list[SalePostToCTSellerLotResult]


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
    """False when ``Photo.storage_path`` is missing or not a readable file on the API host."""
    file_on_disk: bool = True
    """Pipeline triage role when known (e.g. ``tape`` for estate SKU-tape frames)."""
    photo_role: str | None = None
    distinct_item_id: str | None = None
    is_primary_front: bool = False
    shows_whole_lot: bool = False
    shows_measuring_scale: bool = False
    label_legibility: str | None = None


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

