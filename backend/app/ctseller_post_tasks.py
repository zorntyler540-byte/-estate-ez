"""Background CT Seller posting — do not hold the HTTP request open for photo uploads."""

from __future__ import annotations

import logging
import threading
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from app.db import SessionLocal
from app.models import Account, Lot
from app.services.ctseller import CTSellerError
from app.services.ctseller_post import post_one_lot_to_ctseller

_LOG = logging.getLogger(__name__)

_COMPLETED_HOLD = timedelta(minutes=15)
_lock = threading.Lock()
_sale_runs: dict[str, dict[str, Any]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def reset_post_runtime_state() -> None:
    with _lock:
        _sale_runs.clear()


def sale_post_status(sale_id: str) -> dict[str, Any]:
    with _lock:
        run = _sale_runs.get(sale_id)
        if not run:
            return {
                "active": False,
                "total": 0,
                "done": 0,
                "posted": 0,
                "failed": 0,
                "skipped": 0,
                "queued": 0,
                "ctseller_sale_id": None,
                "current_lot_id": None,
                "current_sku": None,
                "current_phase": None,
                "results": [],
            }
        finished_at = run.get("finished_at")
        active = bool(run.get("active"))
        if (not active) and isinstance(finished_at, datetime):
            if _utcnow() - finished_at > _COMPLETED_HOLD:
                return {
                    "active": False,
                    "total": 0,
                    "done": 0,
                    "posted": 0,
                    "failed": 0,
                    "skipped": 0,
                    "queued": 0,
                    "ctseller_sale_id": run.get("ctseller_sale_id"),
                    "current_lot_id": None,
                    "current_sku": None,
                    "current_phase": None,
                    "results": [],
                }
        return {
            "active": active,
            "total": int(run.get("total") or 0),
            "done": int(run.get("done") or 0),
            "posted": int(run.get("posted") or 0),
            "failed": int(run.get("failed") or 0),
            "skipped": int(run.get("skipped") or 0),
            "queued": int(run.get("queued") or 0),
            "ctseller_sale_id": run.get("ctseller_sale_id"),
            "current_lot_id": run.get("current_id"),
            "current_sku": run.get("current_sku"),
            "current_phase": run.get("current_phase"),
            "results": list(run.get("results") or []),
        }


def sale_post_is_active(sale_id: str) -> bool:
    with _lock:
        run = _sale_runs.get(sale_id)
        return bool(run and run.get("active"))


def start_sale_post_in_background(
    *,
    account_id: str,
    sale_id: str,
    lot_ids: list[str],
    skus: dict[str, str | None],
    remote_sale_id: int,
    force: bool,
) -> None:
    ordered = [x for x in lot_ids if (x or "").strip()]
    if not ordered:
        return
    with _lock:
        existing = _sale_runs.get(sale_id)
        if existing and existing.get("active"):
            raise RuntimeError("ctseller_post_already_running")
        _sale_runs[sale_id] = {
            "active": True,
            "account_id": account_id,
            "ctseller_sale_id": int(remote_sale_id),
            "lot_ids": ordered,
            "total": len(ordered),
            "done": 0,
            "posted": 0,
            "failed": 0,
            "skipped": 0,
            "queued": len(ordered),
            "current_id": None,
            "current_sku": None,
            "current_phase": "starting",
            "results": [],
            "force": force,
            "finished_at": None,
        }

    def _runner() -> None:
        try:
            _run_sale_post(
                account_id=account_id,
                sale_id=sale_id,
                lot_ids=ordered,
                skus=skus,
                remote_sale_id=int(remote_sale_id),
                force=force,
            )
        except Exception:
            _LOG.error("CT Seller sale post crashed sale_id=%s\n%s", sale_id, traceback.format_exc())
            with _lock:
                run = _sale_runs.get(sale_id)
                if run:
                    run["active"] = False
                    run["current_phase"] = "failed"
                    run["finished_at"] = _utcnow()
                    run["results"] = list(run.get("results") or []) + [
                        {
                            "lot_id": "",
                            "sku": None,
                            "ok": False,
                            "skipped": False,
                            "error": "Poster thread crashed — check API logs",
                            "ctseller_item_id": None,
                            "photos_uploaded": None,
                        }
                    ]

    threading.Thread(target=_runner, daemon=True, name=f"ctseller-post-{sale_id[:8]}").start()


def _set_phase(sale_id: str, lot_id: str | None, sku: str | None, phase: str) -> None:
    with _lock:
        run = _sale_runs.get(sale_id)
        if not run:
            return
        run["current_id"] = lot_id
        run["current_sku"] = sku
        run["current_phase"] = phase


def _append_result(sale_id: str, row: dict[str, Any], *, posted: bool = False, failed: bool = False) -> None:
    with _lock:
        run = _sale_runs.get(sale_id)
        if not run:
            return
        run["results"] = list(run.get("results") or []) + [row]
        run["done"] = int(run.get("done") or 0) + 1
        if posted:
            run["posted"] = int(run.get("posted") or 0) + 1
        if failed:
            run["failed"] = int(run.get("failed") or 0) + 1


def _run_sale_post(
    *,
    account_id: str,
    sale_id: str,
    lot_ids: list[str],
    skus: dict[str, str | None],
    remote_sale_id: int,
    force: bool,
) -> None:
    from app.main import _ctseller_client_for_account

    with SessionLocal() as db:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise RuntimeError("Account not found")
        try:
            client = _ctseller_client_for_account(account, db, probe=False)
            try:
                detail = client.sale_detail(int(remote_sale_id))
            except CTSellerError as exc:
                if exc.status_code not in (401, 403):
                    raise
                client = _ctseller_client_for_account(account, db, force=True)
                detail = client.sale_detail(int(remote_sale_id))
            try:
                allowed_areas = client.list_areas()
            except CTSellerError:
                allowed_areas = None
        except (CTSellerError, HTTPException) as exc:
            msg = str(exc)
            for lot_id in lot_ids:
                sku = skus.get(lot_id)
                _append_result(
                    sale_id,
                    {
                        "lot_id": lot_id,
                        "sku": sku,
                        "ok": False,
                        "skipped": False,
                        "error": msg,
                        "ctseller_item_id": None,
                        "photos_uploaded": None,
                    },
                    failed=True,
                )
            with _lock:
                run = _sale_runs.get(sale_id)
                if run:
                    run["active"] = False
                    run["current_phase"] = "failed"
                    run["finished_at"] = _utcnow()
            return

        location_id = detail.get("locationid")
        irm = detail.get("saleitemreceiptmethod")
        tax = detail.get("tax")
        buyers_premium = detail.get("buyerspremium")

        for lot_id in lot_ids:
            sku = skus.get(lot_id)
            _set_phase(sale_id, lot_id, sku, "creating item")
            lot = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
            if lot is None:
                _append_result(
                    sale_id,
                    {
                        "lot_id": lot_id,
                        "sku": sku,
                        "ok": False,
                        "skipped": True,
                        "error": "Lot not found",
                        "ctseller_item_id": None,
                        "photos_uploaded": None,
                    },
                )
                continue
            try:
                out = post_one_lot_to_ctseller(
                    db=db,
                    lot=lot,
                    client=client,
                    remote_sale_id=int(remote_sale_id),
                    location_id=location_id,
                    item_receipt_method=irm,
                    force=force,
                    allowed_areas=allowed_areas,
                    tax=tax,
                    buyers_premium=buyers_premium,
                    on_phase=lambda msg, sid=sale_id, lid=lot_id, sk=sku: _set_phase(sid, lid, sk, msg),
                )
                _append_result(
                    sale_id,
                    {
                        "lot_id": lot.id,
                        "sku": sku,
                        "ok": True,
                        "skipped": False,
                        "error": None,
                        "ctseller_item_id": out.ctseller_item_id,
                        "photos_uploaded": out.photos_uploaded,
                    },
                    posted=True,
                )
            except HTTPException as exc:
                detail_msg = exc.detail
                msg = detail_msg if isinstance(detail_msg, str) else str(detail_msg)
                skipped = exc.status_code in (409,)
                _append_result(
                    sale_id,
                    {
                        "lot_id": lot_id,
                        "sku": sku,
                        "ok": False,
                        "skipped": skipped,
                        "error": msg,
                        "ctseller_item_id": getattr(lot, "ctseller_item_id", None),
                        "photos_uploaded": None,
                    },
                    failed=not skipped,
                )
            except CTSellerError as exc:
                _append_result(
                    sale_id,
                    {
                        "lot_id": lot_id,
                        "sku": sku,
                        "ok": False,
                        "skipped": False,
                        "error": str(exc),
                        "ctseller_item_id": getattr(lot, "ctseller_item_id", None),
                        "photos_uploaded": None,
                    },
                    failed=True,
                )
            except Exception as exc:
                _LOG.error("CT Seller post failed lot_id=%s\n%s", lot_id, traceback.format_exc())
                _append_result(
                    sale_id,
                    {
                        "lot_id": lot_id,
                        "sku": sku,
                        "ok": False,
                        "skipped": False,
                        "error": f"{type(exc).__name__}: {exc}"[:2000],
                        "ctseller_item_id": getattr(lot, "ctseller_item_id", None),
                        "photos_uploaded": None,
                    },
                    failed=True,
                )
            db.expire_all()

    with _lock:
        run = _sale_runs.get(sale_id)
        if run:
            run["active"] = False
            run["current_id"] = None
            run["current_phase"] = "done"
            run["finished_at"] = _utcnow()
