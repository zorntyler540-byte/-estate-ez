"""Short Estate-EZ redirects for CT Bids URLs in Facebook captions.

``/go/<code>`` looks up a stored destination and 302s there. Destinations are
never taken from the request, so this cannot be used as an open redirect.
Clicks store no raw IPs. Facebook preview crawlers are not counted.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import FacebookPagePost, Lot, TrackedLink, TrackedLinkClick, TrackedLinkDailyCount
from app.services.ctbids_urls import SALE_LINK_PREFIX, ctbids_sale_url, sale_link_line

PACIFIC = ZoneInfo("America/Los_Angeles")
PRODUCTION_API_ORIGIN = "https://caringtransitions-ai-production.up.railway.app"
CHANNEL_PAGE_POST = "page_post"
CHANNEL_GROUP_SHARE = "group_share"
KIND_LOT = "lot"
KIND_SALE = "sale"
CODE_RE = re.compile(r"^[A-Za-z0-9]{8,16}$")
_CTBIDS_URL_RE = re.compile(r"https?://(?:www\.)?ctbids\.com/[^\s<>\"']+", re.I)
_GO_URL_RE = re.compile(r"https?://[^\s<>\"']+/go/[A-Za-z0-9]{8,16}", re.I)
_ALLOWED_DEST_HOSTS = frozenset({"ctbids.com", "www.ctbids.com"})
_ALLOWED_DEST_RE = re.compile(
    r"^https://(?:www\.)?ctbids\.com/estate-sale/\d+(?:/item/\d+)?$",
    re.I,
)
_BOT_UA_RE = re.compile(
    r"("
    r"facebookexternalhit|facebot|facebookcatalog|"
    r"twitterbot|linkedinbot|slackbot|slack-imgproxy|"
    r"whatsapp|telegrambot|discordbot|"
    r"googlebot|bingbot|applebot|yandexbot|duckduckbot|"
    r"preview|crawler|spider|bot"
    r")",
    re.I,
)
_LOG = logging.getLogger(__name__)

# In-memory only: hashed client key (never raw IP) → recent hit times.
_RATE_HITS: dict[str, list[float]] = defaultdict(list)
_RATE_PER_CLIENT = 60
_RATE_PER_CODE = 120
_RATE_WINDOW_S = 60.0


def public_api_origin() -> str:
    """Host printed into captions. Empty / local / Railway API means do not wrap."""
    origin = (settings.public_api_origin or "").strip().rstrip("/")
    if not origin:
        return ""
    try:
        host = (urlparse(origin).hostname or "").lower()
    except ValueError:
        return ""
    if not host or host in {"localhost", "127.0.0.1"}:
        return ""
    if host.endswith(".up.railway.app") or host.endswith(".railway.app"):
        return ""
    return origin


def click_tracking_in_captions() -> bool:
    """Short links in Facebook copy only with a dedicated public tracking host.

    Empty, localhost, and the Railway API keep CT Bids lot/sale URLs in posts.
    """
    return bool(public_api_origin())


def tracked_url_for_code(code: str) -> str:
    origin = public_api_origin() or "http://localhost:8000"
    return f"{origin}/go/{code}"


def canonicalize_destination(url: str) -> str | None:
    """https://(www.)ctbids.com/estate-sale/<id> or .../item/<id>. No query, fragment, or userinfo."""
    raw = (url or "").strip()
    if not raw:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if (parsed.scheme or "").lower() != "https":
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if "@" in (parsed.netloc or ""):
        return None
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_DEST_HOSTS:
        return None
    path = (parsed.path or "").rstrip("/")
    candidate = urlunparse(("https", host, path, "", "", ""))
    if not _ALLOWED_DEST_RE.match(candidate):
        return None
    return candidate


def is_allowed_destination(url: str) -> bool:
    return canonicalize_destination(url) is not None


