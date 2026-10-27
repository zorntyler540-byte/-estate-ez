from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_WEAK_SECRET_KEYS = frozenset({"", "change-me", "change-me-dev-secret"})

_BACKEND_DIR = Path(__file__).resolve().parent.parent
# Load cwd `.env` first, then `backend/.env` so repo-root env does not override API secrets
# (pydantic-settings: later files win on duplicate keys). Always end with backend/.env when present.
_cwd_env = Path.cwd() / ".env"
_backend_env = _BACKEND_DIR / ".env"
_env_paths: list[Path] = []
if _cwd_env.is_file() and _cwd_env.resolve() != _backend_env.resolve():
    _env_paths.append(_cwd_env)
if _backend_env.is_file():
    _env_paths.append(_backend_env)
_ENV_FILES = tuple(str(p) for p in _env_paths) or (".env",)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES or (".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CaringTransitions API"
    secret_key: str = Field(default="", alias="SECRET_KEY")
    database_url: str = Field(
        default="sqlite:///./app.db",
        alias="DATABASE_URL",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, v: object) -> object:
        """Railway/Heroku often use ``postgres://``; SQLAlchemy 2 + psycopg3 wants ``postgresql+psycopg://``."""
        if not isinstance(v, str):
            return v
        u = v.strip()
        if u.startswith("sqlite"):
            return u
        scheme = u.split(":", 1)[0] if ":" in u else ""
        if "+psycopg" in scheme or "+psycopg2" in scheme:
            return u
        if u.startswith("postgres://"):
            return "postgresql+psycopg://" + u[len("postgres://") :]
        if u.startswith("postgresql://"):
            return "postgresql+psycopg://" + u[len("postgresql://") :]
        return u

    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    gemini_model: str = Field(default="gemini-flash-latest", alias="GEMINI_MODEL")
    gemini_api_version: str = Field(default="v1beta", alias="GEMINI_API_VERSION")
    # Estimated Gemini pricing used for admin analytics (USD per 1M tokens).
    # Legacy single/blended rates (kept for backward compatibility / fallback).
    gemini_input_cost_per_million: float = Field(
        default=0.35, ge=0.0, alias="GEMINI_INPUT_COST_PER_MILLION"
    )
    gemini_output_cost_per_million: float = Field(
        default=1.05, ge=0.0, alias="GEMINI_OUTPUT_COST_PER_MILLION"
    )
    # Per-model rates (USD per 1M tokens). Defaults track published Gemini 2.5 paid pricing
    # (Flash $0.30 in / $2.50 out; Pro $1.25 in / $10.00 out, prompts <=200k). Override via env
    # when Google changes pricing or you move models. Flash covers triage + listing; Pro covers
    # the brand/listing author pass.
    gemini_flash_input_cost_per_million: float = Field(
        default=0.30, ge=0.0, alias="GEMINI_FLASH_INPUT_COST_PER_MILLION"
    )
    gemini_flash_output_cost_per_million: float = Field(
        default=2.50, ge=0.0, alias="GEMINI_FLASH_OUTPUT_COST_PER_MILLION"
    )
    gemini_pro_input_cost_per_million: float = Field(
        default=1.25, ge=0.0, alias="GEMINI_PRO_INPUT_COST_PER_MILLION"
    )
    gemini_pro_output_cost_per_million: float = Field(
        default=10.00, ge=0.0, alias="GEMINI_PRO_OUTPUT_COST_PER_MILLION"
    )
    skip_tape_verify: bool = Field(default=False, alias="SKIP_TAPE_VERIFY")
    # Default is conservative: many parallel Gemini calls decode large JPEGs in RAM and trigger OOM on small hosts (e.g. Railway). Override with GEMINI_PARALLELISM=12–15 on a beefy machine.
    gemini_parallelism: int = Field(default=5, ge=1, le=32, alias="GEMINI_PARALLELISM")
    # Cap simultaneous Gemini HTTP calls across upload jobs, regenerate, and triage threads.
    gemini_global_concurrency: int = Field(default=3, ge=1, le=32, alias="GEMINI_GLOBAL_CONCURRENCY")
    # Stacked Add-one-lot uploads queue automatically when this many jobs are already running.
    catalog_job_max_concurrent: int = Field(default=1, ge=1, le=4, alias="CATALOG_JOB_MAX_CONCURRENT")
    # Per-account catalog spend brakes (Gemini runs on the shared operator key).
    catalog_max_queued_jobs_per_account: int = Field(
        default=25, ge=1, le=500, alias="CATALOG_MAX_QUEUED_JOBS_PER_ACCOUNT"
    )
    catalog_max_photos_per_account_per_day: int = Field(
        default=800, ge=40, le=50000, alias="CATALOG_MAX_PHOTOS_PER_ACCOUNT_PER_DAY"
    )
    # Multi-lot folder jobs (single-lot jobs still use the 40-photo CT Bids cap).
    catalog_max_photos_per_job: int = Field(
        default=250, ge=40, le=2000, alias="CATALOG_MAX_PHOTOS_PER_JOB"
    )
    # When false, this process only enqueues catalog jobs; a separate ``python -m app.worker`` claims them.
    # Set false on the Railway API service and true (default) on the worker service.
    run_catalog_jobs_in_process: bool = Field(default=True, alias="RUN_CATALOG_JOBS_IN_PROCESS")
    gemini_image_max_edge: int = Field(default=2048, ge=512, le=4096, alias="GEMINI_IMAGE_MAX_EDGE")
    gemini_jpeg_quality: int = Field(default=85, ge=40, le=95, alias="GEMINI_JPEG_QUALITY")
    # In-memory pipeline copies (triage/gallery/Pro). Keep well below catalog 4096px originals.
    pipeline_working_max_edge: int = Field(default=1600, ge=256, le=4096, alias="PIPELINE_WORKING_MAX_EDGE")
    pipeline_working_jpeg_quality: int = Field(
        default=80, ge=40, le=95, alias="PIPELINE_WORKING_JPEG_QUALITY"
    )
    # How many full photos are decoded for triage at once (each decode is tens of MB).
    pipeline_triage_parallel: int = Field(default=2, ge=1, le=8, alias="PIPELINE_TRIAGE_PARALLEL")
    # Dedicated worker: run each catalog job in a child process so RSS drops when it exits.
    worker_isolate_jobs: bool = Field(default=True, alias="WORKER_ISOLATE_JOBS")
    # Parallel CT Seller photo upload (download/resize/POST). Keep modest — unofficial API + 2 GB API RAM.
    ctseller_post_photo_workers: int = Field(default=4, ge=1, le=8, alias="CTSELLER_POST_PHOTO_WORKERS")
    # Estate pipeline: per-photo triage + capped Pro brand audit for single-lot and batch uploads.
    gemini_flash_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_FLASH_MODEL")
    gemini_pro_model: str = Field(default="gemini-2.5-pro", alias="GEMINI_PRO_MODEL")
    # One listing cover at a time. Image model (API key), not the local cutout.
    hero_bg_gemini_model: str = Field(
        default="gemini-3.1-flash-image", alias="HERO_BG_GEMINI_MODEL"
    )
    listing_flash_max_frames: int = Field(default=5, ge=1, le=12, alias="LISTING_FLASH_MAX_FRAMES")
    brand_pro_max_label_frames: int = Field(default=4, ge=1, le=8, alias="BRAND_PRO_MAX_LABEL_FRAMES")
    brand_skip_pro_if_agreement: bool = Field(default=True, alias="BRAND_SKIP_PRO_IF_AGREEMENT")
    enable_grounded_valuation: bool = Field(default=False, alias="ENABLE_GROUNDED_VALUATION")
    enable_appraiser: bool = Field(default=False, alias="ENABLE_APPRAISER")
    appraiser_max_photos: int = Field(default=3, ge=1, le=3, alias="APPRAISER_MAX_PHOTOS")
    triage_image_max_edge: int = Field(default=2048, ge=256, le=4096, alias="TRIAGE_IMAGE_MAX_EDGE")
    # Stored catalog bytes (browser upload + EXIF/HEIC normalize). ZIP + /photos/.../download + extensions use these.
    catalog_upload_max_edge: int = Field(default=4096, ge=512, le=8192, alias="CATALOG_UPLOAD_MAX_EDGE")
    catalog_upload_jpeg_quality: int = Field(default=98, ge=70, le=98, alias="CATALOG_UPLOAD_JPEG_QUALITY")
    tape_focus_enabled: bool = Field(default=True, alias="TAPE_FOCUS_ENABLED")
    last_known_sku_fallback: bool = Field(default=True, alias="LAST_KNOWN_SKU_FALLBACK")
    # Outer wall time for one estate-tape SKU audit thread (should exceed HTTP connect+read below).
    tape_verify_total_timeout_seconds: float = Field(
        default=120.0, ge=30.0, le=600.0, alias="TAPE_VERIFY_TOTAL_TIMEOUT_SECONDS"
    )
    # Tape verify uses REST ``generateContent`` only (bounded); connect/read timeouts prevent hung sockets.
    tape_verify_http_connect_seconds: float = Field(
        default=20.0, ge=5.0, le=120.0, alias="TAPE_VERIFY_HTTP_CONNECT_SECONDS"
    )
    tape_verify_http_read_seconds: float = Field(
        default=75.0, ge=15.0, le=300.0, alias="TAPE_VERIFY_HTTP_READ_SECONDS"
    )
    # Appended to both tape audit prompts (after the fixed instructions). Newlines allowed.
    tape_verify_prompt_suffix: str = Field(default="", alias="TAPE_VERIFY_PROMPT_SUFFIX")
    # When true, log each inventory-loop step (file, sku, tape-verify timing) at INFO for stuck-job diagnosis.
    job_debug_inventory: bool = Field(default=False, alias="JOB_DEBUG_INVENTORY")

    # Comma-separated browser origins allowed to call the API (production Next URL). Dev localhost always included.
    cors_origins: str = Field(default="", alias="CORS_ORIGINS")

    # ``development`` (default) or ``production`` — enables stricter checks and response redaction.
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # Comma-separated hostnames for TrustedHostMiddleware (e.g. ``api.example.com``). Empty = disabled (typical dev).
    trusted_hosts: str = Field(default="", alias="TRUSTED_HOSTS")

    # Unset follows ENVIRONMENT: open in development/test, closed in production.
    # Set true only to bootstrap the first real account, then turn it off.
    allow_open_registration: bool | None = Field(default=None, alias="ALLOW_OPEN_REGISTRATION")
    # Comma-separated global admin emails. Register/invite of these addresses is blocked;
    # add an email here only after that user already exists.
    master_admin_emails: str = Field(default="", alias="MASTER_ADMIN_EMAILS")
    # Comma-separated user ids that are master admin even if their email is not listed.
    master_admin_user_ids: str = Field(default="", alias="MASTER_ADMIN_USER_IDS")

    # JWT lifetime (minutes). Mobile field sessions stay signed in longer; raise via env if needed (max 90 days).
    access_token_expire_minutes: int = Field(default=43200, ge=15, le=129600, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    sse_ticket_expire_minutes: int = Field(default=15, ge=5, le=120, alias="SSE_TICKET_EXPIRE_MINUTES")
    click_raw_per_code_per_day: int = Field(default=100, ge=1, le=5000, alias="CLICK_RAW_PER_CODE_PER_DAY")
    click_raw_retention_days: int = Field(default=30, ge=1, le=365, alias="CLICK_RAW_RETENTION_DAYS")

    # Recycle pooled DB connections periodically (seconds); helps with stale TLS routers on managed Postgres.
    db_pool_recycle: int = Field(default=280, ge=60, le=3600, alias="DB_POOL_RECYCLE")
    # Postgres QueuePool sizing (ignored for SQLite). Defaults are higher than SQLAlchemy 5+10 to survive
    # concurrent HTTP + background job threads; tune per replica × DB max_connections budget.
    db_pool_size: int = Field(default=10, ge=1, le=50, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, ge=0, le=50, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(default=30, ge=5, le=120, alias="DB_POOL_TIMEOUT")

    # Suggestions / feedback email (requires SMTP_* for outbound delivery)
    feedback_to_email: str = Field(default="", alias="FEEDBACK_TO_EMAIL")
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from_email: str = Field(default="", alias="SMTP_FROM_EMAIL")

    # Cloudflare R2 (S3-compatible). When all of access key, secret, bucket, and endpoint are set,
    # catalog photos are stored in R2 (with a local cache under data/uploads for fast reads).
    r2_account_id: str = Field(default="", alias="R2_ACCOUNT_ID")
    r2_access_key_id: str = Field(default="", alias="R2_ACCESS_KEY_ID")
    r2_secret_access_key: str = Field(default="", alias="R2_SECRET_ACCESS_KEY")
    r2_bucket: str = Field(default="", alias="R2_BUCKET")
    r2_endpoint: str = Field(default="", alias="R2_ENDPOINT")

    # Facebook Page posting (Meta Graph API). Feature is hidden when META_APP_ID is unset.
    # Tokens are encrypted at rest; optional META_TOKEN_ENCRYPTION_KEY, else derived from SECRET_KEY.
    meta_app_id: str = Field(default="", alias="META_APP_ID")
    meta_app_secret: str = Field(default="", alias="META_APP_SECRET")
    meta_oauth_redirect_uri: str = Field(default="", alias="META_OAUTH_REDIRECT_URI")
    meta_token_encryption_key: str = Field(default="", alias="META_TOKEN_ENCRYPTION_KEY")
    # Where the OAuth callback sends the browser after connecting a Page (Account settings).
    public_web_origin: str = Field(default="", alias="PUBLIC_WEB_ORIGIN")
    # Optional dedicated origin for public ``/go/<code>`` Facebook click-tracking.
    # Leave empty so Facebook captions keep normal CT Bids lot/sale URLs.
    # Do not set this to the Railway API or localhost — those are never printed in posts.
    public_api_origin: str = Field(default="", alias="PUBLIC_API_ORIGIN")

    @field_validator("allow_open_registration", mode="before")
    @classmethod
    def empty_registration_flag_is_unset(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @property
    def is_production(self) -> bool:
        return (self.environment or "").strip().lower() in ("production", "prod")

    @property
    def is_test(self) -> bool:
        return (self.environment or "").strip().lower() in ("test", "testing")

    def registration_is_open(self) -> bool:
        """Public ``POST /auth/register``. Closed by default in production."""
        if self.allow_open_registration is None:
            return not self.is_production
        return bool(self.allow_open_registration)

    def trusted_host_list(self) -> list[str]:
        hosts = [h.strip() for h in (self.trusted_hosts or "").split(",") if h.strip()]
        return hosts

    @property
    def meta_configured(self) -> bool:
        """True when Facebook Login can run (app id + secret). Redirect URI may still be defaulted."""
        return bool((self.meta_app_id or "").strip() and (self.meta_app_secret or "").strip())

    def master_admin_email_list(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in (self.master_admin_emails or "").split(","):
            v = raw.strip().lower()
            if not v or v in seen:
                continue
            seen.add(v)
            out.append(v)
        return out

    def master_admin_user_id_list(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in (self.master_admin_user_ids or "").split(","):
            v = raw.strip()
            if not v or v in seen:
                continue
            seen.add(v)
            out.append(v)
        return out

    @model_validator(mode="after")
    def runtime_must_be_safe(self) -> Self:
        if not self.is_test:
            sk = (self.secret_key or "").strip()
            if secret_key_is_weak(sk):
                raise ValueError(
                    "SECRET_KEY must be a long random string (at least 32 characters) and must not "
                    "be empty or a documented placeholder (change-me / change-me-dev-secret). "
                    "Generate one with: openssl rand -hex 32"
                )
        if not self.is_production:
            return self
        if (self.database_url or "").strip().lower().startswith("sqlite"):
            raise ValueError("In production, DATABASE_URL must not use SQLite; use Postgres.")
        if not (self.cors_origins or "").strip():
            raise ValueError(
                "In production, set CORS_ORIGINS to your deployed web origin(s), comma-separated "
                "(e.g. https://your-app.up.railway.app)."
            )
        return self


def secret_key_is_weak(secret_key: str) -> bool:
    sk = (secret_key or "").strip()
    return sk.lower() in _WEAK_SECRET_KEYS or len(sk) < 32


settings = Settings()


def cors_allow_origins() -> list[str]:
    """Origins for FastAPI CORS: local dev + optional ``CORS_ORIGINS`` (comma-separated)."""
    defaults = ["http://127.0.0.1:3000", "http://localhost:3000"]
    extra = [x.strip() for x in (settings.cors_origins or "").split(",") if x.strip()]
    extra = [x for x in extra if "*" not in x]
    out: list[str] = []
    seen: set[str] = set()
    for o in defaults + extra:
        if o not in seen:
            seen.add(o)
            out.append(o)
    return out


def public_job_input_folder(path: str) -> str:
    """Hide absolute server paths from API clients in production (dashboard still shows batch id)."""
    if not path:
        return ""
    if not settings.is_production:
        return path
    leaf = Path(path).name
    return leaf or path

