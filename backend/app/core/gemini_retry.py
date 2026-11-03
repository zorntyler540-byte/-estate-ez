"""Transient Gemini error detection, Retry-After parsing, and exponential backoff."""

from __future__ import annotations

import random
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

TRANSIENT_HTTP = frozenset({408, 429, 500, 502, 503, 504})

_RETRY_ATTEMPT_RE = re.compile(r"\[catalog-retry (\d+)/(\d+)\]")
_RETRY_IN_SECONDS_RE = re.compile(r"retry in (?:about )?(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


class GeminiRetryableError(Exception):
    """Raised when Gemini is still failing after call-level retries (job may re-queue)."""

    def __init__(self, message: str, *, attempts: int = 0, status_code: int | None = None):
        super().__init__(message)
        self.attempts = attempts
        self.status_code = status_code


def parse_retry_after(value: object, *, now: datetime | None = None) -> float | None:
    """Return wait seconds from an HTTP Retry-After header (delta-seconds or HTTP-date)."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
        if seconds >= 0:
            return seconds
        return None
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - clock).total_seconds())


def retry_after_from_headers(headers: Any) -> float | None:
    if headers is None:
        return None
    try:
        value = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:
        return None
    return parse_retry_after(value)


def retry_after_from_message(message: str) -> float | None:
    if not message:
        return None
    match = _RETRY_IN_SECONDS_RE.search(message)
    if not match:
        return None
    try:
        return max(0.0, float(match.group(1)))
    except ValueError:
        return None


def _status_code_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "code", "status"):
        raw = getattr(exc, attr, None)
        if raw is None:
            continue
        try:
            code = int(raw)
        except (TypeError, ValueError):
            continue
        if code > 0:
            return code
    response = getattr(exc, "response", None)
    if response is not None:
        raw = getattr(response, "status_code", None)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
    return None


def inspect_transient(exc: BaseException) -> tuple[bool, float | None, int | None]:
    """Return ``(is_transient, retry_after_seconds, status_code)`` for an API/SDK error."""
    status = _status_code_of(exc)
    retry_after = None
    response = getattr(exc, "response", None)
    if response is not None:
        retry_after = retry_after_from_headers(getattr(response, "headers", None))
    if retry_after is None:
        retry_after = parse_retry_after(getattr(exc, "retry_after", None))
    if retry_after is None:
        retry_after = retry_after_from_message(str(exc))

    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if status in TRANSIENT_HTTP:
        return True, retry_after, status
    if "resourceexhausted" in name or "too many requests" in text or "rate limit" in text:
        return True, retry_after, status or 429
    if "unavailable" in name or "deadline exceeded" in text or "broken pipe" in text:
        return True, retry_after, status
    if "connection" in name or "timeout" in name or "temporarily" in text:
        return True, retry_after, status
    if status is None and ("429" in text or "503" in text or "502" in text):
        return True, retry_after, None
    return False, retry_after, status


def backoff_seconds(
    attempt: int,
    retry_after: float | None = None,
    *,
    base: float = 1.0,
    cap: float = 32.0,
    rng: random.Random | None = None,
) -> float:
    """Full-jitter exponential backoff. ``Retry-After`` wins, with a little jitter so replicas desync."""
    dice = rng or random
    if retry_after is not None and retry_after >= 0:
        jitter = dice.uniform(0.0, min(1.0, max(0.05, retry_after * 0.1)))
        return min(120.0, retry_after + jitter)
    exp = min(cap, base * (2 ** max(0, attempt)))
    return dice.uniform(0.0, exp)


def parse_catalog_retry_attempt(error_message: str | None) -> int:
    if not error_message:
        return 0
    match = _RETRY_ATTEMPT_RE.search(error_message)
    if not match:
        return 0
    try:
        return max(0, int(match.group(1)))
    except ValueError:
        return 0


def format_catalog_retry_message(attempt: int, max_attempts: int, exc: BaseException) -> str:
    detail = str(exc).strip().splitlines()[0][:240] if str(exc).strip() else "Gemini transient error"
    return f"[catalog-retry {attempt}/{max_attempts}] {detail}"


def catalog_retry_exhausted_message(max_attempts: int, exc: BaseException) -> str:
    detail = str(exc).strip().splitlines()[0][:240] if str(exc).strip() else "Gemini transient error"
    return (
        f"Gemini rate limited after {max_attempts} attempts. {detail} "
        "Use Retry failed when the queue is quieter."
    )