def is_preview_bot(*, user_agent: str = "", purpose: str = "") -> bool:
    if purpose.strip().lower() == "preview":
        return True
    return bool(_BOT_UA_RE.search(user_agent or ""))


def reset_rate_limits() -> None:
    _RATE_HITS.clear()


def _rate_allow(key: str, limit: int) -> bool:
    now = time.monotonic()
    hits = _RATE_HITS[key]
    cutoff = now - _RATE_WINDOW_S
    keep = [t for t in hits if t >= cutoff]
    if len(keep) >= limit:
        _RATE_HITS[key] = keep
        return False
    keep.append(now)
    _RATE_HITS[key] = keep
    return True


def _client_rate_key(request: Request) -> str:
    host = ""
    if request.client is not None:
        host = request.client.host or ""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw = f"{day}:{host}".encode("utf-8")
    secret = (settings.secret_key or "estate-ez").encode("utf-8")
    return hmac.new(secret, raw, hashlib.sha256).hexdigest()[:16]


def _safe_referrer(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))[:400]


def _new_code() -> str:
    return secrets.token_hex(5)


def get_or_create_tracked_link(
    db: Session,
    *,
    account_id: str,
    sale_id: str | None,
    lot_id: str | None,
    destination_url: str,
    destination_kind: str,
    channel: str,
    calendar_key: str = "",
    facebook_post_id: str | None = None,
) -> TrackedLink | None:
    dest = canonicalize_destination(destination_url)
    if not dest:
        return None
    cal = (calendar_key or "").strip()
    chan = (channel or CHANNEL_PAGE_POST).strip() or CHANNEL_PAGE_POST
    kind = (destination_kind or KIND_LOT).strip() or KIND_LOT
    q = db.query(TrackedLink).filter(
        TrackedLink.account_id == account_id,
        TrackedLink.channel == chan,
        TrackedLink.destination_kind == kind,
        TrackedLink.destination_url == dest,
        TrackedLink.calendar_key == cal,
    )
    if lot_id:
        q = q.filter(TrackedLink.lot_id == lot_id)
    else:
        q = q.filter(TrackedLink.lot_id.is_(None))
    row = q.first()
    if row is not None:
        if facebook_post_id and not row.facebook_post_id:
            row.facebook_post_id = facebook_post_id
            db.add(row)
        return row
    for _ in range(8):
        code = _new_code()
        if db.query(TrackedLink).filter(TrackedLink.code == code).first():
            continue
        row = TrackedLink(
            code=code,
            account_id=account_id,
            sale_id=sale_id,
            lot_id=lot_id,
            facebook_post_id=facebook_post_id,
            calendar_key=cal,
            channel=chan,
            destination_kind=kind,
            destination_url=dest,
        )
        db.add(row)
        db.flush()
        return row
    raise RuntimeError("Could not allocate a unique tracked-link code")


def lookup_tracked_url(
    db: Session,
    *,
    account_id: str,
    lot_id: str | None,
    destination_url: str,
    destination_kind: str,
    channel: str,
    calendar_key: str = "",
) -> str:
    dest = canonicalize_destination(destination_url) or (destination_url or "").strip().rstrip("/")
    if not dest:
        return ""
    q = db.query(TrackedLink).filter(
        TrackedLink.account_id == account_id,
        TrackedLink.channel == channel,
        TrackedLink.destination_kind == destination_kind,
        TrackedLink.destination_url == dest,
        TrackedLink.calendar_key == (calendar_key or ""),
    )
    if lot_id:
        q = q.filter(TrackedLink.lot_id == lot_id)
    else:
        q = q.filter(TrackedLink.lot_id.is_(None))
    row = q.first()
    return tracked_url_for_code(row.code) if row is not None else ""


