"""Nightly API process recycle so RSS from a long-lived uvicorn can drop.

Production uses a single unsupervised uvicorn process (Procfile / Dockerfile.api).
``uvicorn --max-requests`` is ignored without a supervisor or ``--workers > 1``,
and a request cap would not fire on a quiet day or could recycle mid-cataloging.
This thread sleeps until 3:00 AM America/Los_Angeles, then exits the process so
the host starts a fresh one. Exit code 1 so Railway's default On Failure restart
policy brings the API back without requiring a cron service or API token.

Keep one API replica. Do not use this to add workers or extra processes.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_LOG = logging.getLogger(__name__)

RECYCLE_ZONE = ZoneInfo("America/Los_Angeles")
RECYCLE_HOUR = 3
# Exit 1 so a default On Failure restart policy still starts a new process.
# A 0 exit would stay down unless Railway is set to Always.
RECYCLE_EXIT_CODE = 1


def next_recycle_at(now: datetime, *, hour: int = RECYCLE_HOUR) -> datetime:
    """Return the next ``hour``:00 in America/Los_Angeles strictly after ``now``."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(RECYCLE_ZONE)
    target = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if local >= target:
        target += timedelta(days=1)
    return target


def start_api_memory_recycle() -> None:
    """Start a daemon thread; no-op in tests or when API_MEMORY_RECYCLE is false."""
    from app.config import settings

    if settings.is_test:
        return
    flag = os.environ.get("API_MEMORY_RECYCLE", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        _LOG.info("API memory recycle disabled (API_MEMORY_RECYCLE=%s)", flag)
        return
    threading.Thread(target=_recycle_loop, name="api-memory-recycle", daemon=True).start()


def _recycle_loop() -> None:
    while True:
        target = next_recycle_at(datetime.now(timezone.utc))
        local = target.astimezone(RECYCLE_ZONE)
        _LOG.info("API memory recycle scheduled for %s", local.isoformat())
        while True:
            remaining = (target - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 300))
        _LOG.warning(
            "Scheduled 3 AM Pacific memory recycle; exiting so the host starts a fresh API process"
        )
        for handler in logging.getLogger().handlers:
            try:
                handler.flush()
            except Exception:
                pass
        os._exit(RECYCLE_EXIT_CODE)
