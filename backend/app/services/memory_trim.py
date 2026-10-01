"""Give RAM back to the OS after a catalog job (CPython often keeps it otherwise)."""

from __future__ import annotations

import gc
import logging

_LOG = logging.getLogger(__name__)


def trim_process_memory() -> None:
    """Run GC, then ``malloc_trim`` on glibc (Railway Linux). Harmless no-op elsewhere."""
    gc.collect()
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except Exception:
        _LOG.debug("malloc_trim skipped", exc_info=True)