def destinations_for_lot(db: Session, lot: Lot) -> tuple[str, str]:
    from app.services.facebook_page import _latest_snapshot, ctbids_lot_url

    snapshot = _latest_snapshot(db, lot)
    item_url = ctbids_lot_url(lot, snapshot=snapshot)
    sale_url = ctbids_sale_url(
        sale_id=getattr(lot, "ctseller_sale_id", None),
        auction_url=(snapshot.auction_url if snapshot else "") or "",
        item_url=item_url,
    )
    return (item_url or "").strip().rstrip("/"), (sale_url or "").strip().rstrip("/")


def tracked_pair(
    db: Session,
    *,
    account_id: str,
    lot: Lot,
    channel: str,
    calendar_key: str = "",
    facebook_post_id: str | None = None,
    item_url: str = "",
    sale_url: str = "",
) -> tuple[str, str, str, str]:
    """Return (dest_item, dest_sale, short_item, short_sale)."""
    dest_item = (item_url or "").strip().rstrip("/")
    dest_sale = (sale_url or "").strip().rstrip("/")
    if not dest_item and not dest_sale:
        dest_item, dest_sale = destinations_for_lot(db, lot)
    if not click_tracking_in_captions():
        return dest_item, dest_sale, dest_item, dest_sale
    short_item = dest_item
    short_sale = dest_sale
    sale_id = getattr(lot, "sale_id", None)
    if dest_item:
        link = get_or_create_tracked_link(
            db,
            account_id=account_id,
            sale_id=sale_id,
            lot_id=lot.id,
            destination_url=dest_item,
            destination_kind=KIND_LOT,
            channel=channel,
            calendar_key=calendar_key,
            facebook_post_id=facebook_post_id,
        )
        if link is not None:
            short_item = tracked_url_for_code(link.code)
    if dest_sale:
        link = get_or_create_tracked_link(
            db,
            account_id=account_id,
            sale_id=sale_id,
            lot_id=lot.id,
            destination_url=dest_sale,
            destination_kind=KIND_SALE,
            channel=channel,
            calendar_key=calendar_key,
            facebook_post_id=facebook_post_id,
        )
        if link is not None:
            short_sale = tracked_url_for_code(link.code)
    return dest_item, dest_sale, short_item, short_sale


def restore_ctbids_urls(
    db: Session,
    text: str,
    *,
    account_id: str,
    fallback_item_url: str = "",
    fallback_sale_url: str = "",
) -> str:
    """Turn leftover /go/<code> URLs back into this account's stored CT Bids destination.

    Codes that are missing or belong to another account are never resolved to that
    destination. They become this lot's own CT Bids URL, or are stripped.
    """
    fallback = (fallback_item_url or fallback_sale_url or "").strip()

    def _swap(match: re.Match[str]) -> str:
        code = match.group(1)
        row = (
            db.query(TrackedLink)
            .filter(TrackedLink.code == code, TrackedLink.account_id == account_id)
            .first()
        )
        if row is not None and is_allowed_destination(row.destination_url):
            return row.destination_url
        return fallback

    return re.sub(r"https?://[^\s<>\"']+/go/([A-Za-z0-9]{8,16})", _swap, text or "", flags=re.I)


def apply_tracked_urls(
    text: str,
    *,
    dest_item: str,
    dest_sale: str,
    short_item: str,
    short_sale: str,
) -> str:
    """Swap stored CT Bids URLs for their short redirects. Longer item URL first."""
    out = text or ""
    pairs: list[tuple[str, str]] = []
    if dest_item and short_item and dest_item != short_item:
        pairs.append((dest_item, short_item))
    if dest_sale and short_sale and dest_sale != short_sale:
        pairs.append((dest_sale, short_sale))
    pairs.sort(key=lambda row: len(row[0]), reverse=True)
    for dest, short in pairs:
        out = out.replace(dest + "/", short)
        out = out.replace(dest, short)

    def _swap_leftover(match: re.Match[str]) -> str:
        raw = match.group(0).rstrip("/")
        if "/go/" in raw:
            return match.group(0)
        if dest_item and raw.rstrip("/") == dest_item.rstrip("/"):
            return short_item or match.group(0)
        if dest_sale and raw.rstrip("/") == dest_sale.rstrip("/"):
            return short_sale or match.group(0)
        if "/item/" in raw:
            return short_item or match.group(0)
        return short_sale or match.group(0)

    if short_item or short_sale:
        out = _CTBIDS_URL_RE.sub(_swap_leftover, out)
    return out


