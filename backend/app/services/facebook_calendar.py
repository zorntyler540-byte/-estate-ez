"""Map the Push-tab social calendar onto Facebook Page scheduled posts.

The engagement calendar is computed from the latest Watcher snapshot (not a
stored slot table). Push lots fill daily Pacific slots in push-rank order:

* 09:00 Facebook (Page feed — the only slot v1 can publish)
* 12:15 Instagram, 16:30 FB+IG Stories, 18:00 TikTok (paste-only)
* Closing-day 17:40 Stories countdown (paste-only)

Lots move buckets when a new snapshot is scored. This module schedules,
reschedules, and deletes Graph posts for the Facebook 09:00 slots only.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import Account, EngagementSnapshot, FacebookPagePost, Lot, User
from app.services.engagement import PACIFIC, build_social_calendar
from app.services.facebook_caption import draft_facebook_caption
from app.services.facebook_crypto import unseal_facebook_secret
from app.services.facebook_page import (
    SCHEDULE_MAX_LEAD,
    SCHEDULE_MIN_LEAD,
    _clear_token_error,
    _existing_posts,
    _lot_photos,
    _mark_token_error,
    _sale_title,
    ctbids_lot_url,
    publish_posts,
)
from app.services.meta_graph import MetaGraphError, delete_page_post, update_scheduled_page_post

_LOG = logging.getLogger(__name__)

CALENDAR_SOURCE = "push_calendar"
FACEBOOK_PLATFORM = "Facebook"
UTC = timezone.utc


def calendar_slot_key(ctseller_sale_id: int, lot_id: str) -> str:
    return f"pushcal:{int(ctseller_sale_id)}:{lot_id}"


def slot_datetime(post_date: str, local_time: str, *, zone: str = "PT") -> datetime:
    day = date.fromisoformat(post_date)
    parts = (local_time or "09:00").split(":")
    hour = int(parts[0]) if parts else 9
    minute = int(parts[1]) if len(parts) > 1 else 0
    tz = PACIFIC if zone in {"PT", "America/Los_Angeles"} else ZoneInfo("America/Los_Angeles")
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def schedule_window_state(when: datetime, *, now: datetime | None = None) -> str:
    """ok | missed | too_soon | too_far."""
    moment = _as_utc(now or datetime.now(UTC))
    target = _as_utc(when)
    delta = target - moment
    if delta <= timedelta(0):
        return "missed"
    if delta < SCHEDULE_MIN_LEAD:
        return "too_soon"
    if delta > SCHEDULE_MAX_LEAD:
        return "too_far"
    return "ok"


def facebook_page_slots(
    db: Session,
    account_id: str,
    ctseller_sale_id: int,
) -> list[dict[str, Any]]:
    rows = (
        db.query(EngagementSnapshot)
        .filter(
            EngagementSnapshot.account_id == account_id,
            EngagementSnapshot.ctseller_sale_id == int(ctseller_sale_id),
        )
        .all()
    )
    if not rows:
        return []
    latest = max(rows, key=lambda row: row.captured_at or datetime.min.replace(tzinfo=UTC))
    push_rows = []
    for row in rows:
        if row.bucket != "Push":
            continue
        push_rows.append(
            {
                "push_rank": row.push_rank,
                "sku": row.sku,
                "title": row.title,
                "price": row.price,
                "watchers": row.watchers,
                "item_url": row.item_url,
                "lot_id": row.lot_id,
                "photo_ids": [],
            }
        )
    posts = build_social_calendar(
        push_rows,
        sale_title=latest.sale_title or "",
        captured_at=latest.captured_at,
        sale_ends_at=latest.sale_ends_at,
    )
    out: list[dict[str, Any]] = []
    for post in posts:
        if post.get("platform") != FACEBOOK_PLATFORM:
            continue
        lot_id = str(post.get("lot_id") or "").strip()
        if not lot_id:
            continue
        when = slot_datetime(str(post["post_date"]), str(post["local_time"]), zone=str(post.get("zone") or "PT"))
        out.append(
            {
                **post,
                "when": when,
                "slot_key": calendar_slot_key(int(ctseller_sale_id), lot_id),
                "lot_id": lot_id,
                "ctseller_sale_id": int(ctseller_sale_id),
            }
        )
    return out


def annotate_social_calendar(
    db: Session,
    account_id: str,
    ctseller_sale_id: int,
    posts: list[Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    records = (
        db.query(FacebookPagePost)
        .filter(
            FacebookPagePost.account_id == account_id,
            FacebookPagePost.source == CALENDAR_SOURCE,
            FacebookPagePost.calendar_sale_id == int(ctseller_sale_id),
        )
        .order_by(FacebookPagePost.created_at.desc())
        .all()
    )
    by_key: dict[str, FacebookPagePost] = {}
    for row in records:
        key = str(getattr(row, "calendar_key", None) or "").strip()
        if key and key not in by_key:
            by_key[key] = row
    annotated: list[dict[str, Any]] = []
    for post in posts:
        data = post if isinstance(post, dict) else post.model_dump()
        platform = str(data.get("platform") or "")
        lot_id = str(data.get("lot_id") or "").strip() or None
        extra = {
            "slot_key": "",
            "facebook_schedulable": False,
            "facebook_status": "not_applicable",
            "facebook_slot_at": None,
            "facebook_post_id": None,
            "facebook_error": None,
        }
        if platform != FACEBOOK_PLATFORM:
            annotated.append({**data, **extra})
            continue
        if not lot_id:
            extra["facebook_status"] = "no_lot"
            annotated.append({**data, **extra})
            continue
        key = calendar_slot_key(int(ctseller_sale_id), lot_id)
        when = slot_datetime(str(data["post_date"]), str(data["local_time"]), zone=str(data.get("zone") or "PT"))
        window = schedule_window_state(when, now=now)
        row = by_key.get(key)
        status = "planned"
        if row and row.status in {"scheduled", "posted", "failed", "cancelled"}:
            status = row.status
        elif window == "missed":
            status = "missed"
        elif window == "too_soon":
            status = "too_soon"
        elif window == "too_far":
            status = "out_of_window"
        extra.update(
            {
                "slot_key": key,
                "facebook_schedulable": window == "ok" and (not row or row.status in {"failed", "cancelled"}),
                "facebook_status": status,
                "facebook_slot_at": when,
                "facebook_post_id": (row.facebook_post_id if row else None),
                "facebook_error": (row.error_message if row else None),
            }
        )
        if row and row.status == "scheduled":
            extra["facebook_schedulable"] = False
        annotated.append({**data, **extra})
    return annotated


def _connected_page(account: Account) -> tuple[str, str] | None:
    page_id = str(getattr(account, "facebook_page_id", None) or "").strip()
    token = unseal_facebook_secret(getattr(account, "facebook_page_token_enc", None))
    if not page_id or not token:
        return None
    return page_id, token


def _cancel_scheduled(
    db: Session,
    account: Account,
    row: FacebookPagePost,
    *,
    reason: str,
) -> None:
    creds = _connected_page(account)
    graph_id = str(row.facebook_post_id or "").strip()
    if creds and graph_id:
        try:
            delete_page_post(post_id=graph_id, page_token=creds[1])
        except MetaGraphError as exc:
            _LOG.warning("Could not delete scheduled Facebook post %s: %s", graph_id, exc)
            if exc.is_token_error:
                _mark_token_error(db, account, str(exc))
                return
    row.status = "cancelled"
    row.error_message = reason[:2000]
    db.add(row)
    db.commit()


def _reschedule_row(
    db: Session,
    account: Account,
    row: FacebookPagePost,
    when: datetime,
) -> None:
    creds = _connected_page(account)
    graph_id = str(row.facebook_post_id or "").strip()
    if creds and graph_id:
        try:
            update_scheduled_page_post(
                post_id=graph_id,
                page_token=creds[1],
                scheduled_unix=int(_as_utc(when).timestamp()),
            )
        except MetaGraphError as exc:
            _LOG.warning("Could not reschedule Facebook post %s: %s", graph_id, exc)
            if exc.is_token_error:
                _mark_token_error(db, account, str(exc))
                row.status = "failed"
                row.error_message = str(exc)[:2000]
                db.add(row)
                db.commit()
                return
            row.status = "failed"
            row.error_message = str(exc)[:2000]
            db.add(row)
            db.commit()
            return
    row.scheduled_publish_time = _as_utc(when)
    row.status = "scheduled"
    row.error_message = None
    _clear_token_error(db, account)
    db.add(row)
    db.commit()


def _draft_item(db: Session, account: Account, slot: dict[str, Any]) -> dict[str, Any] | None:
    lot_id = str(slot.get("lot_id") or "").strip()
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account.id).first()
    if not lot:
        return None
    if not _lot_photos(db, lot):
        return None
    snapshot = (
        db.query(EngagementSnapshot)
        .filter(EngagementSnapshot.account_id == account.id, EngagementSnapshot.lot_id == lot.id)
        .order_by(EngagementSnapshot.captured_at.desc())
        .first()
    )
    item_url = ctbids_lot_url(lot, snapshot=snapshot)
    close_at = snapshot.sale_ends_at if snapshot else None
    caption = draft_facebook_caption(
        title=lot.auction_title or slot.get("title") or "",
        description=lot.professional_description or "",
        sku=lot.sku or slot.get("sku") or "",
        item_url=item_url,
        close_at=close_at,
        include_follow_hook=False,
        current_bid=float(snapshot.price) if snapshot else None,
        watchers=int(snapshot.watchers) if snapshot else None,
        sale_title=(snapshot.sale_title if snapshot else None) or _sale_title(db, lot),
    )
    return {
        "lot_id": lot.id,
        "caption": caption,
        "scheduled_publish_time": slot["when"],
        "calendar_key": slot["slot_key"],
        "calendar_sale_id": int(slot["ctseller_sale_id"]),
    }


def sync_facebook_calendar_after_snapshot(
    db: Session,
    account_id: str,
    ctseller_sale_id: int,
    *,
    now: datetime | None = None,
) -> None:
    """Update/delete already-approved Graph posts; auto-approve new slots if enabled."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return
    slots = facebook_page_slots(db, account_id, ctseller_sale_id)
    by_key = {str(slot["slot_key"]): slot for slot in slots}
    scheduled_rows = (
        db.query(FacebookPagePost)
        .filter(
            FacebookPagePost.account_id == account_id,
            FacebookPagePost.source == CALENDAR_SOURCE,
            FacebookPagePost.calendar_sale_id == int(ctseller_sale_id),
            FacebookPagePost.status == "scheduled",
        )
        .all()
    )
    for row in scheduled_rows:
        key = str(getattr(row, "calendar_key", None) or "").strip()
        slot = by_key.get(key)
        if slot is None:
            _cancel_scheduled(
                db,
                account,
                row,
                reason="Lot left the Push calendar Facebook slot.",
            )
            continue
        window = schedule_window_state(slot["when"], now=now)
        old = row.scheduled_publish_time
        same_time = False
        if old is not None:
            same_time = abs((_as_utc(old) - _as_utc(slot["when"])).total_seconds()) < 60
        if window != "ok":
            if not same_time or window == "missed":
                _cancel_scheduled(
                    db,
                    account,
                    row,
                    reason="Push calendar slot is outside Facebook's 10-minute to 75-day window.",
                )
            continue
        if not same_time:
            _reschedule_row(db, account, row, slot["when"])

    if not bool(getattr(account, "facebook_calendar_auto_approve", False)):
        return
    if not _connected_page(account):
        return
    owner = (
        db.query(User)
        .filter(User.account_id == account_id, User.role == "owner")
        .order_by(User.created_at.asc())
        .first()
    )
    if owner is None:
        return
    pending: list[dict[str, Any]] = []
    for slot in slots:
        if schedule_window_state(slot["when"], now=now) != "ok":
            continue
        lot_id = str(slot.get("lot_id") or "")
        key = str(slot.get("slot_key") or "")
        existing_cal = (
            db.query(FacebookPagePost)
            .filter(
                FacebookPagePost.account_id == account_id,
                FacebookPagePost.calendar_key == key,
                FacebookPagePost.status.in_(("scheduled", "posted")),
            )
            .first()
        )
        if existing_cal:
            continue
        if _existing_posts(db, account_id, lot_id):
            continue
        item = _draft_item(db, account, slot)
        if item:
            pending.append(item)
    if not pending:
        return
    from app import facebook_post_tasks

    try:
        if len(pending) > facebook_post_tasks.SYNC_LOT_LIMIT:
            facebook_post_tasks.start_facebook_posts_in_background(
                account_id=account_id,
                user_id=owner.id,
                items=pending,
                include_follow_hook=False,
                confirm=True,
                force=False,
                source=CALENDAR_SOURCE,
            )
        else:
            publish_posts(
                db,
                account=account,
                user=owner,
                items=pending,
                include_follow_hook=False,
                confirm=True,
                force=False,
                source=CALENDAR_SOURCE,
                now=now,
            )
    except Exception:
        _LOG.exception(
            "Auto-approve Facebook calendar failed account=%s sale=%s",
            account_id,
            ctseller_sale_id,
        )


