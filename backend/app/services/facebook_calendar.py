"""Map the Push-tab social calendar onto Facebook Page scheduled posts.

The engagement calendar is computed from the latest Watcher snapshot (not a
stored slot table). Push lots fill daily Pacific slots in push-rank order:

* 09:00 Facebook — collector find
* 12:15 Instagram — listing spotlight
* 16:30 FB+IG Stories — poll / evening urgency
* 18:00 TikTok — reel hook
* Closing-day 17:40 Stories countdown (top Push lot)

Each row keeps its spreadsheet **intended platform**. A separate
``PLATFORM_PUBLISH_TARGETS`` map chooses the **current publish target**.
Today every target is the Facebook Page feed; flipping Instagram or TikTok
to a native publisher later does not require new slot tables or keys.

Calendar identity is per lot + day + slot:
``pushcal:{sale}:{lot}:{YYYYMMDD}:{slot}`` (slot is ``0900`` / ``1215`` /
``1630`` / ``1800`` / ``1740``). A lot can appear on more than one day.
Legacy keys ``pushcal:{sale}:{lot}`` are the 09:00 Facebook row; undated
``pushcal:{sale}:{lot}:{slot}`` keys pick up the scheduled date on migrate.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Account, EngagementSnapshot, FacebookPagePost, Lot, User
from app.services.engagement import PACIFIC, build_social_calendar
from app.services.ctbids_urls import ctbids_sale_url
from app.services.facebook_caption import draft_facebook_caption, group_post_copy
from app.services.facebook_crypto import unseal_facebook_secret
from app.services.facebook_page import (
    SCHEDULE_MAX_LEAD,
    SCHEDULE_MIN_LEAD,
    _clear_token_error,
    _lot_photos,
    _mark_token_error,
    _sale_title,
    ctbids_lot_url,
    publish_posts,
    rotate_listing_photos,
)
from app.services.meta_graph import MetaGraphError, delete_page_post, update_scheduled_page_post

_LOG = logging.getLogger(__name__)

CALENDAR_SOURCE = "push_calendar"
FACEBOOK_PLATFORM = "Facebook"
PUBLISH_TARGET_FACEBOOK_PAGE = "facebook_page"
UTC = timezone.utc

# Intended spreadsheet platform -> publisher that actually runs today.
# Change a value here when a native Instagram/TikTok/Stories publisher exists.
PLATFORM_PUBLISH_TARGETS: dict[str, str] = {
    "Facebook": PUBLISH_TARGET_FACEBOOK_PAGE,
    "Instagram": PUBLISH_TARGET_FACEBOOK_PAGE,
    "FB + IG Stories": PUBLISH_TARGET_FACEBOOK_PAGE,
    "TikTok": PUBLISH_TARGET_FACEBOOK_PAGE,
}

SLOT_ANGLES: dict[str, str] = {
    "0900": "collector_find",
    "1215": "midday",
    "1630": "stories",
    "1800": "tiktok",
    "1740": "closing",
}

SLOT_PHOTO_OFFSETS: dict[str, int] = {
    "0900": 0,
    "1215": 1,
    "1630": 2,
    "1800": 3,
    "1740": 4,
}

PAGE_PREFLIGHT = (
    "Confirm the lot is still open, shipping is unchanged, and the current bid "
    "matches the listing before you approve. Approve schedules a Page post at the "
    "slot time. Post now publishes immediately if the slot is missed or under 10 minutes."
)

FACEBOOK_STATUS_LABELS: dict[str, str] = {
    "planned": "Planned",
    "missed": "Missed",
    "too_soon": "Too soon (under 10 min)",
    "out_of_window": "Too far (over 75 days)",
    "scheduled": "Scheduled",
    "posted": "Posted",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "no_lot": "No cataloged lot",
    "not_applicable": "Not posting to the Page",
}

FACEBOOK_BLOCKED_REASONS: dict[str, str] = {
    "missed": "Can't schedule: this slot time has already passed. Use Post now.",
    "too_soon": "Can't schedule: under 10 minutes. Facebook needs 10 minutes — use Post now.",
    "out_of_window": "Can't schedule: more than 75 days ahead. Use Post now to publish immediately.",
    "scheduled": "Already scheduled on the Page.",
    "posted": "Already posted to the Page.",
    "no_lot": "Catalog this lot before posting to the Page.",
    "not_applicable": "This slot is not posting to the Facebook Page.",
    "failed": "The last Page post for this slot failed.",
}


def facebook_status_label(status: str) -> str:
    key = (status or "").strip() or "planned"
    return FACEBOOK_STATUS_LABELS.get(key, key.replace("_", " ").title())


def facebook_blocked_reason(status: str, *, schedulable: bool) -> str | None:
    if schedulable:
        return None
    key = (status or "").strip()
    return FACEBOOK_BLOCKED_REASONS.get(key)


def current_publish_target(intended_platform: str) -> str:
    platform = (intended_platform or "").strip() or FACEBOOK_PLATFORM
    return PLATFORM_PUBLISH_TARGETS.get(platform, PUBLISH_TARGET_FACEBOOK_PAGE)


def slot_id_from_local_time(local_time: str) -> str:
    digits = "".join(ch for ch in (local_time or "") if ch.isdigit())
    if len(digits) >= 4:
        return digits[:4]
    if digits:
        return digits.zfill(4)[:4]
    return "0900"


def _key_date_token(post_date: str | date | datetime | None) -> str:
    if post_date is None:
        return ""
    if isinstance(post_date, datetime):
        moment = post_date if post_date.tzinfo else post_date.replace(tzinfo=UTC)
        return moment.astimezone(PACIFIC).date().strftime("%Y%m%d")
    if isinstance(post_date, date):
        return post_date.strftime("%Y%m%d")
    text = str(post_date).strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    try:
        return date.fromisoformat(text[:10]).strftime("%Y%m%d")
    except ValueError:
        return ""


def calendar_slot_key(
    ctseller_sale_id: int,
    lot_id: str,
    slot_id: str,
    post_date: str | date | datetime | None = None,
) -> str:
    sid = (slot_id or "0900").strip() or "0900"
    ymd = _key_date_token(post_date)
    if ymd:
        return f"pushcal:{int(ctseller_sale_id)}:{lot_id}:{ymd}:{sid}"
    return f"pushcal:{int(ctseller_sale_id)}:{lot_id}:{sid}"


def legacy_calendar_slot_key(ctseller_sale_id: int, lot_id: str) -> str:
    return f"pushcal:{int(ctseller_sale_id)}:{lot_id}"


def is_legacy_calendar_key(key: str) -> bool:
    parts = (key or "").split(":")
    return len(parts) == 3 and parts[0] == "pushcal"


def is_undated_slot_key(key: str) -> bool:
    parts = (key or "").split(":")
    return len(parts) == 4 and parts[0] == "pushcal" and len(parts[3]) == 4 and parts[3].isdigit()


def is_dated_slot_key(key: str) -> bool:
    parts = (key or "").split(":")
    return (
        len(parts) == 5
        and parts[0] == "pushcal"
        and len(parts[3]) == 8
        and parts[3].isdigit()
        and len(parts[4]) == 4
        and parts[4].isdigit()
    )


def migrate_calendar_key(
    key: str | None,
    *,
    when: str | date | datetime | None = None,
) -> str:
    raw = (key or "").strip()
    if not raw:
        return ""
    ymd = _key_date_token(when)
    if is_dated_slot_key(raw):
        return raw
    if is_legacy_calendar_key(raw):
        undated = f"{raw}:0900"
        if ymd:
            return f"{raw}:{ymd}:0900"
        return undated
    if is_undated_slot_key(raw) and ymd:
        sale, lot, slot = raw.split(":")[1], raw.split(":")[2], raw.split(":")[3]
        return f"pushcal:{sale}:{lot}:{ymd}:{slot}"
    return raw


def calendar_key_aliases(key: str | None) -> list[str]:
    """Dated key plus undated slot and pre-slot 09:00 forms, for safe lookup."""
    raw = (key or "").strip()
    if not raw:
        return []
    migrated = migrate_calendar_key(raw)
    out: list[str] = []

    def _add(value: str) -> None:
        if value and value not in out:
            out.append(value)

    _add(migrated)
    _add(raw)
    parts = migrated.split(":")
    if is_dated_slot_key(migrated):
        sale, lot, _ymd, slot = parts[1], parts[2], parts[3], parts[4]
        _add(f"pushcal:{sale}:{lot}:{slot}")
        if slot == "0900":
            _add(f"pushcal:{sale}:{lot}")
    elif is_undated_slot_key(migrated) and migrated.endswith(":0900"):
        _add(migrated[: -len(":0900")])
    return out


def calendar_keys_match(slot_key: str, wanted: set[str]) -> bool:
    if not wanted:
        return True
    return bool(wanted.intersection(calendar_key_aliases(slot_key)))


def slot_angle_for(local_time: str, *, hook: str = "") -> str:
    slot_id = slot_id_from_local_time(local_time)
    if slot_id == "1740" or "closing countdown" in (hook or "").lower():
        if slot_id == "1740":
            return "closing"
    return SLOT_ANGLES.get(slot_id, "collector_find")


def photo_offset_for(slot_id: str) -> int:
    return SLOT_PHOTO_OFFSETS.get(slot_id, 0)


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


def _slot_dict(
    post: dict[str, Any],
    *,
    ctseller_sale_id: int,
) -> dict[str, Any] | None:
    lot_id = str(post.get("lot_id") or "").strip()
    if not lot_id:
        return None
    intended = str(post.get("platform") or "").strip() or FACEBOOK_PLATFORM
    slot_id = slot_id_from_local_time(str(post.get("local_time") or "09:00"))
    when = slot_datetime(str(post["post_date"]), str(post["local_time"]), zone=str(post.get("zone") or "PT"))
    return {
        **post,
        "when": when,
        "slot_id": slot_id,
        "slot_key": calendar_slot_key(int(ctseller_sale_id), lot_id, slot_id, post.get("post_date")),
        "lot_id": lot_id,
        "ctseller_sale_id": int(ctseller_sale_id),
        "intended_platform": intended,
        "publish_target": current_publish_target(intended),
        "slot_angle": slot_angle_for(str(post.get("local_time") or ""), hook=str(post.get("hook") or "")),
    }


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
    from app.services.engagement import display_bucket

    latest = max(rows, key=lambda row: row.captured_at or datetime.min.replace(tzinfo=UTC))
    push_rows = []
    needs_eyes_rows = []
    for row in rows:
        bucket = display_bucket(row.bucket or "", watchers=row.watchers, bids=row.bids)
        payload = {
            "push_rank": row.push_rank,
            "sku": row.sku,
            "title": row.title,
            "price": row.price,
            "watchers": row.watchers,
            "item_url": row.item_url,
            "lot_id": row.lot_id,
            "photo_ids": [],
            "bucket": bucket,
        }
        if bucket == "Push":
            push_rows.append(payload)
        elif bucket == "Needs eyes":
            needs_eyes_rows.append(payload)
    posts = build_social_calendar(
        push_rows,
        needs_eyes_rows=needs_eyes_rows,
        sale_title=latest.sale_title or "",
        captured_at=latest.captured_at,
        sale_ends_at=latest.sale_ends_at,
        auction_url=latest.auction_url or "",
        ctseller_sale_id=int(ctseller_sale_id),
    )
    out: list[dict[str, Any]] = []
    for post in posts:
        slot = _slot_dict(post, ctseller_sale_id=int(ctseller_sale_id))
        if not slot:
            continue
        if slot["publish_target"] != PUBLISH_TARGET_FACEBOOK_PAGE:
            continue
        out.append(slot)
    return out


def _index_calendar_rows(records: list[FacebookPagePost]) -> dict[str, FacebookPagePost]:
    by_key: dict[str, FacebookPagePost] = {}
    for row in records:
        key = str(getattr(row, "calendar_key", None) or "").strip()
        if not key:
            continue
        for alias in calendar_key_aliases(key):
            if alias not in by_key:
                by_key[alias] = row
    return by_key


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
    by_key = _index_calendar_rows(records)
    annotated: list[dict[str, Any]] = []
    for post in posts:
        data = post if isinstance(post, dict) else post.model_dump()
        intended = str(data.get("platform") or "").strip() or FACEBOOK_PLATFORM
        target = current_publish_target(intended)
        lot_id = str(data.get("lot_id") or "").strip() or None
        extra = {
            "slot_key": "",
            "intended_platform": intended,
            "publish_target": target,
            "facebook_schedulable": False,
            "facebook_can_post_now": False,
            "facebook_can_cancel": False,
            "facebook_status": "not_applicable",
            "facebook_status_label": facebook_status_label("not_applicable"),
            "facebook_blocked_reason": facebook_blocked_reason("not_applicable", schedulable=False),
            "facebook_slot_at": None,
            "facebook_post_id": None,
            "facebook_error": None,
        }
        if target != PUBLISH_TARGET_FACEBOOK_PAGE:
            annotated.append({**data, **extra})
            continue
        if not lot_id:
            extra["facebook_status"] = "no_lot"
            extra["facebook_status_label"] = facebook_status_label("no_lot")
            extra["facebook_blocked_reason"] = facebook_blocked_reason("no_lot", schedulable=False)
            annotated.append({**data, **extra, "preflight": PAGE_PREFLIGHT})
            continue
        slot_id = slot_id_from_local_time(str(data.get("local_time") or "09:00"))
        key = calendar_slot_key(int(ctseller_sale_id), lot_id, slot_id, data.get("post_date"))
        when = slot_datetime(str(data["post_date"]), str(data["local_time"]), zone=str(data.get("zone") or "PT"))
        window = schedule_window_state(when, now=now)
        row = by_key.get(key)
        if row is None:
            for alias in calendar_key_aliases(key):
                row = by_key.get(alias)
                if row is not None:
                    break
        status = "planned"
        if row and row.status in {"scheduled", "posted", "failed", "cancelled"}:
            status = row.status
        elif window == "missed":
            status = "missed"
        elif window == "too_soon":
            status = "too_soon"
        elif window == "too_far":
            status = "out_of_window"
        schedulable = window == "ok" and (not row or row.status in {"failed", "cancelled"})
        if row and row.status == "scheduled":
            schedulable = False
        can_post_now = not row or row.status in {"failed", "cancelled"}
        extra.update(
            {
                "slot_key": key,
                "facebook_schedulable": schedulable,
                "facebook_can_post_now": can_post_now,
                "facebook_can_cancel": bool(row and row.status == "scheduled"),
                "facebook_status": status,
                "facebook_status_label": facebook_status_label(status),
                "facebook_blocked_reason": facebook_blocked_reason(status, schedulable=schedulable),
                "facebook_slot_at": when,
                "facebook_post_id": (row.facebook_post_id if row else None),
                "facebook_error": (row.error_message if row else None),
                "preflight": PAGE_PREFLIGHT,
            }
        )
        group_copy = str(data.get("group_copy") or "")
        if group_copy and lot_id:
            lot = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
            if lot is not None:
                from app.services.click_tracking import CHANNEL_GROUP_SHARE, rewrite_copy_with_tracked_links

                data["group_copy"] = rewrite_copy_with_tracked_links(
                    db,
                    account_id=account_id,
                    lot=lot,
                    text=group_copy,
                    channel=CHANNEL_GROUP_SHARE,
                    calendar_key=key,
                    append_if_missing=True,
                )
        annotated.append({**data, **extra})
    if posts:
        db.commit()
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


def _photos_for_slot(db: Session, lot: Lot, slot_id: str) -> list:
    photos = _lot_photos(db, lot)
    return rotate_listing_photos(photos, photo_offset_for(slot_id))


def _draft_item(
    db: Session,
    account: Account,
    slot: dict[str, Any],
    *,
    include_follow_hook: bool = False,
) -> dict[str, Any] | None:
    lot_id = str(slot.get("lot_id") or "").strip()
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account.id).first()
    if not lot:
        return None
    slot_id = str(slot.get("slot_id") or slot_id_from_local_time(str(slot.get("local_time") or "")))
    photos = _photos_for_slot(db, lot, slot_id)
    if not photos:
        return None
    snapshot = (
        db.query(EngagementSnapshot)
        .filter(EngagementSnapshot.account_id == account.id, EngagementSnapshot.lot_id == lot.id)
        .order_by(EngagementSnapshot.captured_at.desc())
        .first()
    )
    dest_item = ctbids_lot_url(lot, snapshot=snapshot)
    dest_sale = ctbids_sale_url(
        sale_id=int(slot.get("ctseller_sale_id") or getattr(lot, "ctseller_sale_id", 0) or 0) or None,
        auction_url=(snapshot.auction_url if snapshot else "") or "",
        item_url=dest_item,
    )
    from app.services.click_tracking import (
        CHANNEL_GROUP_SHARE,
        CHANNEL_PAGE_POST,
        apply_tracked_urls,
        tracked_pair,
    )

    calendar_key = str(slot.get("slot_key") or "")
    _di, _ds, item_url, sale_url = tracked_pair(
        db,
        account_id=account.id,
        lot=lot,
        channel=CHANNEL_PAGE_POST,
        calendar_key=calendar_key,
        item_url=dest_item,
        sale_url=dest_sale,
    )
    _gdi, _gds, group_item_url, group_sale_url = tracked_pair(
        db,
        account_id=account.id,
        lot=lot,
        channel=CHANNEL_GROUP_SHARE,
        calendar_key=calendar_key,
        item_url=dest_item,
        sale_url=dest_sale,
    )
    close_at = snapshot.sale_ends_at if snapshot else None
    angle = str(slot.get("slot_angle") or slot_angle_for(str(slot.get("local_time") or ""), hook=str(slot.get("hook") or "")))
    caption = draft_facebook_caption(
        title=lot.auction_title or slot.get("title") or "",
        description=lot.professional_description or "",
        sku=lot.sku or slot.get("sku") or "",
        item_url=item_url,
        close_at=close_at,
        include_follow_hook=include_follow_hook,
        current_bid=float(snapshot.price) if snapshot else None,
        watchers=int(snapshot.watchers) if snapshot else None,
        sale_title=(snapshot.sale_title if snapshot else None) or _sale_title(db, lot),
        angle=angle,
        sale_url=sale_url,
    )
    caption = apply_tracked_urls(
        caption,
        dest_item=dest_item,
        dest_sale=dest_sale,
        short_item=item_url,
        short_sale=sale_url,
    )
    group_caption = group_post_copy(
        title=lot.auction_title or slot.get("title") or "",
        hook=str(slot.get("hook") or ""),
        description=lot.professional_description or "",
        caption=caption,
        item_url=group_item_url,
        close_at=close_at,
        sale_url=group_sale_url,
    )
    group_caption = apply_tracked_urls(
        group_caption,
        dest_item=dest_item,
        dest_sale=dest_sale,
        short_item=group_item_url,
        short_sale=group_sale_url,
    )
    return {
        "lot_id": lot.id,
        "sku": lot.sku,
        "title": lot.auction_title or slot.get("title") or "",
        "item_url": item_url,
        "sale_ends_at": close_at,
        "caption": caption,
        "group_caption": group_caption,
        "photo_ids": [p.id for p in photos],
        "photo_count": len(photos),
        "scheduled_publish_time": slot["when"],
        "calendar_key": slot["slot_key"],
        "calendar_sale_id": int(slot["ctseller_sale_id"]),
        "intended_platform": slot.get("intended_platform") or slot.get("platform") or "",
        "publish_target": slot.get("publish_target") or PUBLISH_TARGET_FACEBOOK_PAGE,
        "local_time": slot.get("local_time") or "",
        "hook": slot.get("hook") or "",
    }


def _record_for_slot(by_key: dict[str, FacebookPagePost], slot: dict[str, Any]) -> FacebookPagePost | None:
    key = str(slot.get("slot_key") or "")
    for alias in calendar_key_aliases(key):
        row = by_key.get(alias)
        if row is not None:
            return row
    return None


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
    by_key: dict[str, dict[str, Any]] = {}
    for slot in slots:
        key = str(slot["slot_key"])
        by_key[key] = slot
        for alias in calendar_key_aliases(key):
            by_key.setdefault(alias, slot)
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
        migrated = migrate_calendar_key(key, when=row.scheduled_publish_time or row.posted_at)
        slot = by_key.get(migrated) or by_key.get(key)
        if slot is None:
            for alias in calendar_key_aliases(key) + calendar_key_aliases(migrated):
                slot = by_key.get(alias)
                if slot is not None:
                    migrated = str(slot["slot_key"])
                    break
        if slot is None:
            _cancel_scheduled(
                db,
                account,
                row,
                reason="Lot left this Push calendar slot.",
            )
            continue
        dated = str(slot["slot_key"])
        if key and key != dated:
            row.calendar_key = dated
            db.add(row)
            db.commit()
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
        key = str(slot.get("slot_key") or "")
        existing_cal = (
            db.query(FacebookPagePost)
            .filter(
                FacebookPagePost.account_id == account_id,
                FacebookPagePost.calendar_key.in_(calendar_key_aliases(key)),
                FacebookPagePost.status.in_(("scheduled", "posted")),
            )
            .first()
        )
        if existing_cal:
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
    include_follow_hook: bool = False,
    post_now: bool = False,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (publishable items, skipped result rows) for Push-calendar Page slots."""
    slots = facebook_page_slots(db, account.id, ctseller_sale_id)
    wanted_raw = {key.strip() for key in slot_keys if key and key.strip()}
    wanted: set[str] = set()
    for key in wanted_raw:
        wanted.update(calendar_key_aliases(key))
        wanted.add(migrate_calendar_key(key))
    chosen = [slot for slot in slots if not wanted_raw or calendar_keys_match(str(slot["slot_key"]), wanted)]
    captions = captions or {}
    publishable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for slot in chosen:
        window = schedule_window_state(slot["when"], now=now)
        lot_id = str(slot["lot_id"])
        key = str(slot["slot_key"])
        if not post_now and window != "ok":
            skipped.append(
                {
                    "lot_id": lot_id,
                    "calendar_key": key,
                    "ok": False,
                    "skipped": True,
                    "error": "This calendar slot is outside Facebook's 10-minute to 75-day window. Use Post now to publish immediately.",
                }
            )
            continue
        drafted = _draft_item(db, account, slot, include_follow_hook=include_follow_hook)
        if not drafted:
            skipped.append(
                {
                    "lot_id": lot_id,
                    "calendar_key": key,
                    "ok": False,
                    "error": "Catalog the lot with listing photos before scheduling the Page post.",
                }
            )
            continue
        caption = (
            captions.get(key)
            or captions.get(migrate_calendar_key(key))
            or captions.get(lot_id)
            or drafted["caption"]
        ).strip()
        drafted["caption"] = caption
        if post_now:
            drafted["scheduled_publish_time"] = None
        publishable.append(drafted)
    return publishable, skipped