def rewrite_copy_with_tracked_links(
    db: Session,
    *,
    account_id: str,
    lot: Lot,
    text: str,
    channel: str,
    calendar_key: str = "",
    facebook_post_id: str | None = None,
    item_url: str = "",
    sale_url: str = "",
    append_if_missing: bool = False,
) -> str:
    if not click_tracking_in_captions():
        dest_item = (item_url or "").strip().rstrip("/")
        dest_sale = (sale_url or "").strip().rstrip("/")
        if not dest_item and not dest_sale:
            dest_item, dest_sale = destinations_for_lot(db, lot)
        return restore_ctbids_urls(
            db,
            text,
            account_id=account_id,
            fallback_item_url=dest_item,
            fallback_sale_url=dest_sale,
        )
    dest_item, dest_sale, short_item, short_sale = tracked_pair(
        db,
        account_id=account_id,
        lot=lot,
        channel=channel,
        calendar_key=calendar_key,
        facebook_post_id=facebook_post_id,
        item_url=item_url,
        sale_url=sale_url,
    )
    out = apply_tracked_urls(
        text,
        dest_item=dest_item,
        dest_sale=dest_sale,
        short_item=short_item,
        short_sale=short_sale,
    )
    if append_if_missing and (text or "").strip():
        if short_item and short_item not in out and dest_item and dest_item not in out:
            if not _GO_URL_RE.search(out):
                out = f"{out.rstrip()}\n\n{short_item}"
        sale_line = sale_link_line(short_sale) if short_sale else ""
        if sale_line and sale_line not in out and SALE_LINK_PREFIX not in out:
            out = f"{out.rstrip()}\n\n{sale_line}"
    return out


def bind_links_to_post(
    db: Session,
    *,
    account_id: str,
    lot_id: str | None,
    channel: str,
    calendar_key: str,
    facebook_post_id: str,
) -> None:
    q = db.query(TrackedLink).filter(
        TrackedLink.account_id == account_id,
        TrackedLink.channel == channel,
        TrackedLink.calendar_key == (calendar_key or ""),
        TrackedLink.facebook_post_id.is_(None),
    )
    if lot_id:
        q = q.filter(TrackedLink.lot_id == lot_id)
    for row in q.all():
        row.facebook_post_id = facebook_post_id
        db.add(row)


def follow_tracked_link(db: Session, *, code: str, request: Request) -> RedirectResponse:
    token = (code or "").strip()
    if not CODE_RE.match(token):
        raise HTTPException(status_code=404, detail="Link not found")
    row = db.query(TrackedLink).filter(TrackedLink.code == token).first()
    dest = canonicalize_destination(row.destination_url) if row is not None else None
    if row is None or not dest:
        raise HTTPException(status_code=404, detail="Link not found")
    headers = {"Cache-Control": "private, no-store"}
    response = RedirectResponse(url=dest, status_code=302, headers=headers)
    if request.method.upper() == "HEAD":
        return response
    ua = request.headers.get("user-agent") or ""
    purpose = request.headers.get("purpose") or request.headers.get("x-purpose") or ""
    if is_preview_bot(user_agent=ua, purpose=purpose):
        return response
    client_key = _client_rate_key(request)
    if not _rate_allow(f"c:{client_key}", _RATE_PER_CLIENT):
        return response
    if not _rate_allow(f"l:{token}", _RATE_PER_CODE):
        return response
    try:
        _record_click(db, row, dest, referrer=_safe_referrer(request.headers.get("referer") or ""))
        db.commit()
    except Exception:
        db.rollback()
        _LOG.exception("Failed to record tracked-link click code=%s", token)
    return response


