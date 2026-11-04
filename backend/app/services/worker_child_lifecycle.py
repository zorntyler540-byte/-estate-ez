"""Tear down isolated catalog-child runtimes so the process can actually exit.

``sys.exit`` / ``SystemExit`` waits for non-daemon threads (ThreadPoolExecutor
workers, google-genai/httpx). The child must shut those down and then
``os._exit`` so Railway cgroup RSS drops when the job is done.

After the job row is persisted, a daemon watchdog guarantees ``os._exit`` even
if cleanup (R2, client.close, malloc_trim) hangs.
"""

from __future__ import annotations

import logging
import os
import threading
import time

_LOG = logging.getLogger(__name__)

_watchdog_armed = False
_complete_logged = False
_watchdog_lock = threading.Lock()


def isolated_child_exit_seconds() -> float:
    raw = os.environ.get("WORKER_CHILD_EXIT_SECONDS")
    if raw:
        try:
            return max(1.0, min(120.0, float(raw)))
        except ValueError:
            pass
    try:
        from app.config import settings

        return max(1.0, min(120.0, float(settings.worker_child_exit_seconds)))
    except Exception:
        return 15.0


def log_isolated_job_complete(
    *,
    job_id: str,
    account_id: str,
    status: str,
    lots: int,
    photos: int,
    duration_s: float = -1.0,
    rss_mb: float | None = None,
) -> None:
    """Always emit the job-complete line once the result is persisted (or the child is exiting)."""
    global _complete_logged
    with _watchdog_lock:
        if _complete_logged:
            return
        _complete_logged = True
    if rss_mb is None:
        try:
            from app.proc_memory import process_rss_mb

            rss_mb = process_rss_mb()
        except Exception:  # noqa: BLE001
            rss_mb = None
    _LOG.info(
        "Isolated catalog job complete job_id=%s account_id=%s status=%s lots=%s photos=%s "
        "duration=%.1fs child_rss_mb=%.0f",
        job_id,
        account_id,
        status,
        lots,
        photos,
        duration_s,
        rss_mb if rss_mb is not None else -1,
    )


def arm_isolated_child_exit_watchdog(code: int = 0) -> None:
    """After persist: force ``os._exit`` in N seconds even if cleanup never returns."""
    global _watchdog_armed
    if os.environ.get("WORKER_ISOLATED_CHILD") != "1":
        return
    with _watchdog_lock:
        if _watchdog_armed:
            return
        _watchdog_armed = True
    seconds = isolated_child_exit_seconds()

    def _fire() -> None:
        time.sleep(seconds)
        _LOG.error(
            "Isolated child exit watchdog fired after %.1fs — os._exit(%s)",
            seconds,
            code,
        )
        os._exit(int(code))

    threading.Thread(target=_fire, name="child-exit-watchdog", daemon=True).start()
    _LOG.info("Isolated child exit watchdog armed seconds=%.1f", seconds)


def note_job_persisted(
    *,
    job_id: str,
    account_id: str,
    status: str,
    lots: int,
    photos: int,
    duration_s: float = -1.0,
) -> None:
    """Call as soon as the job row is completed/failed so the child cannot linger."""
    log_isolated_job_complete(
        job_id=job_id,
        account_id=account_id,
        status=status,
        lots=lots,
        photos=photos,
        duration_s=duration_s,
    )
    arm_isolated_child_exit_watchdog(0)


def _close_optional(obj: object) -> None:
    if obj is None:
        return
    for name in ("close", "aclose"):
        fn = getattr(obj, name, None)
        if not callable(fn):
            continue
        try:
            fn()
        except Exception:  # noqa: BLE001
            _LOG.debug("close %s failed", name, exc_info=True)
        return


def shutdown_isolated_child_runtime() -> None:
    """Drop Gemini clients, thread pools, and extra DB engines; then malloc_trim."""
    try:
        from app.core import gemini as gemini_mod

        gemini_mod._shutdown_tape_verify_pool()
        close = getattr(gemini_mod, "close_genai_client", None)
        if callable(close):
            close()
    except Exception:  # noqa: BLE001
        _LOG.debug("gemini runtime shutdown skipped", exc_info=True)

    try:
        from pipeline import pipeline as pipeline_mod

        close = getattr(pipeline_mod, "close_genai_client", None)
        if callable(close):
            close()
    except Exception:  # noqa: BLE001
        _LOG.debug("pipeline client shutdown skipped", exc_info=True)

    try:
        from app.core import gemini_slots

        close = getattr(gemini_slots, "close_lock_engine", None)
        if callable(close):
            close()
    except Exception:  # noqa: BLE001
        _LOG.debug("gemini slot engine shutdown skipped", exc_info=True)

    try:
        from app.db import engine

        engine.dispose()
    except Exception:  # noqa: BLE001
        _LOG.debug("db engine dispose skipped", exc_info=True)

    try:
        from app.services.memory_trim import trim_process_memory

        trim_process_memory()
    except Exception:  # noqa: BLE001
        _LOG.debug("malloc_trim skipped", exc_info=True)


def exit_isolated_child(code: int) -> None:
    """Hard-exit so leftover SDK/thread-pool threads cannot keep the child alive."""
    arm_isolated_child_exit_watchdog(int(code))
    try:
        shutdown_isolated_child_runtime()
    except Exception:  # noqa: BLE001
        _LOG.debug("isolated child shutdown failed", exc_info=True)
    os._exit(int(code))


def reset_isolated_child_lifecycle_for_tests() -> None:
    global _watchdog_armed, _complete_logged
    with _watchdog_lock:
        _watchdog_armed = False
        _complete_logged = False
