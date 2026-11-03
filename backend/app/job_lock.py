"""Catalog-job advisory-lock key.

Kept in a tiny module so the worker parent can probe Postgres without importing
the Gemini pipeline (PIL, google-genai, decoded-image helpers).
"""

from __future__ import annotations

import zlib


def job_advisory_lock_key(job_id: str) -> int:
    """Stable 31-bit key for ``pg_try_advisory_lock`` (one catalog worker per job id)."""
    return zlib.crc32(job_id.encode("utf-8")) & 0x7FFFFFFF
