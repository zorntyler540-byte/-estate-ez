"""Background Facebook Page posting — do not hold the HTTP request for multi-lot uploads."""

from __future__ import annotations

import logging
import threading
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import SessionLocal
from app.models import Account, User

_LOG = logging.getLogger(__name__)
_COMPLETED_HOLD = timedelta(minutes=15)
_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}

# One lot stays on the request so the modal can show a result immediately.
# Two or more lots (Push-queue / dashboard selection) run in a daemon thread.
SYNC_LOT_LIMIT = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def reset_facebook_post_runtime_state() -> None:
    with _lock:
        _runs.clear()


def facebook_post_batch_status(batch_id: str, account_id: str) -> dict[str, Any] | None:
    with _lock:
        run = _runs.get(batch_id)
        if not run or run.get("account_id") != account_id:
            return None
        finished_at = run.get("finished_at")
        active = bool(run.get("active"))
        if (not active) and isinstance(finished_at, datetime):
            if _utcnow() - finished_at > _COMPLETED_HOLD:
                return None
        return {
            "queued": True,
            "batch_id": batch_id,
            "active": active,
            "total": int(run.get("total") or 0),
            "done": int(run.get("done") or 0),
            "posted": int(run.get("posted") or 0),
            "failed": int(run.get("failed") or 0),
            "current_lot_id": run.get("current_lot_id"),
            "results": list(run.get("results") or []),
        }


def start_facebook_posts_in_background(
    *,
    account_id: str,
    user_id: str,
    items: list[dict[str, Any]],
    include_follow_hook: bool,
    confirm: bool,
    force: bool,
    source: str,
) -> str:
    batch_id = uuid.uuid4().hex
    ordered = list(items)
    with _lock:
        _runs[batch_id] = {
            "active": True,
            "account_id": account_id,
            "user_id": user_id,
            "total": len(ordered),
            "done": 0,
            "posted": 0,
            "failed": 0,
            "current_lot_id": None,
            "results": [],
            "finished_at": None,
        }

    def _runner() -> None:
        try:
            _run_facebook_posts(
                batch_id=batch_id,
                account_id=account_id,
                user_id=user_id,
                items=ordered,
                include_follow_hook=include_follow_hook,
                confirm=confirm,
                force=force,
                source=source,
            )
        except Exception:
            _LOG.error("Facebook Page batch crashed batch_id=%s\n%s", batch_id, traceback.format_exc())
            with _lock:
                run = _runs.get(batch_id)
                if run:
                    run["active"] = False
                    run["finished_at"] = _utcnow()
                    run["results"] = list(run.get("results") or []) + [
                        {
                            "lot_id": "",
                            "ok": False,
                            "error": "Facebook poster thread crashed — check API logs",
                        }
                    ]

    threading.Thread(target=_runner, daemon=True, name=f"fb-page-post-{batch_id[:8]}").start()
    return batch_id


def _set_current(batch_id: str, lot_id: str | None) -> None:
    with _lock:
        run = _runs.get(batch_id)
        if run:
            run["current_lot_id"] = lot_id


def _append_result(batch_id: str, row: dict[str, Any]) -> None:
    with _lock:
        run = _runs.get(batch_id)
        if not run:
            return
        run["results"] = list(run.get("results") or []) + [row]
        run["done"] = int(run.get("done") or 0) + 1
        if row.get("ok"):
            run["posted"] = int(run.get("posted") or 0) + 1
        elif not row.get("skipped"):
            run["failed"] = int(run.get("failed") or 0) + 1


def _run_facebook_posts(
    *,
    batch_id: str,
    account_id: str,
    user_id: str,
    items: list[dict[str, Any]],
    include_follow_hook: bool,
    confirm: bool,
    force: bool,
    source: str,
) -> None:
    from app.services.facebook_page import publish_posts

    with SessionLocal() as db:
        account = db.query(Account).filter(Account.id == account_id).first()
        user = db.query(User).filter(User.id == user_id, User.account_id == account_id).first()
        if not account or not user:
            _append_result(batch_id, {"lot_id": "", "ok": False, "error": "Account or user not found"})
            with _lock:
                run = _runs.get(batch_id)
                if run:
                    run["active"] = False
                    run["finished_at"] = _utcnow()
            return

        def on_result(row: dict[str, Any]) -> None:
            _append_result(batch_id, row)
            db.expire_all()

        def on_lot(lot_id: str) -> None:
            _set_current(batch_id, lot_id)

        publish_posts(
            db,
            account=account,
            user=user,
            items=items,
            include_follow_hook=include_follow_hook,
            confirm=confirm,
            force=force,
            source=source,
            on_lot=on_lot,
            on_result=on_result,
        )

    with _lock:
        run = _runs.get(batch_id)
        if run:
            run["active"] = False
            run["current_lot_id"] = None
            run["finished_at"] = _utcnow()