def cancel_calendar_slots(
    db: Session,
    *,
    account: Account,
    ctseller_sale_id: int,
    slot_keys: list[str],
) -> list[dict[str, Any]]:
    """Delete unpublished Graph posts and mark those calendar slots cancelled."""
    wanted_raw = [key.strip() for key in slot_keys if key and str(key).strip()]
    if not wanted_raw:
        raise HTTPException(status_code=400, detail="Pick at least one calendar slot to unapprove.")
    records = (
        db.query(FacebookPagePost)
        .filter(
            FacebookPagePost.account_id == account.id,
            FacebookPagePost.source == CALENDAR_SOURCE,
            FacebookPagePost.calendar_sale_id == int(ctseller_sale_id),
        )
        .order_by(FacebookPagePost.created_at.desc())
        .all()
    )
    by_key = _index_calendar_rows(records)
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in wanted_raw:
        key = migrate_calendar_key(raw)
        if key in seen:
            continue
        seen.add(key)
        row = None
        for alias in calendar_key_aliases(key):
            row = by_key.get(alias)
            if row is not None:
                break
        lot_id = str(row.lot_id) if row and row.lot_id else ""
        if row is None or row.status != "scheduled":
            results.append(
                {
                    "lot_id": lot_id,
                    "calendar_key": key,
                    "ok": False,
                    "skipped": True,
                    "error": "No scheduled Page post for this slot.",
                }
            )
            continue
        _cancel_scheduled(
            db,
            account,
            row,
            reason="Owner unapproved this Push calendar slot.",
        )
        db.refresh(row)
        if row.status != "cancelled":
            results.append(
                {
                    "lot_id": lot_id,
                    "calendar_key": key,
                    "ok": False,
                    "needs_reconnect": bool(getattr(account, "facebook_token_error", None)),
                    "error": row.error_message or "Could not remove the scheduled Page post.",
                }
            )
            continue
        results.append(
            {
                "lot_id": lot_id,
                "calendar_key": key,
                "ok": True,
                "status": "cancelled",
                "facebook_post_id": row.facebook_post_id,
            }
        )
    return results


