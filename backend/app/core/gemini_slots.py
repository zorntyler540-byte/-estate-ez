"""Cross-process Gemini concurrency + per-model RPM/TPM/RPD limits.

Concurrency uses Postgres advisory locks (two-argument form, classid ``GEMINI_SLOT_CLASSID``)
so isolated worker children and extra Railway replicas share one ceiling. SQLite / tests fall
back to a process-local semaphore.

Rate windows live in ``gemini_rate_windows`` (created on first use; no Alembic migration).
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.gemini_retry import (
    GeminiRetryableError,
    backoff_seconds,
    inspect_transient,
)

_LOG = logging.getLogger(__name__)

# Distinct from process_job's single-arg ``pg_try_advisory_lock(key)``.
GEMINI_SLOT_CLASSID = 0x47454D49  # 'GEMI'

_lock = threading.Lock()
_sem: threading.Semaphore | None = None
_rate_lock = threading.Lock()
_rate_windows: dict[str, tuple[int, int]] = {}
_lock_engine: Engine | None = None
_lock_engine_failed = False
_table_ready = False

_ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS gemini_rate_windows (
    bucket_key VARCHAR(80) PRIMARY KEY,
    window_start DOUBLE PRECISION NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0
)
"""


def model_family(model: str | None) -> str:
    """Map a Gemini model id onto flash / pro / 3.8-flash rate-limit buckets. Does not change model choice."""
    name = (model or "").strip().lower()
    if "pro" in name:
        return "pro"
    if "3.8" in name or "3-8" in name or "flash-latest" in name:
        return "flash38"
    return "flash"


def family_limits(family: str) -> tuple[int, int, int]:
    """Return ``(rpm, tpm, rpd)`` for a model family."""
    if family == "pro":
        return (
            int(settings.gemini_pro_rpm),
            int(settings.gemini_pro_tpm),
            int(settings.gemini_pro_rpd),
        )
    if family == "flash38":
        return (
            int(settings.gemini_38_flash_rpm),
            int(settings.gemini_38_flash_tpm),
            int(settings.gemini_38_flash_rpd),
        )
    return (
        int(settings.gemini_flash_rpm),
        int(settings.gemini_flash_tpm),
        int(settings.gemini_flash_rpd),
    )


def _postgres_url() -> str | None:
    url = (settings.database_url or "").strip()
    if "postgresql" in url.lower():
        return url
    return None


def _get_lock_engine() -> Engine | None:
    global _lock_engine, _lock_engine_failed
    if _lock_engine is not None:
        return _lock_engine
    if _lock_engine_failed:
        return None
    url = _postgres_url()
    if not url:
        return None
    try:
        _lock_engine = create_engine(
            url,
            future=True,
            poolclass=NullPool,
            pool_pre_ping=True,
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("gemini lock engine failed; falling back to in-process limits")
        _lock_engine_failed = True
        return None
    return _lock_engine


def _ensure_rate_table(engine: Engine) -> None:
    global _table_ready
    if _table_ready:
        return
    with engine.begin() as conn:
        conn.execute(text(_ENSURE_TABLE_SQL))
    _table_ready = True


def _semaphore() -> threading.Semaphore:
    global _sem
    if _sem is None:
        with _lock:
            if _sem is None:
                cap = max(1, settings.gemini_global_concurrency)
                _sem = threading.Semaphore(cap)
    return _sem


def reset_gemini_slots_for_tests() -> None:
    """Drop cached semaphore / in-memory windows (unit tests only)."""
    global _sem, _table_ready, _lock_engine, _lock_engine_failed
    with _lock:
        _sem = None
    with _rate_lock:
        _rate_windows.clear()
    _table_ready = False
    _lock_engine = None
    _lock_engine_failed = False


def _memory_wait_and_consume(key: str, limit: int, window_seconds: int) -> None:
    while True:
        now = int(time.time())
        window = now // window_seconds
        with _rate_lock:
            stored_window, count = _rate_windows.get(key, (window, 0))
            if stored_window != window:
                stored_window, count = window, 0
            if count < limit:
                _rate_windows[key] = (window, count + 1)
                return
        sleep_for = (window + 1) * window_seconds - time.time()
        time.sleep(max(0.05, min(2.0, sleep_for)))


def _pg_wait_and_consume(engine: Engine, key: str, limit: int, window_seconds: int) -> None:
    _ensure_rate_table(engine)
    sql = text(
        """
        INSERT INTO gemini_rate_windows (bucket_key, window_start, call_count)
        VALUES (:key, :window, 1)
        ON CONFLICT (bucket_key) DO UPDATE
        SET call_count = CASE
            WHEN gemini_rate_windows.window_start = EXCLUDED.window_start
            THEN gemini_rate_windows.call_count + 1
            ELSE 1
        END,
            window_start = EXCLUDED.window_start
        RETURNING call_count
        """
    )
    while True:
        window = int(time.time()) // window_seconds
        with engine.begin() as conn:
            count = int(conn.execute(sql, {"key": key, "window": window}).scalar() or 0)
        if count <= limit:
            return
        sleep_for = (window + 1) * window_seconds - time.time()
        time.sleep(max(0.05, min(2.0, sleep_for)))


def _memory_window_count(key: str, window_seconds: int) -> int:
    now = int(time.time())
    window = now // window_seconds
    with _rate_lock:
        stored_window, count = _rate_windows.get(key, (window, 0))
        if stored_window != window:
            return 0
        return int(count)


def _pg_window_count(engine: Engine, key: str, window_seconds: int) -> int:
    _ensure_rate_table(engine)
    window = int(time.time()) // window_seconds
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT window_start, call_count FROM gemini_rate_windows WHERE bucket_key = :key"
            ),
            {"key": key},
        ).first()
    if row is None:
        return 0
    stored_window, count = int(row[0]), int(row[1])
    if stored_window != window:
        return 0
    return count


