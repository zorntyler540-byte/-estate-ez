"""Facebook Push-calendar follow-up off the request thread.

Snapshot ingest queues local slot bookkeeping here; the owner's auto-approve switch
queues a publishing pass (Gemini + Graph) so the settings request returns at once.
One runner per (account, sale): a request that lands while that sale is already syncing
marks it dirty and the runner goes around once more with fresh rows.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

_LOG = logging.getLogger(__name__)
_lock = threading.Lock()
_running: set[tuple[str, int]] = set()
_dirty: set[tuple[str, int]] = set()
_publish: set[tuple[str, int]] = set()


def _start_thread(target: Callable[[], None], name: str) -> None:
    threading.Thread(target=target, daemon=True, name=name).start()


def reset_engagement_task_state() -> None:
    with _lock:
        _running.clear()
        _dirty.clear()
        _publish.clear()


def schedule_facebook_calendar_sync(
    bind: Engine,
    account_id: str,
    ctseller_sale_id: int,
    *,
    publish_new_slots: bool = False,
) -> None:
    """Run ``sync_facebook_calendar_after_snapshot`` in a daemon thread with its own session."""
    key = (str(account_id), int(ctseller_sale_id))
    with _lock:
        if publish_new_slots:
            _publish.add(key)
        if key in _running:
            _dirty.add(key)
            return
        _running.add(key)
    factory = sessionmaker(bind=bind, autoflush=False, autocommit=False, future=True)

    def _runner() -> None:
        _run_until_clean(factory, key)

    try:
        _start_thread(_runner, f"fb-cal-sync-{key[1]}")
    except Exception:
        with _lock:
            _running.discard(key)
            _dirty.discard(key)
            _publish.discard(key)
        raise


def _run_until_clean(factory: sessionmaker, key: tuple[str, int]) -> None:
    from app.services.facebook_calendar import sync_facebook_calendar_after_snapshot

    account_id, sale_id = key
    try:
        while True:
            with _lock:
                publish = key in _publish
                _publish.discard(key)
            try:
                with factory() as db:
                    sync_facebook_calendar_after_snapshot(
                        db, account_id, sale_id, publish_new_slots=publish
                    )
            except Exception:
                _LOG.exception(
                    "Facebook calendar sync after snapshot failed account=%s sale=%s",
                    account_id,
                    sale_id,
                )
            with _lock:
                if key in _dirty:
                    _dirty.discard(key)
                    continue
                _running.discard(key)
                return
    except BaseException:
        with _lock:
            _running.discard(key)
            _dirty.discard(key)
            _publish.discard(key)
        raise
