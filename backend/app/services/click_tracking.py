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
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import FacebookPagePost, Lot, TrackedLink, TrackedLinkClick
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


def is_allowed_destination(url: str) -> bool:
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_DEST_HOSTS:
        return False
    path = parsed.path or ""
    if "/estate-sale/" not in path:
        return False
    return True


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
    dest = (destination_url or "").strip()
    if not is_allowed_destination(dest):
        return None
    dest = dest.rstrip("/")
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
    dest = (destination_url or "").strip().rstrip("/")
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


def restore_ctbids_urls(db: Session, text: str) -> str:
    """Turn leftover /go/<code> URLs back into the stored CT Bids destination."""

    def _swap(match: re.Match[str]) -> str:
        code = match.group(1)
        row = db.query(TrackedLink).filter(TrackedLink.code == code).first()
        if row is not None and is_allowed_destination(row.destination_url):
            return row.destination_url
        return match.group(0)

    return re.sub(r"https?://[^\s<>\"']+/go/([A-Za-z0-9]{8,16})", _swap, text or "", flags=re.I)


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
        return restore_ctbids_urls(db, text)
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
    if row is None or not is_allowed_destination(row.destination_url):
        raise HTTPException(status_code=404, detail="Link not found")
    dest = row.destination_url
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
            destination_url=row.destination_url,
            referrer=_safe_referrer(request.headers.get("referer") or ""),
        )
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        _LOG.exception("Failed to record tracked-link click code=%s", token)
    return response


def click_counts_by_lot(db: Session, account_id: str, lot_ids: list[str]) -> dict[str, int]:
    ids = [lid for lid in lot_ids if lid]
    if not ids:
        return {}
    rows = (
        db.query(TrackedLinkClick.lot_id, func.count())
        .filter(
            TrackedLinkClick.account_id == account_id,
            TrackedLinkClick.lot_id.in_(ids),
        )
        .group_by(TrackedLinkClick.lot_id)
        .all()
    )
    return {str(lid): int(n) for lid, n in rows if lid}


def sale_click_report(db: Session, *, account_id: str, sale_id: str) -> dict[str, Any]:
    clicks = (
        db.query(TrackedLinkClick)
        .filter(
            TrackedLinkClick.account_id == account_id,
            TrackedLinkClick.sale_id == sale_id,
        )
        .all()
    )
    lots = {
        lot.id: lot
        for lot in db.query(Lot).filter(Lot.account_id == account_id, Lot.sale_id == sale_id).all()
    }
    post_ids = {c.facebook_post_id for c in clicks if c.facebook_post_id}
    posts = {}
    if post_ids:
        posts = {
            row.id: row
            for row in db.query(FacebookPagePost)
            .filter(FacebookPagePost.account_id == account_id, FacebookPagePost.id.in_(post_ids))
            .all()
        }
    by_day: dict[str, int] = defaultdict(int)
    by_lot: dict[str, dict[str, Any]] = {}
    by_post: dict[str, dict[str, Any]] = {}
    for click in clicks:
        when = click.clicked_at or datetime.now(timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        day = when.astimezone(PACIFIC).date().isoformat()
        by_day[day] += 1
        lot = lots.get(click.lot_id or "")
        lot_key = click.lot_id or "unknown"
        if lot_key not in by_lot:
            by_lot[lot_key] = {
                "lot_id": click.lot_id,
                "sku": (lot.sku if lot else "") or "",
                "title": (lot.auction_title if lot else "") or "",
                "clicks": 0,
            }
        by_lot[lot_key]["clicks"] += 1
        post = posts.get(click.facebook_post_id or "")
        if click.facebook_post_id:
            post_key = click.facebook_post_id
            caption = (post.caption if post else "") or ""
            label = caption.splitlines()[0][:80] if caption else "Page post"
            channel = click.channel
        else:
            post_key = f"{click.channel}:{click.lot_id or ''}:{click.calendar_key or ''}"
            label = "Group share" if click.channel == CHANNEL_GROUP_SHARE else "Page post"
            channel = click.channel
        if post_key not in by_post:
            by_post[post_key] = {
                "facebook_post_id": click.facebook_post_id,
                "calendar_key": click.calendar_key or "",
                "channel": channel,
                "lot_id": click.lot_id,
                "sku": (lot.sku if lot else "") or "",
                "title": (lot.auction_title if lot else "") or "",
                "label": label,
                "clicks": 0,
            }
        by_post[post_key]["clicks"] += 1
    day_rows = [{"day": day, "clicks": by_day[day]} for day in sorted(by_day)]
    lot_rows = sorted(by_lot.values(), key=lambda row: (-int(row["clicks"]), str(row.get("sku") or "")))
    post_rows = sorted(by_post.values(), key=lambda row: (-int(row["clicks"]), str(row.get("label") or "")))
    return {
        "sale_id": sale_id,
        "total": len(clicks),
        "by_day": day_rows,
        "by_lot": lot_rows,
        "by_post": post_rows,
    }
