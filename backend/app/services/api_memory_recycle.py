"""Nightly process recycle so RSS from a long-lived process can drop.

The API is a single unsupervised uvicorn process (Procfile / Dockerfile.api).
``uvicorn --max-requests`` is ignored without a supervisor or ``--workers > 1``,
and a request cap would not fire on a quiet day or could recycle mid-cataloging.
A daemon thread sleeps until 3:00 AM America/Los_Angeles, then exits the process
so the host starts a fresh one. Exit code 1 so Railway's default On Failure
restart policy brings the API back without requiring a cron service or API token.

Keep one API replica. Do not use this to add workers or extra processes.

The catalog worker uses the same clock. Each replica adds 0–15 minutes of jitter,
stops claiming new jobs, and exits once in-flight work finishes or after about
10 minutes (``WORKER_MEMORY_RECYCLE``). See ``start_worker_memory_recycle``.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

_LOG = logging.getLogger(__name__)

RECYCLE_ZONE = ZoneInfo("America/Los_Angeles")
RECYCLE_HOUR = 3
# Exit 1 so a default On Failure restart policy still starts a new process.
# A 0 exit would stay down unless Railway is set to Always.
RECYCLE_EXIT_CODE = 1

# Two worker replicas would otherwise restart on the same second.
WORKER_JITTER_MAX_SECONDS = 15 * 60
# Stop claiming at recycle time; leave anyway if a job is still running.
WORKER_DRAIN_MAX_SECONDS = 10 * 60

_OFF_VALUES = {"0", "false", "no", "off"}


class _Randint(Protocol):
    def randint(self, a: int, b: int) -> int: ...


def memory_recycle_enabled(env_name: str) -> bool:
    """True unless ``env_name`` is 0/false/no/off. Unset means on."""
    flag = os.environ.get(env_name, "1").strip().lower()
    return flag not in _OFF_VALUES


def next_recycle_at(
    now: datetime,
    *,
    hour: int = RECYCLE_HOUR,
    jitter: timedelta | None = None,
) -> datetime:
    """Return the next ``hour``:00 in America/Los_Angeles strictly after ``now``.

    ``jitter`` is added after that instant. The API passes none. Worker replicas
    pass 0–15 minutes so they do not restart together. Negative jitter is ignored.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(RECYCLE_ZONE)
    target = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if local >= target:
        target += timedelta(days=1)
    if jitter is not None and jitter.total_seconds() > 0:
        target += jitter
    return target


def worker_recycle_jitter(rng: _Randint | None = None) -> timedelta:
    """Uniform delay in [0, 15 minutes], inclusive."""
    chooser: _Randint = rng if rng is not None else random
    seconds = chooser.randint(0, WORKER_JITTER_MAX_SECONDS)
    return timedelta(seconds=seconds)


def plan_worker_recycle(now: datetime, *, rng: _Randint | None = None) -> datetime:
    """Next worker recycle instant: 3:00 AM Pacific plus this process's jitter."""
    return next_recycle_at(now, jitter=worker_recycle_jitter(rng))


def worker_recycle_should_start(*, is_test: bool) -> bool:
    """False in tests and when ``WORKER_MEMORY_RECYCLE`` is off."""
    if is_test:
        return False
    return memory_recycle_enabled("WORKER_MEMORY_RECYCLE")


def worker_recycle_action(*, draining: bool, busy: bool, drain_expired: bool) -> str:
    """What the worker poll loop should do this tick.

    ``claim`` — keep taking work.
    ``wait`` — recycle time has passed and a job is still running; do not claim.
    ``exit`` — idle after recycle, or the drain wait is over (even if still busy).
    """
    if not draining:
        return "claim"
    if busy and not drain_expired:
        return "wait"
    return "exit"


def worker_recycle_exit_reason(*, drain_expired: bool) -> str:
    if drain_expired:
        return (
            "Scheduled 3 AM Pacific memory recycle; drain wait elapsed with work still in flight; "
            "exiting so the host starts a fresh worker process"
        )
    return (
        "Scheduled 3 AM Pacific memory recycle; in-flight work finished; "
        "exiting so the host starts a fresh worker process"
    )