def calendar_publish_items(
    db: Session,
    *,
    account: Account,
    ctseller_sale_id: int,
    slot_keys: list[str],
    captions: dict[str, str] | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (publishable items, skipped result rows) for Push-calendar Facebook slots."""
    slots = facebook_page_slots(db, account.id, ctseller_sale_id)
    wanted = {key.strip() for key in slot_keys if key and key.strip()}
    chosen = [slot for slot in slots if not wanted or slot["slot_key"] in wanted]
    captions = captions or {}
    publishable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for slot in chosen:
        window = schedule_window_state(slot["when"], now=now)
        lot_id = str(slot["lot_id"])
        if window != "ok":
            skipped.append(
                {
                    "lot_id": lot_id,
                    "ok": False,
                    "skipped": True,
                    "error": "This calendar slot is outside Facebook's 10-minute to 75-day window.",
                }
            )
            continue
        drafted = _draft_item(db, account, slot)
        if not drafted:
            skipped.append(
                {
                    "lot_id": lot_id,
                    "ok": False,
                    "error": "Catalog the lot with listing photos before scheduling the Page post.",
                }
            )
            continue
        caption = (captions.get(lot_id) or drafted["caption"]).strip()
        drafted["caption"] = caption
        publishable.append(drafted)
    return publishable, skipped
