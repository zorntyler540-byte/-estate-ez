"""Map the Push-tab social calendar onto Facebook Page scheduled posts.

The engagement calendar is computed from the latest Watcher snapshot (not a
stored slot table). Push lots fill daily Pacific slots in push-rank order:

* 09:00 Facebook — collector find
* 12:15 Instagram — listing spotlight
* 16:30 FB+IG Stories — poll / evening urgency
* 18:00 TikTok — reel hook
* Closing-day 17:40 Stories countdown (top Push lot)

Each row keeps its spreadsheet **intended platform**. ``PLATFORM_PUBLISH_TARGETS``
chooses the **publish target**: Facebook -> Page feed (Facebook schedules it),
Instagram -> Instagram feed (copy-only until an IG account is linked and
``META_INSTAGRAM_PUBLISHING`` is on), FB + IG Stories -> Page story (+ IG story),
TikTok -> copy-only. Instagram and Stories cannot be scheduled through Graph: approving
one pins the slot as ``scheduled`` and the publish queue posts it at slot time.
A pinned row keeps the target it was approved with.

Calendar identity is per lot + day + slot:
``pushcal:{sale}:{lot}:{YYYYMMDD}:{slot}`` (slot is ``0900`` / ``1215`` /
``1630`` / ``1800`` / ``1740``). A lot can appear on more than one day.
Legacy keys ``pushcal:{sale}:{lot}`` are the 09:00 Facebook row; undated
``pushcal:{sale}:{lot}:{slot}`` keys pick up the date of their own scheduled /
posted time (never the date of whatever calendar is current).

Safety rules:

* A slot row that is ``scheduled``, ``publishing`` or ``posted`` is **pinned**: the
  rebuilt calendar keeps that lot on that day and time, and the snapshot sync never
  deletes, moves, or edits the Graph post. Only unposted slots are regenerated.
* Publishing claims the slot row (``publishing``) before any Graph call; the unique
  ``(account_id, calendar_key)`` index makes a second publisher skip it.
* Auto-approve never re-posts a slot the owner cancelled, retries a failed slot at
  most ``AUTO_APPROVE_MAX_ATTEMPTS`` times with backoff, and skips accounts whose
  Page token is dead.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Account, EngagementSnapshot, FacebookPagePost, Lot, User
from app.services.engagement import build_social_calendar
from app.services.franchise_time import DEFAULT_TIMEZONE, account_zone_name, zone_for
from app.services.ctbids_urls import ctbids_sale_url
from app.services.facebook_caption import draft_captions, draft_facebook_caption, group_post_copy
from app.services.facebook_crypto import unseal_facebook_secret
from app.services.facebook_page import (
    DEFERRED_MAX_LATE,
    DEFERRED_MISSED_NOTE,
    SCHEDULE_MAX_LEAD,
    SCHEDULE_MIN_LEAD,
    _clear_token_error,
    _lot_photos,
    _mark_token_error,
    _sale_title,
    ctbids_lot_url,
    rotate_listing_photos,
)
from app.services.meta_graph import MetaGraphError, delete_page_post
from app.services.social_targets import (
    DEFERRED_TARGETS,
    PUBLISH_TARGET_COPY_ONLY,
    PUBLISH_TARGET_FACEBOOK_PAGE,
    PUBLISH_TARGET_INSTAGRAM_FEED,
    PUBLISH_TARGET_STORIES,
    PUBLISHABLE_TARGETS,
    instagram_user_id,
)

_LOG = logging.getLogger(__name__)

CALENDAR_SOURCE = "push_calendar"
FACEBOOK_PLATFORM = "Facebook"
UTC = timezone.utc

# Rows in these states own their slot; rebuilds and auto-approve leave them alone.
PINNED_STATUSES = frozenset({"scheduled", "publishing", "posted"})
AUTO_APPROVE_MAX_ATTEMPTS = 3
AUTO_RETRY_BASE = timedelta(minutes=10)
AUTO_RETRY_MAX = timedelta(hours=6)
# A ``publishing`` claim older than this crashed mid-Graph-call; the outcome is unknown.
PUBLISH_CLAIM_STALE = timedelta(minutes=15)
# Owner Unapprove refuses once the post is this close to going live.
CANCEL_MIN_LEAD = timedelta(minutes=2)

LOT_LEFT_SALE_NOTE = (
    "This lot is no longer in the latest Watcher snapshot. The scheduled post is still on "
    "Facebook — Unapprove it if the lot ended."
)
INTERRUPTED_NOTE = (
    "Publishing was interrupted before Facebook answered. Check the Page before posting this "
    "slot again — it may already be live."
)

# Intended spreadsheet platform -> publisher. Nothing falls back to the Page feed.
PLATFORM_PUBLISH_TARGETS: dict[str, str] = {
    "Facebook": PUBLISH_TARGET_FACEBOOK_PAGE,
    "Instagram": PUBLISH_TARGET_INSTAGRAM_FEED,
    "FB + IG Stories": PUBLISH_TARGET_STORIES,
    "TikTok": PUBLISH_TARGET_COPY_ONLY,
}
COPY_ONLY_REASONS: dict[str, str] = {
    "Instagram": (
        "Copy only: link an Instagram professional account to the Facebook Page to publish "
        "Instagram slots."
    ),
    "TikTok": "TikTok is copy-only: paste the caption into the TikTok app.",
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
    "publishing": "Publishing…",
    "posted": "Posted",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "no_lot": "No cataloged lot",
    "not_applicable": "Not posting to the Page",
    "copy_only": "Copy only",
}

FACEBOOK_BLOCKED_REASONS: dict[str, str] = {
    "missed": "Can't schedule: this slot time has already passed. Use Post now.",
    "too_soon": "Can't schedule: under 10 minutes. Facebook needs 10 minutes — use Post now.",
    "out_of_window": "Can't schedule: more than 75 days ahead. Use Post now to publish immediately.",
    "scheduled": "Already scheduled on the Page.",
    "publishing": "Being sent to the Page right now.",
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


def current_publish_target(intended_platform: str, *, instagram: bool = False) -> str:
    """Publisher for a new approval. Instagram slots are copy-only until Instagram is linked."""
    platform = (intended_platform or "").strip() or FACEBOOK_PLATFORM
    target = PLATFORM_PUBLISH_TARGETS.get(platform, PUBLISH_TARGET_COPY_ONLY)
    if target == PUBLISH_TARGET_INSTAGRAM_FEED and not instagram:
        return PUBLISH_TARGET_COPY_ONLY
    return target


def _instagram_ready(db: Session, account_id: str) -> bool:
    account = db.get(Account, account_id)
    return account is not None and instagram_user_id(account) is not None


def row_target(row: FacebookPagePost | None) -> str:
    return str(getattr(row, "publish_target", None) or "") or PUBLISH_TARGET_FACEBOOK_PAGE


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
        # Only legacy undated keys reach here; those were written on Pacific days.
        moment = post_date if post_date.tzinfo else post_date.replace(tzinfo=UTC)
        return moment.astimezone(zone_for(DEFAULT_TIMEZONE)).date().strftime("%Y%m%d")
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
    """Exact dated-key match. An empty selection matches nothing (never "all slots")."""
    if not wanted:
        return False
    return migrate_calendar_key(slot_key) in wanted


def _as_aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def effective_calendar_key(row: FacebookPagePost) -> str:
    """The dated slot key this row owns. Undated legacy keys take the row's own time."""
    raw = str(getattr(row, "calendar_key", None) or "").strip()
    if not raw:
        return ""
    when = (
        _as_aware(getattr(row, "scheduled_publish_time", None))
        or _as_aware(getattr(row, "posted_at", None))
        or _as_aware(getattr(row, "created_at", None))
    )
    return migrate_calendar_key(raw, when=when)