def _pacific_day(when: datetime | None = None) -> str:
    now = when or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(PACIFIC).date().isoformat()


def _pacific_day_start_utc(when: datetime | None = None) -> datetime:
    now = when or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(PACIFIC)
    start_local = datetime(local.year, local.month, local.day, tzinfo=PACIFIC)
    return start_local.astimezone(timezone.utc)


def _bump_daily_count(db: Session, row: TrackedLink) -> TrackedLinkDailyCount:
    day = _pacific_day()
    stat = (
        db.query(TrackedLinkDailyCount)
        .filter(
            TrackedLinkDailyCount.link_id == row.id,
            TrackedLinkDailyCount.day_pacific == day,
        )
        .first()
    )
    if stat is None:
        stat = TrackedLinkDailyCount(
            link_id=row.id,
            account_id=row.account_id,
            sale_id=row.sale_id,
            lot_id=row.lot_id,
            facebook_post_id=row.facebook_post_id,
            calendar_key=row.calendar_key or "",
            channel=row.channel,
            destination_kind=row.destination_kind,
            day_pacific=day,
            clicks=0,
        )
        db.add(stat)
        db.flush()
    stat.clicks = int(stat.clicks or 0) + 1
    if row.facebook_post_id and not stat.facebook_post_id:
        stat.facebook_post_id = row.facebook_post_id
    db.add(stat)
    return stat


def _record_click(db: Session, row: TrackedLink, dest: str, *, referrer: str) -> None:
    _bump_daily_count(db, row)
    cap = int(settings.click_raw_per_code_per_day)
    raw_n = int(
        db.query(func.count())
        .select_from(TrackedLinkClick)
        .where(
            TrackedLinkClick.code == row.code,
            TrackedLinkClick.clicked_at >= _pacific_day_start_utc(),
        )
        .scalar()
        or 0
    )
    if raw_n >= cap:
        return
    db.add(
        TrackedLinkClick(
            link_id=row.id,
            account_id=row.account_id,
            code=row.code,
            sale_id=row.sale_id,
            lot_id=row.lot_id,
            facebook_post_id=row.facebook_post_id,
            calendar_key=row.calendar_key or "",
            channel=row.channel,
            destination_kind=row.destination_kind,
            destination_url=dest,
            referrer=referrer,
        )
    )


