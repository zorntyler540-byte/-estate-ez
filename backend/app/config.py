from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    secret_key: str = Field(default="change-me-dev-secret", alias="SECRET_KEY")
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
    gemini_input_cost_per_million: float = Field(
        default=0.35, ge=0.0, alias="GEMINI_INPUT_COST_PER_MILLION"
    )
    gemini_output_cost_per_million: float = Field(
        default=1.05, ge=0.0, alias="GEMINI_OUTPUT_COST_PER_MILLION"
    )
    skip_tape_verify: bool = Field(default=False, alias="SKIP_TAPE_VERIFY")
    # Default is conservative: many parallel Gemini calls decode large JPEGs in RAM and trigger OOM on small hosts (e.g. Railway). Override with GEMINI_PARALLELISM=12–15 on a beefy machine.
    gemini_parallelism: int = Field(default=5, ge=1, le=32, alias="GEMINI_PARALLELISM")
    # Cap simultaneous Gemini HTTP calls across upload jobs, regenerate, and triage threads.
    gemini_global_concurrency: int = Field(default=4, ge=1, le=32, alias="GEMINI_GLOBAL_CONCURRENCY")
    # Stacked Add-one-lot uploads queue automatically when this many jobs are already running.
    catalog_job_max_concurrent: int = Field(default=1, ge=1, le=4, alias="CATALOG_JOB_MAX_CONCURRENT")
    # When false, this process only enqueues catalog jobs; a separate ``python -m app.worker`` claims them.
    # Set false on the Railway API service and true (default) on the worker service.
    run_catalog_jobs_in_process: bool = Field(default=True, alias="RUN_CATALOG_JOBS_IN_PROCESS")
    gemini_image_max_edge: int = Field(default=3072, ge=512, le=4096, alias="GEMINI_IMAGE_MAX_EDGE")
    gemini_jpeg_quality: int = Field(default=95, ge=40, le=95, alias="GEMINI_JPEG_QUALITY")
    # Estate pipeline: per-photo triage + capped Pro brand audit for single-lot and batch uploads.
    gemini_flash_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_FLASH_MODEL")
    gemini_pro_model: str = Field(default="gemini-2.5-pro", alias="GEMINI_PRO_MODEL")
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

    # When ``ENVIRONMENT=production`` and this is false, ``POST /auth/register`` returns 403 (set true only to bootstrap users).
    allow_open_registration: bool = Field(default=True, alias="ALLOW_OPEN_REGISTRATION")
    # Comma-separated global admin emails that can manage all accounts across the server.
    master_admin_emails: str = Field(default="", alias="MASTER_ADMIN_EMAILS")

    # JWT lifetime (minutes). Shorter values reduce exposure if a token leaks.
    access_token_expire_minutes: int = Field(default=1440, ge=15, le=10080, alias="ACCESS_TOKEN_EXPIRE_MINUTES")

    # Recycle pooled DB connections periodically (seconds); helps with stale TLS routers on managed Postgres.
    db_pool_recycle: int = Field(default=280, ge=60, le=3600, alias="DB_POOL_RECYCLE")
    # Postgres QueuePool sizing (ignored for SQLite). Defaults are higher than SQLAlchemy 5+10 to survive
    # concurrent HTTP + background job threads; tune per replica × DB max_connections budget.
    db_pool_size: int = Field(default=10, ge=1, le=50, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, ge=0, le=50, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(default=30, ge=5, le=120, alias="DB_POOL_TIMEOUT")

    # Suggestions / feedback email (requires SMTP_* for outbound delivery)
    feedback_to_email: str = Field(default="zorntyler540@gmail.com", alias="FEEDBACK_TO_EMAIL")
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

    @property
    def is_production(self) -> bool:
        return (self.environment or "").strip().lower() in ("production", "prod")

    def trusted_host_list(self) -> list[str]:
        hosts = [h.strip() for h in (self.trusted_hosts or "").split(",") if h.strip()]
        return hosts

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

    @model_validator(mode="after")
    def production_must_be_safe(self) -> Self:
        if not self.is_production:
            return self
        sk = (self.secret_key or "").strip()
        weak = {"", "change-me", "change-me-dev-secret"}
        if sk.lower() in weak or len(sk) < 32:
            raise ValueError(
                "In production, SECRET_KEY must be a long random string (at least 32 characters), "
                "e.g. generate with: openssl rand -hex 32"
            )
        if (self.database_url or "").strip().lower().startswith("sqlite"):
            raise ValueError("In production, DATABASE_URL must not use SQLite; use Postgres.")
        if not (self.cors_origins or "").strip():
            raise ValueError(
                "In production, set CORS_ORIGINS to your deployed web origin(s), comma-separated "
                "(e.g. https://your-app.up.railway.app)."
            )
        return self


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

