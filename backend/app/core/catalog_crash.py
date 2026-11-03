"""Child-process crash / OOM attempt counters stored on ``ProcessingJob.error_message``.

A dedicated worker re-queues a job when its isolated child dies while the row is still
``processing``. That marker must survive the next ``process_job`` start (which otherwise
clears ``error_message``) so a later OOM can increment instead of looping forever.
"""

from __future__ import annotations

import re

from app.config import settings
from app.models import JobStatus, ProcessingJob

_CRASH_ATTEMPT_RE = re.compile(r"\[catalog-crash (\d+)/(\d+)\]")
_CRASH_EXHAUSTED_MARKERS = (
    "worker child crashed",
    "isolated worker child exited",
)


def catalog_crash_max_attempts() -> int:
    return max(1, int(getattr(settings, "worker_child_crash_retries", 3) or 3))


def parse_catalog_crash_attempt(error_message: str | None) -> int:
    if not error_message:
        return 0
    match = _CRASH_ATTEMPT_RE.search(error_message)
    if not match:
        return 0
    try:
        return max(0, int(match.group(1)))
    except ValueError:
        return 0


def format_catalog_crash_message(attempt: int, max_attempts: int, reason: str) -> str:
    detail = reason.strip().splitlines()[0][:240] if reason.strip() else "isolated worker child exited"
    return f"[catalog-crash {attempt}/{max_attempts}] {detail}"


def catalog_crash_exhausted_message(max_attempts: int, reason: str) -> str:
    detail = reason.strip().splitlines()[0][:240] if reason.strip() else "isolated worker child exited"
    return (
        f"Worker child crashed {max_attempts} times (OOM or unexpected exit). {detail} "
        "Use Retry failed after reducing lot size or photo count."
    )


def is_catalog_crash_exhausted_error(msg: str | None) -> bool:
    text = (msg or "").lower()
    if not text:
        return False
    if "worker child crashed" in text and "times" in text:
        return True
    return any(marker in text for marker in _CRASH_EXHAUSTED_MARKERS) and "retry failed" in text


def processing_error_message_preserving_crash(prior: str | None) -> str | None:
    """Keep the crash marker while a retry runs so a later child death can increment it."""
    crash = parse_catalog_crash_attempt(prior)
    if not crash:
        return None
    return format_catalog_crash_message(
        crash,
        catalog_crash_max_attempts(),
        "retry in progress after prior child crash",
    )


def apply_child_crash(row: ProcessingJob, *, reason: str) -> str:
    """Increment the crash counter on a still-processing job. Returns ``queued`` or ``failed``."""
    max_n = catalog_crash_max_attempts()
    nxt = parse_catalog_crash_attempt(row.error_message) + 1
    row.cancel_requested = False
    if nxt < max_n:
        row.status = JobStatus.queued
        row.error_message = format_catalog_crash_message(nxt, max_n, reason)
        return "queued"
    row.status = JobStatus.failed
    row.error_message = catalog_crash_exhausted_message(max_n, reason)[:4000]
    return "failed"
