"""Dedicated catalog worker heartbeat (``worker_heartbeats``).

The worker parent upserts its row every ``HEARTBEAT_WRITE_SECONDS`` from the poll loop, busy
or idle. The API reads the newest ``last_seen_at``: fresh means a worker is alive and will
claim queued jobs, so the API must not run Gemini itself (OOM risk on the web dyno).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.config import settings
from app.db import SessionLocal
from app.models import WorkerHeartbeat

_LOG = logging.getLogger(__name__)

HEARTBEAT_WRITE_SECONDS = 10.0
HEARTBEAT_PRUNE_AFTER = timedelta(days=1)
# GET /jobs and /jobs/catalog-status are polled constantly; one DB read per window per process.
_READ_CACHE_SECONDS = 5.0

_read_lock = threading.Lock()
_read_cache: tuple[float, datetime | None] | None = None


def worker_instance_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"[:128]


def stale_after_seconds() -> int:
    return max(15, int(getattr(settings, "worker_heartbeat_stale_seconds", 90) or 90))


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def write_heartbeat(
    *,
    instance_id: str,
    started_at: datetime,
    slots: int,
    slots_in_use: int,
    now: datetime | None = None,
) -> None:
    clock = now or datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.get(WorkerHeartbeat, instance_id)
        if row is None:
            row = WorkerHeartbeat(id=instance_id, started_at=started_at)
            db.add(row)
        row.last_seen_at = clock
        row.slots = int(slots)
        row.slots_in_use = int(slots_in_use)
        db.query(WorkerHeartbeat).filter(
            WorkerHeartbeat.last_seen_at < clock - HEARTBEAT_PRUNE_AFTER
        ).delete(synchronize_session=False)
        db.commit()


def latest_heartbeat_at(*, use_cache: bool = True) -> datetime | None:
    global _read_cache
    now_mono = time.monotonic()
    if use_cache:
        with _read_lock:
            if _read_cache is not None and now_mono - _read_cache[0] < _READ_CACHE_SECONDS:
                return _read_cache[1]
    try:
        with SessionLocal() as db:
            latest = _aware(db.query(func.max(WorkerHeartbeat.last_seen_at)).scalar())
    except Exception:  # noqa: BLE001
        _LOG.warning("worker heartbeat read failed — treating worker as down", exc_info=True)
        latest = None
    with _read_lock:
        _read_cache = (now_mono, latest)
    return latest


def worker_heartbeat_fresh(now: datetime | None = None, *, use_cache: bool = True) -> bool:
    latest = latest_heartbeat_at(use_cache=use_cache)
    if latest is None:
        return False
    clock = now or datetime.now(timezone.utc)
    return (clock - latest).total_seconds() < stale_after_seconds()


def reset_heartbeat_cache() -> None:
    global _read_cache
    with _read_lock:
        _read_cache = None


class HeartbeatWriter:
    """Throttled writer for the worker poll loop. Never raises."""

    def __init__(self, *, slots: int, instance_id: str | None = None) -> None:
        self.instance_id = instance_id or worker_instance_id()
        self.started_at = datetime.now(timezone.utc)
        self.slots = int(slots)
        self._last_mono = 0.0

    def maybe_beat(self, slots_in_use: int, *, now_mono: float | None = None) -> bool:
        now = time.monotonic() if now_mono is None else now_mono
        if self._last_mono and now - self._last_mono < HEARTBEAT_WRITE_SECONDS:
            return False
        self._last_mono = now
        try:
            write_heartbeat(
                instance_id=self.instance_id,
                started_at=self.started_at,
                slots=self.slots,
                slots_in_use=slots_in_use,
            )
        except Exception:  # noqa: BLE001
            _LOG.exception("worker heartbeat write failed")
            return False
        return True