def calendar_row_for_key(db: Session, account_id: str, key: str | None) -> FacebookPagePost | None:
    """The row that owns this dated slot (legacy undated rows only by their own date)."""
    wanted = migrate_calendar_key(key)
    if not wanted:
        return None
    candidates = (
        db.query(FacebookPagePost)
        .filter(
            FacebookPagePost.account_id == account_id,
            FacebookPagePost.calendar_key.in_(calendar_key_aliases(wanted)),
        )
        .order_by(FacebookPagePost.created_at.desc())
        .all()
    )
    return _index_calendar_rows(candidates).get(wanted)


def dated_slot_parts(key: str) -> tuple[str, str] | None:
    """``(YYYY-MM-DD, HH:MM)`` for a dated slot key, else None."""
    if not is_dated_slot_key(key):
        return None
    parts = key.split(":")
    ymd, slot = parts[3], parts[4]
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}", f"{slot[:2]}:{slot[2:]}"


def requested_slot_keys(slot_keys: list[str] | None) -> set[str]:
    """Normalize a request's slot keys. Raises 400 when nothing was picked."""
    wanted = {migrate_calendar_key(key.strip()) for key in (slot_keys or []) if key and key.strip()}
    wanted.discard("")
    if not wanted:
        raise HTTPException(status_code=400, detail="Pick at least one calendar slot.")
    return wanted


def auto_retry_backoff(attempts: int) -> timedelta:
    step = AUTO_RETRY_BASE * (2 ** max(0, int(attempts) - 1))
    return min(step, AUTO_RETRY_MAX)


def auto_retry_allowed(row: FacebookPagePost | None, *, now: datetime) -> bool:
    """Auto-approve may (re)try this slot. Owner-cancelled and pinned slots: never."""
    if row is None:
        return True
    if row.status != "failed":
        return False
    if int(row.attempt_count or 0) >= AUTO_APPROVE_MAX_ATTEMPTS:
        return False
    due = _as_aware(row.next_attempt_at)
    return due is None or due <= _as_utc(now)


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
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone_for(zone))


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
    instagram: bool = False,
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
        "publish_target": current_publish_target(intended, instagram=instagram),
        "slot_angle": slot_angle_for(str(post.get("local_time") or ""), hook=str(post.get("hook") or "")),
    }


def calendar_rows(db: Session, account_id: str, ctseller_sale_id: int) -> list[FacebookPagePost]:
    return (
        db.query(FacebookPagePost)
        .filter(
            FacebookPagePost.account_id == account_id,
            FacebookPagePost.source == CALENDAR_SOURCE,
            FacebookPagePost.calendar_sale_id == int(ctseller_sale_id),
        )
        .order_by(FacebookPagePost.created_at.desc())
        .all()
    )


