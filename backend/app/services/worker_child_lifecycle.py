"""Tear down isolated catalog-child runtimes so the process can actually exit.

``sys.exit`` / ``SystemExit`` waits for non-daemon threads (ThreadPoolExecutor
workers, google-genai/httpx). The child must shut those down and then
``os._exit`` so Railway cgroup RSS drops when the job is done.
"""

from __future__ import annotations

import logging
import os

_LOG = logging.getLogger(__name__)


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
    try:
        shutdown_isolated_child_runtime()
    except Exception:  # noqa: BLE001
        _LOG.debug("isolated child shutdown failed", exc_info=True)
    os._exit(int(code))