def calendar_preview(
    db: Session,
    *,
    account: Account,
    ctseller_sale_id: int,
    slot_keys: list[str],
    include_follow_hook: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    from app.services.facebook_page import FOLLOW_HOOK, connection_status, require_meta_configured

    require_meta_configured()
    status = connection_status(account)
    if not status["connected"]:
        raise HTTPException(
            status_code=400,
            detail="Connect a Facebook Page on Account settings before drafting a post.",
        )
    slots = facebook_page_slots(db, account.id, ctseller_sale_id)
    wanted_raw = {key.strip() for key in slot_keys if key and key.strip()}
    wanted: set[str] = set()
    for key in wanted_raw:
        wanted.update(calendar_key_aliases(key))
        wanted.add(migrate_calendar_key(key))
    chosen = [slot for slot in slots if not wanted_raw or calendar_keys_match(str(slot["slot_key"]), wanted)]
    records = (
        db.query(FacebookPagePost)
        .filter(
            FacebookPagePost.account_id == account.id,
            FacebookPagePost.source == CALENDAR_SOURCE,
            FacebookPagePost.calendar_sale_id == int(ctseller_sale_id),
        )
        .order_by(FacebookPagePost.created_at.desc())
        .all()
    )
    by_key = _index_calendar_rows(records)
    drafts: list[dict[str, Any]] = []
    for slot in chosen:
        key = str(slot["slot_key"])
        row = _record_for_slot(by_key, slot)
        drafted = _draft_item(db, account, slot, include_follow_hook=include_follow_hook)
        if not drafted:
            drafts.append(
                {
                    "lot_id": slot["lot_id"],
                    "sku": slot.get("sku") or "",
                    "title": slot.get("title") or "",
                    "item_url": slot.get("item_url") or "",
                    "sale_ends_at": None,
                    "caption": "",
                    "photo_ids": [],
                    "photo_count": 0,
                    "already_posted": bool(row and row.status in {"posted", "scheduled"}),
                    "existing_posts": [],
                    "error": "Catalog the lot with listing photos before scheduling the Page post.",
                    "calendar_key": key,
                    "intended_platform": slot.get("intended_platform") or "",
                    "publish_target": slot.get("publish_target") or PUBLISH_TARGET_FACEBOOK_PAGE,
                    "local_time": slot.get("local_time") or "",
                    "hook": slot.get("hook") or "",
                }
            )
            continue
        existing = []
        if row:
            existing = [
                {
                    "id": row.id,
                    "status": row.status,
                    "facebook_post_id": row.facebook_post_id,
                    "scheduled_publish_time": row.scheduled_publish_time,
                    "posted_at": row.posted_at,
                    "created_at": row.created_at,
                }
            ]
        drafts.append(
            {
                **drafted,
                "already_posted": bool(row and row.status in {"posted", "scheduled"}),
                "existing_posts": existing,
                "error": None,
            }
        )
    db.commit()
    return {
        **status,
        "include_follow_hook": include_follow_hook,
        "follow_hook_line": FOLLOW_HOOK,
        "schedule_suggestions": [],
        "lots": drafts,
    }


def rewrite_legacy_calendar_keys(conn) -> int:
    """Rewrite undated Push-calendar keys to include Pacific YYYYMMDD. Returns update count."""
    from sqlalchemy import text

    try:
        rows = conn.execute(
            text(
                "SELECT id, calendar_key, scheduled_publish_time, posted_at, created_at "
                "FROM facebook_page_posts "
                "WHERE calendar_key IS NOT NULL AND calendar_key LIKE 'pushcal:%'"
            )
        ).fetchall()
    except Exception:
        return 0
    updated = 0
    for row in rows:
        row_id = row[0]
        raw = str(row[1] or "").strip()
        when = row[2] or row[3] or row[4]
        migrated = migrate_calendar_key(raw, when=when)
        if not migrated or migrated == raw:
            continue
        conn.execute(
            text("UPDATE facebook_page_posts SET calendar_key = :new WHERE id = :id"),
            {"new": migrated, "id": row_id},
        )
        updated += 1
    return updated
