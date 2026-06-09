"""Process-wide limit on concurrent Gemini HTTP calls (jobs + regenerate + triage)."""

from __future__ import annotations

import threading
from contextlib import contextmanager

from app.config import settings

_lock = threading.Lock()
_sem: threading.Semaphore | None = None


def _semaphore() -> threading.Semaphore:
    global _sem
    if _sem is None:
        with _lock:
            if _sem is None:
                cap = max(1, settings.gemini_global_concurrency)
                _sem = threading.Semaphore(cap)
    return _sem


@contextmanager
def gemini_slot():
    sem = _semaphore()
    sem.acquire()
    try:
        yield
    finally:
        sem.release()