def purge_old_tracked_clicks(db: Session, *, older_than_days: int | None = None) -> int:
    days = int(older_than_days if older_than_days is not None else settings.click_raw_retention_days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    return int(
        db.query(TrackedLinkClick)
        .filter(TrackedLinkClick.clicked_at < cutoff)
        .delete(synchronize_session=False)
        or 0
    )


def click_counts_by_lot(db: Session, account_id: str, lot_ids: list[str]) -> dict[str, int]:
    ids = [lid for lid in lot_ids if lid]
    if not ids:
        return {}
    rows = (
        db.query(TrackedLinkDailyCount.lot_id, func.coalesce(func.sum(TrackedLinkDailyCount.clicks), 0))
        .filter(
            TrackedLinkDailyCount.account_id == account_id,
            TrackedLinkDailyCount.lot_id.in_(ids),
        )
        .group_by(TrackedLinkDailyCount.lot_id)
        .all()
    )
    return {str(lid): int(n) for lid, n in rows if lid}


def sale_click_report(db: Session, *, account_id: str, sale_id: str) -> dict[str, Any]:
    lots = {
        lot.id: lot
        for lot in db.query(Lot).filter(Lot.account_id == account_id, Lot.sale_id == sale_id).all()
    }
    total = int(
        db.query(func.coalesce(func.sum(TrackedLinkDailyCount.clicks), 0))
        .filter(
            TrackedLinkDailyCount.account_id == account_id,
            TrackedLinkDailyCount.sale_id == sale_id,
        )
        .scalar()
        or 0
    )
    day_rows = [
        {"day": day, "clicks": int(n)}
        for day, n in (
            db.query(TrackedLinkDailyCount.day_pacific, func.sum(TrackedLinkDailyCount.clicks))
            .filter(
                TrackedLinkDailyCount.account_id == account_id,
                TrackedLinkDailyCount.sale_id == sale_id,
            )
            .group_by(TrackedLinkDailyCount.day_pacific)
            .order_by(TrackedLinkDailyCount.day_pacific)
            .all()
        )
    ]
    by_lot: dict[str, dict[str, Any]] = {}
    for lot_id, n in (
        db.query(TrackedLinkDailyCount.lot_id, func.sum(TrackedLinkDailyCount.clicks))
        .filter(
            TrackedLinkDailyCount.account_id == account_id,
            TrackedLinkDailyCount.sale_id == sale_id,
        )
        .group_by(TrackedLinkDailyCount.lot_id)
        .all()
    ):
        lot = lots.get(lot_id or "")
        by_lot[lot_id or "unknown"] = {
            "lot_id": lot_id,
            "sku": (lot.sku if lot else "") or "",
            "title": (lot.auction_title if lot else "") or "",
            "clicks": int(n or 0),
        }
    post_rows_raw = (
        db.query(
            TrackedLinkDailyCount.facebook_post_id,
            TrackedLinkDailyCount.calendar_key,
            TrackedLinkDailyCount.channel,
            TrackedLinkDailyCount.lot_id,
            func.sum(TrackedLinkDailyCount.clicks),
        )
        .filter(
            TrackedLinkDailyCount.account_id == account_id,
            TrackedLinkDailyCount.sale_id == sale_id,
        )
        .group_by(
            TrackedLinkDailyCount.facebook_post_id,
            TrackedLinkDailyCount.calendar_key,
            TrackedLinkDailyCount.channel,
            TrackedLinkDailyCount.lot_id,
        )
        .all()
    )
    post_ids = {pid for pid, *_rest in post_rows_raw if pid}
    posts = {}
    if post_ids:
        posts = {
            row.id: row
            for row in db.query(FacebookPagePost)
            .filter(FacebookPagePost.account_id == account_id, FacebookPagePost.id.in_(post_ids))
            .all()
        }
    by_post: dict[str, dict[str, Any]] = {}
    for facebook_post_id, calendar_key, channel, lot_id, n in post_rows_raw:
        lot = lots.get(lot_id or "")
        if facebook_post_id:
            post_key = facebook_post_id
            post = posts.get(facebook_post_id)
            caption = (post.caption if post else "") or ""
            label = caption.splitlines()[0][:80] if caption else "Page post"
        else:
            post_key = f"{channel}:{lot_id or ''}:{calendar_key or ''}"
            label = "Group share" if channel == CHANNEL_GROUP_SHARE else "Page post"
        if post_key not in by_post:
            by_post[post_key] = {
                "facebook_post_id": facebook_post_id,
                "calendar_key": calendar_key or "",
                "channel": channel,
                "lot_id": lot_id,
                "sku": (lot.sku if lot else "") or "",
                "title": (lot.auction_title if lot else "") or "",
                "label": label,
                "clicks": 0,
            }
        by_post[post_key]["clicks"] += int(n or 0)
    lot_rows = sorted(by_lot.values(), key=lambda row: (-int(row["clicks"]), str(row.get("sku") or "")))
    post_rows = sorted(by_post.values(), key=lambda row: (-int(row["clicks"]), str(row.get("label") or "")))
    return {
        "sale_id": sale_id,
        "total": total,
        "by_day": day_rows,
        "by_lot": lot_rows,
        "by_post": post_rows,
    }