def _index_calendar_rows(records: list[FacebookPagePost]) -> dict[str, FacebookPagePost]:
    """Effective dated key -> row. A pinned row wins over a failed/cancelled one."""
    by_key: dict[str, FacebookPagePost] = {}
    for row in records:
        key = effective_calendar_key(row)
        if not key:
            continue
        current = by_key.get(key)
        if current is None or (current.status not in PINNED_STATUSES and row.status in PINNED_STATUSES):
            by_key[key] = row
    return by_key


def snapshot_payload(row: EngagementSnapshot) -> dict[str, Any]:
    from app.services.engagement import display_bucket

    return {
        "push_rank": row.push_rank,
        "sku": row.sku,
        "title": row.title,
        "price": row.price,
        "watchers": row.watchers,
        "item_url": row.item_url,
        "lot_id": row.lot_id,
        "photo_ids": [],
        "bucket": display_bucket(row.bucket or "", watchers=row.watchers, bids=row.bids),
    }


def calendar_pins(
    db: Session,
    account_id: str,
    records: list[FacebookPagePost],
    payload_by_lot: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pinned slots for ``build_social_calendar``: approved/posted rows keep day + time + lot."""
    pins: list[dict[str, Any]] = []
    missing: set[str] = set()
    moment = datetime.now(UTC)
    for row in records:
        if row.status not in PINNED_STATUSES or not row.lot_id:
            continue
        parts = dated_slot_parts(effective_calendar_key(row))
        if parts is None:
            continue
        if str(row.lot_id) not in payload_by_lot:
            missing.add(str(row.lot_id))
        pins.append(
            {
                "post_date": parts[0],
                "local_time": parts[1],
                "lot_id": str(row.lot_id),
                "upcoming": row.status != "posted" and not _row_live(row, now=moment),
            }
        )
    stubs: dict[str, dict[str, Any]] = {}
    if missing:
        for lot in db.query(Lot).filter(Lot.account_id == account_id, Lot.id.in_(sorted(missing))).all():
            stubs[lot.id] = {
                "push_rank": None,
                "sku": lot.sku or "",
                "title": lot.auction_title or lot.sku or "Lot",
                "price": 0,
                "watchers": 0,
                "item_url": ctbids_lot_url(lot),
                "lot_id": lot.id,
                "photo_ids": [],
                "bucket": "",
            }
    out: list[dict[str, Any]] = []
    for pin in pins:
        row = payload_by_lot.get(pin["lot_id"]) or stubs.get(pin["lot_id"])
        if row is None:
            continue
        out.append({**pin, "row": row})
    return out


def facebook_page_slots(
    db: Session,
    account_id: str,
    ctseller_sale_id: int,
    *,
    records: list[FacebookPagePost] | None = None,
    tz: str | None = None,
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
    needs_eyes_rows = []
    payload_by_lot: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = snapshot_payload(row)
        if row.lot_id:
            payload_by_lot.setdefault(str(row.lot_id), payload)
        if payload["bucket"] == "Push":
            push_rows.append(payload)
        elif payload["bucket"] == "Needs eyes":
            needs_eyes_rows.append(payload)
    if records is None:
        records = calendar_rows(db, account_id, ctseller_sale_id)
    posts = build_social_calendar(
        push_rows,
        needs_eyes_rows=needs_eyes_rows,
        sale_title=latest.sale_title or "",
        captured_at=latest.captured_at,
        sale_ends_at=latest.sale_ends_at,
        auction_url=latest.auction_url or "",
        ctseller_sale_id=int(ctseller_sale_id),
        pinned=calendar_pins(db, account_id, records, payload_by_lot),
        tz=tz,
    )
    instagram = _instagram_ready(db, account_id)
    by_key = _index_calendar_rows(records)
    out: list[dict[str, Any]] = []
    for post in posts:
        slot = _slot_dict(post, ctseller_sale_id=int(ctseller_sale_id), instagram=instagram)
        if not slot:
            continue
        pinned = by_key.get(migrate_calendar_key(str(slot["slot_key"])))
        if pinned is not None and pinned.status in PINNED_STATUSES:
            slot["publish_target"] = row_target(pinned)
        if slot["publish_target"] not in PUBLISHABLE_TARGETS:
            continue
        out.append(slot)
    return out


def annotate_social_calendar(
    db: Session,
    account_id: str,
    ctseller_sale_id: int,
    posts: list[Any],
    *,
    now: datetime | None = None,
    records: list[FacebookPagePost] | None = None,
) -> list[dict[str, Any]]:
    if records is None:
        records = calendar_rows(db, account_id, ctseller_sale_id)
    by_key = _index_calendar_rows(records)
    instagram = _instagram_ready(db, account_id)
    moment = _as_utc(now or datetime.now(UTC))
    rows = [post if isinstance(post, dict) else post.model_dump() for post in posts]
    links = _GroupLinks(db, account_id, ctseller_sale_id, [str(row.get("lot_id") or "") for row in rows])
    annotated: list[dict[str, Any]] = []
    for data in rows:
        intended = str(data.get("platform") or "").strip() or FACEBOOK_PLATFORM
        target = current_publish_target(intended, instagram=instagram)
        lot_id = str(data.get("lot_id") or "").strip() or None
        key = ""
        if lot_id:
            key = calendar_slot_key(
                int(ctseller_sale_id),
                lot_id,
                slot_id_from_local_time(str(data.get("local_time") or "09:00")),
                data.get("post_date"),
            )
            pinned = by_key.get(key)
            if pinned is not None and pinned.status in PINNED_STATUSES:
                target = row_target(pinned)
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
            "facebook_review_note": None,
            "facebook_caption": None,
        }
        if target not in PUBLISHABLE_TARGETS:
            extra["slot_key"] = key
            extra["facebook_status"] = "copy_only"
            extra["facebook_status_label"] = facebook_status_label("copy_only")
            extra["facebook_blocked_reason"] = COPY_ONLY_REASONS.get(
                intended, "Copy only: nothing is posted automatically for this slot."
            )
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
        status = "planned"
        if row and row.status in {"scheduled", "publishing", "posted", "failed", "cancelled"}:
            status = row.status
            # Facebook publishes a scheduled post on its own at slot time.
            if status == "scheduled" and _row_live(row, now=moment):
                status = "posted"
        elif window == "missed":
            status = "missed"
        elif window == "too_soon":
            status = "too_soon"
        elif window == "too_far":
            status = "out_of_window"
        open_slot = not row or row.status in {"failed", "cancelled"}
        schedulable = window == "ok" and open_slot
        can_cancel = bool(row and status == "scheduled" and _cancel_allowed(row, now=moment))
        label = facebook_status_label(status)
        if status == "scheduled" and target in DEFERRED_TARGETS:
            label = "Scheduled (Estate-EZ posts at slot time)"
        extra.update(
            {
                "slot_key": key,
                "facebook_schedulable": schedulable,
                "facebook_can_post_now": open_slot,
                "facebook_can_cancel": can_cancel,
                "facebook_status": status,
                "facebook_status_label": label,
                "facebook_blocked_reason": facebook_blocked_reason(status, schedulable=schedulable),
                "facebook_slot_at": when,
                "facebook_post_id": (row.facebook_post_id if row else None),
                "facebook_error": (row.error_message if row else None),
                "facebook_review_note": (row.review_note if row else None),
                "facebook_caption": (row.caption if row and row.status in PINNED_STATUSES else None),
                "preflight": PAGE_PREFLIGHT,
            }
        )
        group_copy = str(data.get("group_copy") or "")
        if group_copy and lot_id in links.dests:
            dest_item, dest_sale = links.dests[lot_id]
            data = {
                **data,
                "group_copy": links.index.rewrite(
                    group_copy,
                    lot_id=lot_id,
                    calendar_key=key,
                    dest_item=dest_item,
                    dest_sale=dest_sale,
                    append_if_missing=True,
                ),
            }
        annotated.append({**data, **extra})
    return annotated


class _GroupLinks:
    """Lots, CT Bids destinations, and Groups tracked links for one sale, in three queries."""

    def __init__(self, db: Session, account_id: str, ctseller_sale_id: int, lot_ids: list[str]) -> None:
        from app.services.click_tracking import CHANNEL_GROUP_SHARE, TrackedLinkIndex

        ids = sorted({lid for lid in lot_ids if lid})
        self.lots: dict[str, Lot] = {}
        self.dests: dict[str, tuple[str, str]] = {}
        if ids:
            self.lots = {
                lot.id: lot
                for lot in db.query(Lot).filter(Lot.account_id == account_id, Lot.id.in_(ids)).all()
            }
        snaps: dict[str, EngagementSnapshot] = {}
        if self.lots:
            for row in (
                db.query(EngagementSnapshot)
                .filter(
                    EngagementSnapshot.account_id == account_id,
                    EngagementSnapshot.ctseller_sale_id == int(ctseller_sale_id),
                    EngagementSnapshot.lot_id.in_(sorted(self.lots)),
                )
                .all()
            ):
                snaps.setdefault(str(row.lot_id), row)
        for lid, lot in self.lots.items():
            snap = snaps.get(lid)
            item = ctbids_lot_url(lot, snapshot=snap)
            sale = ctbids_sale_url(
                sale_id=getattr(lot, "ctseller_sale_id", None),
                auction_url=(snap.auction_url if snap else "") or "",
                item_url=item,
            )
            self.dests[lid] = ((item or "").strip().rstrip("/"), (sale or "").strip().rstrip("/"))
        self.index = TrackedLinkIndex(
            db, account_id=account_id, lot_ids=list(self.lots), channel=CHANNEL_GROUP_SHARE
        )


def ensure_group_links(db: Session, account_id: str, ctseller_sale_id: int, slots: list[dict[str, Any]]) -> int:
    """Create missing Groups tracked links for calendar slots, so the calendar GET stays read-only."""
    from app.services.click_tracking import CHANNEL_GROUP_SHARE, tracked_pair

    links = _GroupLinks(db, account_id, ctseller_sale_id, [str(slot.get("lot_id") or "") for slot in slots])
    if not links.index.enabled:
        return 0
    created = 0
    for slot in slots:
        lot_id = str(slot.get("lot_id") or "")
        key = str(slot.get("slot_key") or "")
        if lot_id not in links.lots or not str(slot.get("group_copy") or "").strip():
            continue
        dest_item, dest_sale = links.dests[lot_id]
        if links.index.has_links(lot_id=lot_id, calendar_key=key, dest_item=dest_item, dest_sale=dest_sale):
            continue
        tracked_pair(
            db,
            account_id=account_id,
            lot=links.lots[lot_id],
            channel=CHANNEL_GROUP_SHARE,
            calendar_key=key,
            item_url=dest_item,
            sale_url=dest_sale,
        )
        created += 1
    if created:
        db.commit()
    return created


def _connected_page(account: Account) -> tuple[str, str] | None:
    page_id = str(getattr(account, "facebook_page_id", None) or "").strip()
    token = unseal_facebook_secret(getattr(account, "facebook_page_token_enc", None))
    if not page_id or not token:
        return None
    return page_id, token


def _row_live(row: FacebookPagePost, *, now: datetime) -> bool:
    """A scheduled Page feed row whose slot time has passed is live (Facebook published it).

    Instagram / Stories rows are posted by the queue, never by Facebook on its own.
    """
    if row_target(row) != PUBLISH_TARGET_FACEBOOK_PAGE:
        return False
    when = _as_aware(row.scheduled_publish_time)
    return when is not None and when <= _as_utc(now)


def _cancel_allowed(row: FacebookPagePost, *, now: datetime) -> bool:
    when = _as_aware(row.scheduled_publish_time)
    return when is not None and when - _as_utc(now) > CANCEL_MIN_LEAD


def _cancel_scheduled(
    db: Session,
    account: Account,
    row: FacebookPagePost,
    *,
    reason: str,
) -> None:
    """Owner-initiated only. Snapshot sync and rebuilds never call this."""
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
    row.review_note = None
    db.add(row)
    db.commit()


def reconcile_calendar_rows(
    db: Session,
    account: Account,
    ctseller_sale_id: int,
    *,
    now: datetime | None = None,
    records: list[FacebookPagePost] | None = None,
) -> int:
    """Local bookkeeping only — never a Graph call. Returns rows changed.

    * ``scheduled`` past its slot time -> ``posted`` (Facebook published it).
    * ``publishing`` with a stale claim -> ``failed`` with a check-the-Page note and no
      auto retries (the Graph call may have succeeded).
    * ``scheduled`` whose lot left the sale snapshot -> review note for the owner. The
      Graph post is left alone.
    """
    moment = _as_utc(now or datetime.now(UTC))
    if records is None:
        records = calendar_rows(db, account.id, ctseller_sale_id)
    snapshot_lots = {
        str(row[0])
        for row in db.query(EngagementSnapshot.lot_id)
        .filter(
            EngagementSnapshot.account_id == account.id,
            EngagementSnapshot.ctseller_sale_id == int(ctseller_sale_id),
            EngagementSnapshot.lot_id.isnot(None),
        )
        .all()
    }
    changed = 0
    for row in records:
        if row.status == "scheduled" and _row_live(row, now=moment):
            row.status = "posted"
            row.posted_at = row.posted_at or row.scheduled_publish_time
            row.review_note = None
            changed += 1
        elif (
            row.status == "scheduled"
            and row_target(row) in DEFERRED_TARGETS
            and not row.facebook_post_id
            and (when := _as_aware(row.scheduled_publish_time)) is not None
            and moment - when > DEFERRED_MAX_LATE + timedelta(hours=1)
        ):
            # Its queue job never ran (worker down, Page disconnected). Unpin so it can be redone.
            row.status = "failed"
            row.error_message = DEFERRED_MISSED_NOTE
            row.review_note = DEFERRED_MISSED_NOTE
            row.attempt_count = max(int(row.attempt_count or 0), AUTO_APPROVE_MAX_ATTEMPTS)
            changed += 1
        elif row.status == "publishing":
            claimed = _as_aware(row.claimed_at)
            if claimed is None or moment - claimed >= PUBLISH_CLAIM_STALE:
                row.status = "failed"
                row.error_message = INTERRUPTED_NOTE
                row.review_note = INTERRUPTED_NOTE
                row.attempt_count = max(int(row.attempt_count or 0), AUTO_APPROVE_MAX_ATTEMPTS)
                changed += 1
        if row.status == "scheduled" and row.review_note in (None, LOT_LEFT_SALE_NOTE):
            note = None if str(row.lot_id or "") in snapshot_lots else LOT_LEFT_SALE_NOTE
            if row.review_note != note:
                row.review_note = note
                changed += 1
    if changed:
        db.commit()
    return changed


def _photos_for_slot(db: Session, lot: Lot, slot_id: str) -> list:
    photos = _lot_photos(db, lot)
    return rotate_listing_photos(photos, photo_offset_for(slot_id))


def _draft_inputs(
    db: Session,
    account: Account,
    slot: dict[str, Any],
    *,
    include_follow_hook: bool = False,
) -> dict[str, Any] | None:
    """DB half of a slot draft: lot, photos, snapshot, tracked links. No Gemini."""
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
    from app.services.click_tracking import CHANNEL_GROUP_SHARE, CHANNEL_PAGE_POST, tracked_pair

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
    tz = account_zone_name(account)
    close_at = snapshot.sale_ends_at if snapshot else None
    title = lot.auction_title or slot.get("title") or ""
    angle = str(slot.get("slot_angle") or slot_angle_for(str(slot.get("local_time") or ""), hook=str(slot.get("hook") or "")))
    return {
        "caption_request": {
            "title": title,
            "description": lot.professional_description or "",
            "sku": lot.sku or slot.get("sku") or "",
            "item_url": item_url,
            "close_at": close_at,
            "include_follow_hook": include_follow_hook,
            "current_bid": float(snapshot.price) if snapshot else None,
            "watchers": int(snapshot.watchers) if snapshot else None,
            "sale_title": (snapshot.sale_title if snapshot else None) or _sale_title(db, lot),
            "angle": angle,
            "sale_url": sale_url,
            "tz": tz,
        },
        "urls": (dest_item, dest_sale, item_url, sale_url, group_item_url, group_sale_url),
        "hook": str(slot.get("hook") or ""),
        "tz": tz,
        "item": {
            "lot_id": lot.id,
            "sku": lot.sku,
            "title": title,
            "item_url": item_url,
            "sale_ends_at": close_at,
            "photo_ids": [p.id for p in photos],
            "photo_count": len(photos),
            "scheduled_publish_time": slot["when"],
            "calendar_key": slot["slot_key"],
            "calendar_sale_id": int(slot["ctseller_sale_id"]),
            "intended_platform": slot.get("intended_platform") or slot.get("platform") or "",
            "publish_target": slot.get("publish_target") or PUBLISH_TARGET_FACEBOOK_PAGE,
            "local_time": slot.get("local_time") or "",
            "hook": slot.get("hook") or "",
        },
    }


def _finish_draft(inputs: dict[str, Any], caption: str) -> dict[str, Any]:
    """Pure: swap tracked URLs into the caption and derive the Groups paste."""
    from app.services.click_tracking import apply_tracked_urls

    dest_item, dest_sale, item_url, sale_url, group_item_url, group_sale_url = inputs["urls"]
    caption = apply_tracked_urls(
        caption,
        dest_item=dest_item,
        dest_sale=dest_sale,
        short_item=item_url,
        short_sale=sale_url,
    )
    request = inputs["caption_request"]
    group_caption = group_post_copy(
        title=request["title"],
        hook=inputs["hook"],
        description=request["description"],
        caption=caption,
        item_url=group_item_url,
        close_at=request["close_at"],
        sale_url=group_sale_url,
        tz=inputs["tz"],
    )
    group_caption = apply_tracked_urls(
        group_caption,
        dest_item=dest_item,
        dest_sale=dest_sale,
        short_item=group_item_url,
        short_sale=group_sale_url,
    )
    return {**inputs["item"], "caption": caption, "group_caption": group_caption}


def _draft_items(
    db: Session,
    account: Account,
    slots: list[dict[str, Any]],
    *,
    include_follow_hook: bool = False,
    captions: list[str | None] | None = None,
) -> list[dict[str, Any] | None]:
    """Draft many slots: DB work in order, then only the missing captions go to Gemini, overlapped.

    ``captions[i]`` (the text the owner already reviewed) skips Gemini for that slot.
    """
    given = list(captions or [None] * len(slots))
    inputs = [_draft_inputs(db, account, slot, include_follow_hook=include_follow_hook) for slot in slots]
    db.commit()
    need = [i for i, inp in enumerate(inputs) if inp is not None and not (given[i] or "").strip()]
    drafted = draft_captions([inputs[i]["caption_request"] for i in need], drafter=draft_facebook_caption)
    text: dict[int, str] = dict(zip(need, drafted))
    out: list[dict[str, Any] | None] = []
    for i, inp in enumerate(inputs):
        if inp is None:
            out.append(None)
            continue
        out.append(_finish_draft(inp, (given[i] or "").strip() or text.get(i, "")))
    return out


def _record_for_slot(by_key: dict[str, FacebookPagePost], slot: dict[str, Any]) -> FacebookPagePost | None:
    return by_key.get(migrate_calendar_key(str(slot.get("slot_key") or "")))


def _account_owner(db: Session, account_id: str) -> User | None:
    return (
        db.query(User)
        .filter(User.account_id == account_id, User.role == "owner")
        .order_by(User.created_at.asc())
        .first()
    )


def auto_approve_ready(account: Account) -> bool:
    """Auto-approve on, Page connected, and no recorded dead-token error."""
    if not bool(getattr(account, "facebook_calendar_auto_approve", False)):
        return False
    if str(getattr(account, "facebook_token_error", None) or "").strip():
        return False
    return _connected_page(account) is not None


def sync_facebook_calendar_after_snapshot(
    db: Session,
    account_id: str,
    ctseller_sale_id: int,
    *,
    now: datetime | None = None,
    publish_new_slots: bool = False,
) -> None:
    """Reconcile slot rows locally; auto-approve new slots only when asked.

    Never deletes, moves, or edits a Graph post: approved / posted slots are pinned and
    the rebuilt calendar keeps them. Snapshot ingest passes ``publish_new_slots=False``
    so a catalogger Watcher upload cannot publish. Owner settings and the auto-approve
    sweep pass True.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return
    reconcile_calendar_rows(db, account, ctseller_sale_id, now=now)
    try:
        ensure_group_links(
            db,
            account_id,
            ctseller_sale_id,
            facebook_page_slots(db, account_id, ctseller_sale_id, tz=account_zone_name(account)),
        )
    except Exception:
        db.rollback()
        _LOG.exception("Groups tracked links failed account=%s sale=%s", account_id, ctseller_sale_id)
    if not publish_new_slots:
        return
    if not bool(getattr(account, "facebook_calendar_auto_approve", False)):
        return
    if not auto_approve_ready(account):
        _LOG.info(
            "Auto-approve skipped account=%s sale=%s: Facebook Page not connected or token needs reconnect",
            account_id,
            ctseller_sale_id,
        )
        return
    owner = _account_owner(db, account_id)
    if owner is None:
        return
    from app import facebook_post_tasks

    moment = _as_utc(now or datetime.now(UTC))
    records = calendar_rows(db, account_id, ctseller_sale_id)
    by_key = _index_calendar_rows(records)
    queued = facebook_post_tasks.live_slot_keys(db, account_id)
    due = [
        slot
        for slot in facebook_page_slots(
            db, account_id, ctseller_sale_id, records=records, tz=account_zone_name(account)
        )
        if schedule_window_state(slot["when"], now=moment) == "ok"
        and migrate_calendar_key(str(slot["slot_key"])) not in queued
        and auto_retry_allowed(_record_for_slot(by_key, slot), now=moment)
    ]
    pending = [item for item in _draft_items(db, account, due) if item]
    if not pending:
        return
    # The publish queue posts them (worker in production, a thread in dev).
    try:
        facebook_post_tasks.enqueue_batch(
            db,
            account_id=account_id,
            user_id=owner.id,
            items=pending,
            include_follow_hook=False,
            force=False,
            source=CALENDAR_SOURCE,
            now=moment,
        )
        facebook_post_tasks.kick(db.get_bind(), account_id, now=now)
    except Exception:
        _LOG.exception(
            "Auto-approve Facebook calendar failed account=%s sale=%s",
            account_id,
            ctseller_sale_id,
        )


# A sale with no close time stops getting auto-approve passes this long after its last snapshot.
OPEN_SALE_NO_CLOSE_GRACE = timedelta(days=14)


def open_calendar_sale_ids(db: Session, account_id: str, *, now: datetime | None = None) -> list[int]:
    """CT Seller sales whose Push calendar can still publish: not closed, snapshot not stale."""
    moment = _as_utc(now or datetime.now(UTC))
    rows = (
        db.query(
            EngagementSnapshot.ctseller_sale_id,
            func.max(EngagementSnapshot.sale_ends_at),
            func.max(EngagementSnapshot.captured_at),
        )
        .filter(EngagementSnapshot.account_id == account_id)
        .group_by(EngagementSnapshot.ctseller_sale_id)
        .all()
    )
    out: list[int] = []
    for sale_id, ends_at, captured_at in rows:
        if sale_id is None:
            continue
        ends = _as_aware(ends_at)
        if ends is not None:
            if ends > moment:
                out.append(int(sale_id))
            continue
        captured = _as_aware(captured_at)
        if captured is not None and moment - captured <= OPEN_SALE_NO_CLOSE_GRACE:
            out.append(int(sale_id))
    return sorted(out)


def auto_approve_targets(db: Session, *, now: datetime | None = None) -> list[tuple[str, int]]:
    """(account_id, sale_id) pairs the sweep should visit: auto-approve on, Page usable, sale open."""
    accounts = (
        db.query(Account)
        .filter(
            Account.facebook_calendar_auto_approve.is_(True),
            Account.facebook_page_id.isnot(None),
            Account.facebook_page_token_enc.isnot(None),
        )
        .all()
    )
    out: list[tuple[str, int]] = []
    for account in accounts:
        if not auto_approve_ready(account):
            continue
        for sale_id in open_calendar_sale_ids(db, account.id, now=now):
            out.append((account.id, sale_id))
    return out


def sweep_calendar_auto_approve(db: Session, *, now: datetime | None = None) -> int:
    """Owner-configured auto-approve: publish pending Push slots without a catalogger trigger."""
    n = 0
    for account_id, sale_id in auto_approve_targets(db, now=now):
        try:
            sync_facebook_calendar_after_snapshot(
                db, account_id, int(sale_id), now=now, publish_new_slots=True
            )
            n += 1
        except Exception:
            db.rollback()
            _LOG.exception(
                "Auto-approve sweep failed account=%s sale=%s",
                account_id,
                sale_id,
            )
    return n


def _skip(lot_id: str, key: str, error: str, *, failed: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = {"lot_id": lot_id, "calendar_key": key, "ok": False, "error": error}
    if not failed:
        row["skipped"] = True
    return row


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
    """Return (publishable items, skipped result rows) for Push-calendar Page slots.

    Only the exact slot keys asked for. An empty request is a 400, never "every slot".
    Keys that are no longer on the calendar come back as skipped rows.
    """
    wanted = requested_slot_keys(slot_keys)
    records = calendar_rows(db, account.id, ctseller_sale_id)
    by_key = _index_calendar_rows(records)
    slots = facebook_page_slots(
        db, account.id, ctseller_sale_id, records=records, tz=account_zone_name(account)
    )
    chosen = [slot for slot in slots if migrate_calendar_key(str(slot["slot_key"])) in wanted]
    captions = captions or {}
    publishable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    found = {migrate_calendar_key(str(slot["slot_key"])) for slot in chosen}
    for key in sorted(wanted - found):
        parts = key.split(":")
        lot_id = parts[2] if len(parts) > 2 else ""
        skipped.append(
            _skip(lot_id, key, "This slot is no longer on the Push calendar. Refresh and approve again.")
        )
    eligible: list[dict[str, Any]] = []
    for slot in chosen:
        window = schedule_window_state(slot["when"], now=now)
        lot_id = str(slot["lot_id"])
        key = str(slot["slot_key"])
        existing = _record_for_slot(by_key, slot)
        if existing is not None and existing.status in PINNED_STATUSES:
            skipped.append(_skip(lot_id, key, "This Push-calendar slot is already scheduled or posted."))
            continue
        if not post_now and window != "ok":
            skipped.append(
                _skip(
                    lot_id,
                    key,
                    "This calendar slot is outside Facebook's 10-minute to 75-day window. "
                    "Use Post now to publish immediately.",
                )
            )
            continue
        eligible.append(slot)
    # The owner already reviewed a caption in the preview; reuse it instead of a second Gemini call.
    reviewed = [
        captions.get(str(slot["slot_key"]))
        or captions.get(migrate_calendar_key(str(slot["slot_key"])))
        or captions.get(str(slot["lot_id"]))
        for slot in eligible
    ]
    drafted_items = _draft_items(
        db, account, eligible, include_follow_hook=include_follow_hook, captions=reviewed
    )
    for slot, drafted in zip(eligible, drafted_items):
        if not drafted:
            skipped.append(
                _skip(
                    str(slot["lot_id"]),
                    str(slot["slot_key"]),
                    "Catalog the lot with listing photos before scheduling the Page post.",
                    failed=True,
                )
            )
            continue
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
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Owner Unapprove: delete a still-unpublished scheduled Graph post. Live posts are refused."""
    wanted = requested_slot_keys(slot_keys)
    moment = _as_utc(now or datetime.now(UTC))
    by_key = _index_calendar_rows(calendar_rows(db, account.id, ctseller_sale_id))
    results: list[dict[str, Any]] = []
    for key in sorted(wanted):
        row = by_key.get(key)
        lot_id = str(row.lot_id) if row and row.lot_id else ""
        if row is None or row.status != "scheduled":
            results.append(_skip(lot_id, key, "No scheduled Page post for this slot."))
            continue
        if not _cancel_allowed(row, now=moment):
            if _row_live(row, now=moment):
                row.status = "posted"
                row.posted_at = row.posted_at or row.scheduled_publish_time
                db.add(row)
                db.commit()
            results.append(
                _skip(
                    lot_id,
                    key,
                    "This post is already live (or about to go live) on Facebook. "
                    "Delete it on the Page if it should come down.",
                )
            )
            continue
        _cancel_scheduled(
            db,
            account,
            row,
            reason="Owner unapproved this Push calendar slot.",
        )
        db.refresh(row)
        if row.status == "cancelled":
            from app import facebook_post_tasks

            facebook_post_tasks.cancel_slot_jobs(db, account.id, key, reason="Owner unapproved this slot.")
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
    wanted = requested_slot_keys(slot_keys)
    records = calendar_rows(db, account.id, ctseller_sale_id)
    by_key = _index_calendar_rows(records)
    slots = facebook_page_slots(
        db, account.id, ctseller_sale_id, records=records, tz=account_zone_name(account)
    )
    chosen = [slot for slot in slots if migrate_calendar_key(str(slot["slot_key"])) in wanted]
    open_slots = [
        slot for slot in chosen
        if not ((row := _record_for_slot(by_key, slot)) is not None and row.status in PINNED_STATUSES)
    ]
    drafted_by_key = {
        str(slot["slot_key"]): item
        for slot, item in zip(
            open_slots,
            _draft_items(db, account, open_slots, include_follow_hook=include_follow_hook),
        )
    }
    drafts: list[dict[str, Any]] = []
    for slot in chosen:
        key = str(slot["slot_key"])
        row = _record_for_slot(by_key, slot)
        already = bool(row and row.status in PINNED_STATUSES)
        drafted = None if already else drafted_by_key.get(key)
        if not drafted:
            drafts.append(
                {
                    "lot_id": slot["lot_id"],
                    "sku": slot.get("sku") or "",
                    "title": slot.get("title") or "",
                    "item_url": slot.get("item_url") or "",
                    "sale_ends_at": None,
                    "caption": (row.caption if already and row else ""),
                    "photo_ids": [],
                    "photo_count": 0,
                    "already_posted": already,
                    "existing_posts": [],
                    "error": (
                        "This Push-calendar slot is already scheduled or posted."
                        if already
                        else "Catalog the lot with listing photos before scheduling the Page post."
                    ),
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
                "already_posted": False,
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
