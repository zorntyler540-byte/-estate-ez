from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class HealthResponse(BaseModel):
    status: str
    pipeline_triage_brand_audit: bool = False
    gemini_pro_model: str = ""
    gemini_flash_model: str = ""
    catalog_upload_root: str = ""
    catalog_image_files_on_disk: int = 0


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
    photos_uploaded: int
    lots_created: int
    avg_photos_per_job: float
    avg_job_duration_seconds: float
    est_input_tokens: int
    est_output_tokens: int
    est_total_cost_usd: float = 0.0


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
    created_at: datetime


class SaleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class SaleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_closed: bool | None = None


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
    ct_category: str | None = None
    ct_subcategory: str | None = None
    is_completed: bool = False
    completed_at: datetime | None = None
    starting_price: float | None
    condition_text: str
    photo_count: int
    has_thumbnail: bool = False
    created_at: datetime
    sku_needs_review: bool = False
    many_photos_review: bool = False
    sku_duplicate_in_sale: bool = False
    sku_text_mismatch: bool = False


class LotUpdate(BaseModel):
    auction_title: str | None = None
    professional_description: str | None = None
    specification_lines: list[str] | None = None
    brands: list[str] | None = None
    labels_and_tags: list[str] | None = None
    starting_price: float | None = None
    condition_text: str | None = None
    is_completed: bool | None = None
    sku: str | None = None


class CreateJobRequest(BaseModel):
    input_folder: str
    sale_id: str
    inherit_sku_from_subfolder: bool = False
    single_lot_mode: bool = False
    single_lot_manual_sku: str | None = None


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


class LotPhotoOut(BaseModel):
    id: str
    lot_id: str
    original_name: str
    created_at: datetime
    sort_order: int = 0
    """False when ``Photo.storage_path`` is missing or not a readable file on the API host."""
    file_on_disk: bool = True


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