def wait_for_model_quota(model: str | None, *, extra_tokens: int = 1) -> None:
    """Block until this model family is under RPM / RPD (and TPM if the window is already full)."""
    family = model_family(model)
    rpm, tpm, rpd = family_limits(family)
    engine = _get_lock_engine()
    consume = (
        (lambda key, limit, window: _pg_wait_and_consume(engine, key, limit, window))
        if engine is not None
        else _memory_wait_and_consume
    )
    consume(f"{family}:rpm", rpm, 60)
    consume(f"{family}:rpd", rpd, 86400)
    token_cost = max(1, int(extra_tokens))
    if token_cost > 1:
        consume(f"{family}:tpm", tpm, 60)
        return
    tpm_key = f"{family}:tpm"
    while True:
        used = (
            _pg_window_count(engine, tpm_key, 60)
            if engine is not None
            else _memory_window_count(tpm_key, 60)
        )
        if used < tpm:
            return
        sleep_for = (int(time.time()) // 60 + 1) * 60 - time.time()
        time.sleep(max(0.05, min(2.0, sleep_for)))


def record_model_tokens(model: str | None, tokens: int) -> None:
    """Add actual prompt+candidate tokens to the TPM window after a successful call."""
    if tokens <= 0:
        return
    family = model_family(model)
    _rpm, tpm, _rpd = family_limits(family)
    engine = _get_lock_engine()
    consume = (
        (lambda key, limit, window: _pg_wait_and_consume(engine, key, min(limit, tokens + limit), window))
        if engine is not None
        else _memory_wait_and_consume
    )
    # Recording after the call should not block the caller for a full TPM window.
    try:
        if engine is not None:
            _ensure_rate_table(engine)
            window = int(time.time()) // 60
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO gemini_rate_windows (bucket_key, window_start, call_count)
                        VALUES (:key, :window, :tokens)
                        ON CONFLICT (bucket_key) DO UPDATE
                        SET call_count = CASE
                            WHEN gemini_rate_windows.window_start = EXCLUDED.window_start
                            THEN gemini_rate_windows.call_count + EXCLUDED.call_count
                            ELSE EXCLUDED.call_count
                        END,
                            window_start = EXCLUDED.window_start
                        """
                    ),
                    {"key": f"{family}:tpm", "window": window, "tokens": int(tokens)},
                )
        else:
            now = int(time.time())
            window = now // 60
            with _rate_lock:
                stored_window, count = _rate_windows.get(f"{family}:tpm", (window, 0))
                if stored_window != window:
                    count = 0
                _rate_windows[f"{family}:tpm"] = (window, count + int(tokens))
    except Exception:  # noqa: BLE001
        _LOG.debug("gemini TPM record failed", exc_info=True)


def _acquire_pg_slot(conn: Any, cap: int) -> int | None:
    for slot in range(cap):
        got = conn.execute(
            text("SELECT pg_try_advisory_lock(:c, :s)"),
            {"c": GEMINI_SLOT_CLASSID, "s": slot},
        ).scalar()
        if got:
            return slot
    return None


def _release_pg_slot(conn: Any, slot: int) -> None:
    conn.execute(
        text("SELECT pg_advisory_unlock(:c, :s)"),
        {"c": GEMINI_SLOT_CLASSID, "s": slot},
    )


@contextmanager
def gemini_slot(model: str | None = None):
    """Hold one global Gemini concurrency slot, then consume RPM/RPD for ``model``."""
    engine = _get_lock_engine()
    cap = max(1, settings.gemini_global_concurrency)
    if engine is None:
        sem = _semaphore()
        sem.acquire()
        try:
            wait_for_model_quota(model)
            yield
        finally:
            sem.release()
        return

    deadline = time.monotonic() + 300.0
    conn = engine.connect()
    slot: int | None = None
    try:
        while True:
            slot = _acquire_pg_slot(conn, cap)
            if slot is not None:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for a Gemini concurrency slot")
            time.sleep(0.05)
        wait_for_model_quota(model)
        yield
    finally:
        try:
            if slot is not None:
                _release_pg_slot(conn, slot)
        except Exception:  # noqa: BLE001
            _LOG.debug("gemini advisory unlock failed", exc_info=True)
        conn.close()


def _call_tokens(response: Any) -> int:
    try:
        meta = getattr(response, "usage_metadata", None)
        if meta is None:
            return 0
        inp = int(getattr(meta, "prompt_token_count", 0) or 0)
        out = int(getattr(meta, "candidates_token_count", 0) or 0)
        return max(0, inp + out)
    except Exception:
        return 0


def generate_content(client: Any, *, model: str, contents: Any, config: Any = None, **kwargs: Any) -> Any:
    """Rate-limited ``client.models.generate_content`` with exponential backoff on 429/transient errors."""
    max_attempts = max(1, int(settings.gemini_retry_max_attempts))
    base = float(settings.gemini_retry_base_seconds)
    cap = float(settings.gemini_retry_max_seconds)
    last_exc: BaseException | None = None
    last_status: int | None = None
    for attempt in range(max_attempts):
        try:
            with gemini_slot(model):
                if config is None:
                    response = client.models.generate_content(
                        model=model, contents=contents, **kwargs
                    )
                else:
                    response = client.models.generate_content(
                        model=model, contents=contents, config=config, **kwargs
                    )
            record_model_tokens(model, _call_tokens(response))
            return response
        except GeminiRetryableError:
            raise
        except Exception as exc:  # noqa: BLE001
            transient, retry_after, status = inspect_transient(exc)
            last_exc = exc
            last_status = status
            if not transient or attempt >= max_attempts - 1:
                if transient:
                    raise GeminiRetryableError(
                        str(exc) or "Gemini transient error",
                        attempts=attempt + 1,
                        status_code=status,
                    ) from exc
                raise
            delay = backoff_seconds(attempt, retry_after, base=base, cap=cap)
            _LOG.warning(
                "Gemini transient error model=%s attempt=%s/%s sleep=%.2fs status=%s",
                model,
                attempt + 1,
                max_attempts,
                delay,
                status,
            )
            time.sleep(delay)
    raise GeminiRetryableError(
        str(last_exc) if last_exc else "Gemini transient error",
        attempts=max_attempts,
        status_code=last_status,
    ) from last_exc


def retrying_call(fn: Callable[[], Any], *, model: str | None = None, what: str = "gemini") -> Any:
    """Run a Gemini-related callable with the same backoff policy (REST fallback, etc.)."""
    max_attempts = max(1, int(settings.gemini_retry_max_attempts))
    base = float(settings.gemini_retry_base_seconds)
    cap = float(settings.gemini_retry_max_seconds)
    last_exc: BaseException | None = None
    last_status: int | None = None
    for attempt in range(max_attempts):
        try:
            with gemini_slot(model):
                return fn()
        except GeminiRetryableError:
            raise
        except Exception as exc:  # noqa: BLE001
            transient, retry_after, status = inspect_transient(exc)
            last_exc = exc
            last_status = status
            if not transient or attempt >= max_attempts - 1:
                if transient:
                    raise GeminiRetryableError(
                        str(exc) or f"{what} transient error",
                        attempts=attempt + 1,
                        status_code=status,
                    ) from exc
                raise
            delay = backoff_seconds(attempt, retry_after, base=base, cap=cap)
            _LOG.warning(
                "%s transient error attempt=%s/%s sleep=%.2fs status=%s",
                what,
                attempt + 1,
                max_attempts,
                delay,
                status,
            )
            time.sleep(delay)
    raise GeminiRetryableError(
        str(last_exc) if last_exc else f"{what} transient error",
        attempts=max_attempts,
        status_code=last_status,
    ) from last_exc