def sleep_until(target: datetime, *, chunk_seconds: float = 300.0) -> None:
    """Sleep until ``target``, waking at least every ``chunk_seconds``."""
    while True:
        remaining = (target - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, chunk_seconds))


def exit_for_memory_recycle(reason: str) -> None:
    """Log, flush, and ``os._exit(1)`` so On Failure / Always restarts the container."""
    _LOG.warning("%s", reason)
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            pass
    os._exit(RECYCLE_EXIT_CODE)


class WorkerRecycleGate:
    """Armed by the recycle thread. The worker loop reads it and exits between jobs."""

    def __init__(self) -> None:
        self._requested = threading.Event()
        self._deadline: float | None = None
        self._lock = threading.Lock()

    @property
    def draining(self) -> bool:
        return self._requested.is_set()

    def arm(
        self,
        *,
        now_mono: float | None = None,
        drain_seconds: float = WORKER_DRAIN_MAX_SECONDS,
    ) -> None:
        now = time.monotonic() if now_mono is None else now_mono
        with self._lock:
            if self._deadline is None:
                self._deadline = now + max(0.0, drain_seconds)
        self._requested.set()

    def drain_expired(self, *, now_mono: float | None = None) -> bool:
        now = time.monotonic() if now_mono is None else now_mono
        with self._lock:
            deadline = self._deadline
        return deadline is not None and now >= deadline


def start_api_memory_recycle() -> None:
    """Start a daemon thread; no-op in tests or when API_MEMORY_RECYCLE is false."""
    from app.config import settings

    if settings.is_test:
        return
    if not memory_recycle_enabled("API_MEMORY_RECYCLE"):
        flag = os.environ.get("API_MEMORY_RECYCLE", "1").strip().lower()
        _LOG.info("API memory recycle disabled (API_MEMORY_RECYCLE=%s)", flag)
        return
    threading.Thread(target=_recycle_loop, name="api-memory-recycle", daemon=True).start()


def _recycle_loop() -> None:
    while True:
        target = next_recycle_at(datetime.now(timezone.utc))
        local = target.astimezone(RECYCLE_ZONE)
        _LOG.info("API memory recycle scheduled for %s", local.isoformat())
        sleep_until(target)
        exit_for_memory_recycle(
            "Scheduled 3 AM Pacific memory recycle; exiting so the host starts a fresh API process"
        )


def start_worker_memory_recycle() -> WorkerRecycleGate | None:
    """Arm a drain at 3:00 AM Pacific plus 0–15 min. No-op in tests or when disabled.

    The thread does not claim jobs and does not exit immediately. The worker loop
    stops picking up work and calls ``os._exit`` when the current job finishes.
    If that loop is wedged, this thread exits after the drain wait.
    """
    from app.config import settings

    if not worker_recycle_should_start(is_test=settings.is_test):
        if not settings.is_test and not memory_recycle_enabled("WORKER_MEMORY_RECYCLE"):
            flag = os.environ.get("WORKER_MEMORY_RECYCLE", "1").strip().lower()
            _LOG.info("Worker memory recycle disabled (WORKER_MEMORY_RECYCLE=%s)", flag)
        return None
    gate = WorkerRecycleGate()
    threading.Thread(
        target=_worker_recycle_loop,
        args=(gate,),
        name="worker-memory-recycle",
        daemon=True,
    ).start()
    return gate


def _worker_recycle_loop(gate: WorkerRecycleGate) -> None:
    jitter = worker_recycle_jitter()
    target = next_recycle_at(datetime.now(timezone.utc), jitter=jitter)
    local = target.astimezone(RECYCLE_ZONE)
    _LOG.info(
        "Worker memory recycle scheduled for %s (jitter_seconds=%s)",
        local.isoformat(),
        int(jitter.total_seconds()),
    )
    sleep_until(target)
    _LOG.warning(
        "Scheduled 3 AM Pacific worker memory recycle; stopping new claims and draining in-flight jobs"
    )
    gate.arm()
    # The poll loop exits earlier when idle. This is the backstop if it is stuck.
    time.sleep(WORKER_DRAIN_MAX_SECONDS)
    exit_for_memory_recycle(worker_recycle_exit_reason(drain_expired=True))
