"""Per-account Facebook Page OAuth, preview, and approved Page posts."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.catalog_storage import resolve_catalog_storage_path
from app.config import cors_allow_origins, settings
from app.models import Account, EngagementSnapshot, FacebookPagePost, Lot, Photo, Sale, User
from app.services.ctseller import jpeg_bytes_for_ctseller_post
from app.services.ctbids_urls import CTBIDS_ITEM_URL, ctbids_sale_url
from app.services.facebook_caption import FOLLOW_HOOK_LINE, draft_facebook_caption
from app.services.facebook_crypto import seal_facebook_secret, unseal_facebook_secret
from app.services.meta_graph import (
    GRAPH_API_VERSION,
    MetaGraphError,
    create_page_feed_post,
    exchange_code_for_user_token,
    exchange_long_lived_user_token,
    get_me,
    list_user_pages,
    oauth_dialog_url,
    upload_unpublished_photo,
)
from app.services.photo_order import ordered_photos_for_listing
from app.services.photo_roles import photos_for_ctbids_listing
from app.services.roles import can_manage_integrations, is_owner

PACIFIC = ZoneInfo("America/Los_Angeles")
OAUTH_STATE_MINUTES = 15
MAX_PAGE_PHOTOS = 4
MAX_PAGE_POSTS = 80
SCHEDULE_MIN_LEAD = timedelta(minutes=10)
SCHEDULE_MAX_LEAD = timedelta(days=75)
FOLLOW_HOOK = FOLLOW_HOOK_LINE
_LOG = logging.getLogger(__name__)


def meta_oauth_redirect_uri() -> str:
    raw = (settings.meta_oauth_redirect_uri or "").strip()
    if raw:
        return raw
    return "http://localhost:8000/integrations/facebook/oauth/callback"


def public_web_origin() -> str:
    raw = (settings.public_web_origin or "").strip().rstrip("/")
    if raw:
        return raw
    extras = [x.strip().rstrip("/") for x in (settings.cors_origins or "").split(",") if x.strip()]
    if extras:
        return extras[0]
    origins = cors_allow_origins()
    return (origins[0] if origins else "http://localhost:3000").rstrip("/")


def require_meta_configured() -> None:
    if not settings.meta_configured:
        raise HTTPException(
            status_code=503,
            detail="Facebook Page posting is not configured (set META_APP_ID and META_APP_SECRET).",
        )


def encode_oauth_state(*, account_id: str, user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "purpose": "meta_oauth",
        "account_id": account_id,
        "user_id": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=OAUTH_STATE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_oauth_state(state: str) -> tuple[str, str]:
    try:
        payload = jwt.decode(
            state,
            settings.secret_key,
            algorithms=["HS256"],
            options={"require_exp": True, "verify_aud": False},
        )
    except JWTError as exc:
        raise HTTPException(status_code=400, detail="Facebook sign-in expired. Start Connect again.") from exc
    if payload.get("purpose") != "meta_oauth":
        raise HTTPException(status_code=400, detail="Invalid Facebook sign-in state.")
    account_id = str(payload.get("account_id") or "").strip()
    user_id = str(payload.get("user_id") or "").strip()
    if not account_id or not user_id:
        raise HTTPException(status_code=400, detail="Invalid Facebook sign-in state.")
    return account_id, user_id


def require_facebook_poster(current: User) -> None:
    """Connect, pick a Page, and post are account-owner actions — not catalogger."""
    if not can_manage_integrations(current.role):
        raise HTTPException(status_code=403, detail="Owner access required to post to Facebook")


def connection_status(account: Account) -> dict:
    page_id = str(getattr(account, "facebook_page_id", None) or "").strip()
    page_name = str(getattr(account, "facebook_page_name", None) or "").strip()
    page_token = unseal_facebook_secret(getattr(account, "facebook_page_token_enc", None))
    token_error = str(getattr(account, "facebook_token_error", None) or "").strip()
    configured = settings.meta_configured
    connected = bool(configured and page_id and page_token)
    # Page tokens from a long-lived user token typically do not expire. A stored
    # user token without a selected Page is "pick a Page", not reconnect. Only a
    # recorded Graph token error on an already-connected Page means reconnect.
    needs_reconnect = bool(connected and token_error)
    return {
        "configured": configured,
        "connected": connected,
        "needs_reconnect": needs_reconnect,
        "calendar_auto_approve": bool(getattr(account, "facebook_calendar_auto_approve", False)),
        "page_id": page_id or None,
        "page_name": page_name or None,
        "user_id": str(getattr(account, "facebook_user_id", None) or "").strip() or None,
        "connected_at": getattr(account, "facebook_connected_at", None),
        "token_error": token_error or None,
        "graph_api_version": GRAPH_API_VERSION,
        "oauth_scopes": ["pages_show_list", "pages_manage_posts", "pages_read_engagement"],
    }


def oauth_start_url(current: User) -> str:
    require_meta_configured()
    require_facebook_poster(current)
    state = encode_oauth_state(account_id=current.account_id, user_id=current.id)
    return oauth_dialog_url(
        app_id=settings.meta_app_id.strip(),
        redirect_uri=meta_oauth_redirect_uri(),
        state=state,
    )


def frontend_oauth_redirect(*, status: str, error: str = "", pick: bool = False) -> str:
    origin = public_web_origin()
    params: dict[str, str] = {"facebook": status}
    if error:
        params["facebook_error"] = error[:300]
    if pick:
        params["facebook"] = "pick"
    return f"{origin}/account?{urlencode(params)}"


def _account(db: Session, account_id: str) -> Account:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def _mark_token_error(db: Session, account: Account, message: str) -> None:
    account.facebook_token_error = (message or "Facebook token error")[:2000]
    db.add(account)
    db.commit()


def _clear_token_error(db: Session, account: Account) -> None:
    if getattr(account, "facebook_token_error", None):
        account.facebook_token_error = None
        db.add(account)


def complete_oauth(db: Session, *, code: str, state: str) -> str:
    require_meta_configured()
    account_id, user_id = decode_oauth_state(state)
    account = _account(db, account_id)
    user = db.query(User).filter(User.id == user_id, User.account_id == account_id).first()
    if not user or not is_owner(user):
        return frontend_oauth_redirect(status="error", error="Only an account owner can connect Facebook.")
    try:
        short = exchange_code_for_user_token(
            app_id=settings.meta_app_id.strip(),
            app_secret=settings.meta_app_secret.strip(),
            redirect_uri=meta_oauth_redirect_uri(),
            code=code,
        )
        short_token = str(short.get("access_token") or "").strip()
        if not short_token:
            raise MetaGraphError("Facebook did not return an access token")
        try:
            long_lived = exchange_long_lived_user_token(
                app_id=settings.meta_app_id.strip(),
                app_secret=settings.meta_app_secret.strip(),
                short_lived_token=short_token,
            )
            user_token = str(long_lived.get("access_token") or short_token).strip()
            expires_in = long_lived.get("expires_in")
        except MetaGraphError:
            user_token = short_token
            expires_in = short.get("expires_in")
        me = get_me(user_token)
        pages = list_user_pages(user_token)
    except MetaGraphError as exc:
        _LOG.warning("Facebook OAuth failed account=%s: %s", account_id, exc)
        return frontend_oauth_redirect(status="error", error=str(exc))

    account.facebook_user_id = str(me.get("id") or "")[:64] or None
    account.facebook_user_token_enc = seal_facebook_secret(user_token)
    try:
        seconds = int(expires_in) if expires_in is not None else 60 * 24 * 3600
    except (TypeError, ValueError):
        seconds = 60 * 24 * 3600
    account.facebook_user_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(seconds, 3600))
    account.facebook_token_error = None
    db.add(account)
    db.commit()

    postable = [p for p in pages if p.get("can_post")]
    if len(postable) == 1:
        _store_selected_page(db, account, postable[0])
        return frontend_oauth_redirect(status="connected")
    if not pages:
        return frontend_oauth_redirect(
            status="error",
            error="No Facebook Pages found. Add the owner as an admin of the Page and of the Meta app.",
        )
    return frontend_oauth_redirect(status="pick", pick=True)


def listed_pages(db: Session, account: Account) -> list[dict]:
    require_meta_configured()
    token = unseal_facebook_secret(getattr(account, "facebook_user_token_enc", None))
    if not token:
        raise HTTPException(status_code=400, detail="Connect Facebook first.")
    try:
        pages = list_user_pages(token)
    except MetaGraphError as exc:
        if exc.is_token_error:
            _mark_token_error(db, account, str(exc))
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    selected = str(getattr(account, "facebook_page_id", None) or "").strip()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "can_post": bool(row.get("can_post")),
            "selected": row["id"] == selected,
        }
        for row in pages
    ]


def _store_selected_page(db: Session, account: Account, page: dict) -> None:
    account.facebook_page_id = str(page["id"])[:64]
    account.facebook_page_name = str(page.get("name") or "")[:255]
    account.facebook_page_token_enc = seal_facebook_secret(str(page["access_token"]))
    account.facebook_connected_at = datetime.now(timezone.utc)
    account.facebook_token_error = None
    db.add(account)
    db.commit()


def select_page(db: Session, account: Account, page_id: str) -> dict:
    require_meta_configured()
    token = unseal_facebook_secret(getattr(account, "facebook_user_token_enc", None))
    if not token:
        raise HTTPException(status_code=400, detail="Connect Facebook first.")
    try:
        pages = list_user_pages(token)
    except MetaGraphError as exc:
        if exc.is_token_error:
            _mark_token_error(db, account, str(exc))
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    match = next((p for p in pages if p["id"] == page_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="That Page is not on this Facebook login.")
    if not match.get("can_post"):
        raise HTTPException(
            status_code=400,
            detail="This Facebook user cannot create posts on that Page.",
        )
    _store_selected_page(db, account, match)
    return connection_status(account)


def disconnect(db: Session, account: Account) -> dict:
    account.facebook_user_id = None
    account.facebook_user_token_enc = None
    account.facebook_user_token_expires_at = None
    account.facebook_page_id = None
    account.facebook_page_name = None
    account.facebook_page_token_enc = None
    account.facebook_connected_at = None
    account.facebook_token_error = None
    db.add(account)
    db.commit()
    return connection_status(account)


def _page_token(account: Account) -> tuple[str, str]:
    page_id = str(getattr(account, "facebook_page_id", None) or "").strip()
    token = unseal_facebook_secret(getattr(account, "facebook_page_token_enc", None))
    if not page_id or not token:
        raise HTTPException(
            status_code=400,
            detail="Connect a Facebook Page on Account settings before posting.",
        )
    return page_id, token


def ctbids_lot_url(lot: Lot, *, snapshot: EngagementSnapshot | None = None) -> str:
    if snapshot and (snapshot.item_url or "").strip():
        return snapshot.item_url.strip()
    sale_id = getattr(lot, "ctseller_sale_id", None)
    item_id = getattr(lot, "ctseller_item_id", None)
    if sale_id and item_id:
        return CTBIDS_ITEM_URL.format(sale_id=int(sale_id), item_id=int(item_id))
    return ""


def _latest_snapshot(db: Session, lot: Lot) -> EngagementSnapshot | None:
    item_id = getattr(lot, "ctseller_item_id", None)
    if item_id:
        row = (
            db.query(EngagementSnapshot)
            .filter(
                EngagementSnapshot.account_id == lot.account_id,
                EngagementSnapshot.ctseller_item_id == int(item_id),
            )
            .order_by(EngagementSnapshot.captured_at.desc())
            .first()
        )
        if row:
            return row
    sku = (lot.sku or "").strip()
    if not sku:
        return None
    return (
        db.query(EngagementSnapshot)
        .filter(
            EngagementSnapshot.account_id == lot.account_id,
            EngagementSnapshot.sku == sku,
        )
        .order_by(EngagementSnapshot.captured_at.desc())
        .first()
    )


def listing_photos_for_facebook(photos: list[Photo]) -> list[Photo]:
    """SKU-tape frames stay off the Page, same as CT Seller. Hero/listing order.

    Does not cap length — calendar rotation needs the full listing set so each
    slot can pick a different group of up to ``MAX_PAGE_PHOTOS``.
    """
    return ordered_photos_for_listing(photos_for_ctbids_listing(photos))


def rotate_listing_photos(photos: list[Photo], offset: int) -> list[Photo]:
    """Start the gallery at ``offset`` (wraps) so same-lot slots are not identical."""
    if not photos:
        return []
    start = int(offset or 0) % len(photos)
    rotated = photos[start:] + photos[:start]
    return rotated[:MAX_PAGE_PHOTOS]


def _photos_for_item(db: Session, lot: Lot, photo_ids: list[str] | None) -> list[Photo]:
    photos = _lot_photos(db, lot)
    if not photo_ids:
        return photos[:MAX_PAGE_PHOTOS]
    by_id = {p.id: p for p in photos}
    ordered = [by_id[pid] for pid in photo_ids if pid in by_id]
    return (ordered or photos)[:MAX_PAGE_PHOTOS]


def _lot_photos(db: Session, lot: Lot) -> list[Photo]:
    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot.id, Photo.account_id == lot.account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc())
        .all()
    )
    return listing_photos_for_facebook(list(photos) if photos else [])


def _existing_posts(db: Session, account_id: str, lot_id: str) -> list[FacebookPagePost]:
    return (
        db.query(FacebookPagePost)
        .filter(
            FacebookPagePost.account_id == account_id,
            FacebookPagePost.lot_id == lot_id,
            FacebookPagePost.status.in_(("posted", "scheduled")),
        )
        .order_by(FacebookPagePost.created_at.desc())
        .all()
    )


def suggest_schedule_slots(
    *,
    now: datetime | None = None,
    close_at: datetime | None = None,
) -> list[dict]:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    candidates: list[tuple[datetime, str]] = []
    if close_at is not None:
        close_pt = close_at.astimezone(PACIFIC) if close_at.tzinfo else close_at.replace(tzinfo=PACIFIC)
        morning = close_pt.replace(hour=9, minute=0, second=0, microsecond=0)
        candidates.append((morning - timedelta(days=3), "3 days before close, 9:00 AM PT"))
        candidates.append((morning - timedelta(days=2), "2 days before close, 9:00 AM PT"))
        candidates.append((morning, "Morning of close, 9:00 AM PT"))
    tomorrow = moment.astimezone(PACIFIC).replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if not any(abs((c[0] - tomorrow).total_seconds()) < 60 for c in candidates):
        candidates.append((tomorrow, "Tomorrow morning, 9:00 AM PT"))
    out: list[dict] = []
    seen: set[str] = set()
    for when, label in candidates:
        utc = when.astimezone(timezone.utc)
        if utc - moment < SCHEDULE_MIN_LEAD or utc - moment > SCHEDULE_MAX_LEAD:
            continue
        key = utc.isoformat()
        if key in seen:
            continue
        seen.add(key)
        out.append({"at": utc, "label": label})
    return out


def validate_scheduled_time(when: datetime, *, now: datetime | None = None) -> datetime:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    target = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    delta = target - moment
    if delta < SCHEDULE_MIN_LEAD:
        raise HTTPException(
            status_code=400,
            detail="Facebook requires scheduled posts at least 10 minutes from now.",
        )
    if delta > SCHEDULE_MAX_LEAD:
        raise HTTPException(
            status_code=400,
            detail="Facebook allows scheduling at most 75 days ahead.",
        )
    return target


def _sale_title(db: Session, lot: Lot) -> str:
    sale = db.query(Sale).filter(Sale.id == lot.sale_id).first()
    return (sale.name if sale else "") or ""


def preview_posts(
    db: Session,
    *,
    account: Account,
    lot_ids: list[str],
    include_follow_hook: bool,
) -> dict:
    require_meta_configured()
    status = connection_status(account)
    if not status["connected"]:
        raise HTTPException(
            status_code=400,
            detail="Connect a Facebook Page on Account settings before drafting a post.",
        )
    ids = [str(x).strip() for x in lot_ids if str(x).strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="Pick at least one lot.")
    if len(ids) > MAX_PAGE_POSTS:
        raise HTTPException(status_code=400, detail=f"Preview at most {MAX_PAGE_POSTS} lots at a time.")
    lots = (
        db.query(Lot)
        .filter(Lot.account_id == account.id, Lot.id.in_(ids))
        .all()
    )
    by_id = {lot.id: lot for lot in lots}
    missing = [lid for lid in ids if lid not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail="One or more lots were not found on this account.")

    close_times: list[datetime] = []
    drafts: list[dict] = []
    for lid in ids:
        lot = by_id[lid]
        snapshot = _latest_snapshot(db, lot)
        photos = _lot_photos(db, lot)[:MAX_PAGE_PHOTOS]
        item_url = ctbids_lot_url(lot, snapshot=snapshot)
        sale_url = ctbids_sale_url(
            sale_id=getattr(lot, "ctseller_sale_id", None),
            auction_url=(snapshot.auction_url if snapshot else "") or "",
            item_url=item_url,
        )
        close_at = snapshot.sale_ends_at if snapshot else None
        if close_at is not None:
            close_times.append(close_at)
        existing = _existing_posts(db, account.id, lot.id)
        error = ""
        if not photos:
            error = "This lot has no listing photos (SKU-tape frames are never posted)."
        caption = draft_facebook_caption(
            title=lot.auction_title or "",
            description=lot.professional_description or "",
            sku=lot.sku or "",
            item_url=item_url,
            close_at=close_at,
            include_follow_hook=include_follow_hook,
            current_bid=float(snapshot.price) if snapshot else None,
            watchers=int(snapshot.watchers) if snapshot else None,
            sale_title=(snapshot.sale_title if snapshot else None) or _sale_title(db, lot),
            sale_url=sale_url,
        )
        drafts.append(
            {
                "lot_id": lot.id,
                "sku": lot.sku,
                "title": lot.auction_title or "",
                "item_url": item_url,
                "sale_ends_at": close_at,
                "caption": caption,
                "photo_ids": [p.id for p in photos],
                "photo_count": len(photos),
                "already_posted": bool(existing),
                "existing_posts": [
                    {
                        "id": row.id,
                        "status": row.status,
                        "facebook_post_id": row.facebook_post_id,
                        "scheduled_publish_time": row.scheduled_publish_time,
                        "posted_at": row.posted_at,
                        "created_at": row.created_at,
                    }
                    for row in existing[:5]
                ],
                "error": error or None,
            }
        )
    close_at = min(close_times) if close_times else None
    return {
        **status,
        "include_follow_hook": include_follow_hook,
        "follow_hook_line": FOLLOW_HOOK,
        "schedule_suggestions": suggest_schedule_slots(close_at=close_at),
        "lots": drafts,
    }


def _photo_jpeg(photo: Photo) -> bytes | None:
    try:
        path = resolve_catalog_storage_path(photo.storage_path)
        if not path.is_file():
            return None
        return jpeg_bytes_for_ctseller_post(path)
    except (OSError, ValueError, FileNotFoundError):
        return None


def publish_posts(
    db: Session,
    *,
    account: Account,
    user: User,
    items: list[dict],
    include_follow_hook: bool,
    confirm: bool,
    force: bool,
    source: str,
    now: datetime | None = None,
    on_lot=None,
    on_result=None,
) -> list[dict]:
    require_meta_configured()
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Preview the post and click Post to Page. Nothing is sent without that.",
        )
    page_id, page_token = _page_token(account)
    if not items:
        raise HTTPException(status_code=400, detail="Pick at least one lot.")
    if len(items) > MAX_PAGE_POSTS:
        raise HTTPException(status_code=400, detail=f"Post at most {MAX_PAGE_POSTS} lots at a time.")

    results: list[dict] = []

    def _emit(row: dict) -> dict:
        results.append(row)
        if on_result is not None:
            on_result(row)
        return row

    for item in items:
        lot_id = str(item.get("lot_id") or "").strip()
        caption = str(item.get("caption") or "").strip()
        scheduled = item.get("scheduled_publish_time")
        calendar_key = str(item.get("calendar_key") or "").strip() or None
        if calendar_key:
            from app.services.facebook_calendar import calendar_key_aliases, migrate_calendar_key

            calendar_key = migrate_calendar_key(calendar_key) or None
        calendar_sale_id = item.get("calendar_sale_id")
        try:
            calendar_sale_id = int(calendar_sale_id) if calendar_sale_id is not None else None
        except (TypeError, ValueError):
            calendar_sale_id = None
        if on_lot is not None:
            on_lot(lot_id)
        lot = (
            db.query(Lot)
            .filter(Lot.id == lot_id, Lot.account_id == account.id)
            .first()
        )
        if not lot:
            _emit({"lot_id": lot_id, "calendar_key": calendar_key, "ok": False, "error": "Lot not found"})
            continue
        if not caption:
            _emit({"lot_id": lot_id, "calendar_key": calendar_key, "ok": False, "error": "Caption is empty"})
            continue
        cal_row = None
        if calendar_key:
            from app.services.facebook_calendar import calendar_key_aliases

            cal_row = (
                db.query(FacebookPagePost)
                .filter(
                    FacebookPagePost.account_id == account.id,
                    FacebookPagePost.calendar_key.in_(calendar_key_aliases(calendar_key)),
                )
                .order_by(FacebookPagePost.created_at.desc())
                .first()
            )
        existing = _existing_posts(db, account.id, lot.id)
        if cal_row and cal_row.status in {"posted", "scheduled"} and not force:
            _emit(
                {
                    "lot_id": lot_id,
                    "calendar_key": calendar_key,
                    "ok": False,
                    "skipped": True,
                    "error": "This Push-calendar slot is already scheduled or posted.",
                }
            )
            continue
        # Calendar slots on the same lot must not block each other. Lot/queue posts
        # still skip when this lot already has a posted/scheduled Page row.
        if not calendar_key:
            other_active = [row for row in existing if cal_row is None or row.id != cal_row.id]
            if other_active and not force:
                _emit(
                    {
                        "lot_id": lot_id,
                        "ok": False,
                        "skipped": True,
                        "error": "Already posted or scheduled to this Page. Confirm to post again.",
                    }
                )
                continue
        raw_photo_ids = item.get("photo_ids") or []
        if not isinstance(raw_photo_ids, list):
            raw_photo_ids = []
        photos = _photos_for_item(db, lot, [str(pid) for pid in raw_photo_ids if str(pid).strip()])
        if not photos:
            _emit(
                {
                    "lot_id": lot_id,
                    "calendar_key": calendar_key,
                    "ok": False,
                    "error": "This lot has no listing photos (SKU-tape frames are never posted).",
                }
            )
            continue
        scheduled_at = None
        scheduled_unix = None
        if scheduled is not None:
            scheduled_at = validate_scheduled_time(scheduled, now=now)
            scheduled_unix = int(scheduled_at.timestamp())

        if cal_row is not None and cal_row.status in {"failed", "cancelled"}:
            record = cal_row
            record.user_id = user.id
            record.lot_id = lot.id
            record.lot_ids_json = json.dumps([lot.id])
            record.photo_ids_json = json.dumps([p.id for p in photos])
            record.facebook_page_id = page_id
            record.caption = caption
            record.include_follow_hook = include_follow_hook
            record.source = (source or "lot")[:32]
            record.status = "failed"
            record.scheduled_publish_time = scheduled_at
            record.facebook_post_id = None
            record.posted_at = None
            record.error_message = None
            record.calendar_key = calendar_key
            record.calendar_sale_id = calendar_sale_id
        else:
            record = FacebookPagePost(
                account_id=account.id,
                user_id=user.id,
                lot_id=lot.id,
                lot_ids_json=json.dumps([lot.id]),
                photo_ids_json=json.dumps([p.id for p in photos]),
                facebook_page_id=page_id,
                caption=caption,
                include_follow_hook=include_follow_hook,
                source=(source or "lot")[:32],
                calendar_key=calendar_key,
                calendar_sale_id=calendar_sale_id,
                status="failed",
                scheduled_publish_time=scheduled_at,
            )
        db.add(record)
        db.flush()

        try:
            media_ids: list[str] = []
            for photo in photos:
                raw = _photo_jpeg(photo)
                if not raw:
                    continue
                name = (photo.original_name or "lot.jpg").rsplit(".", 1)[0][:60] + ".jpg"
                media_ids.append(
                    upload_unpublished_photo(
                        page_id=page_id,
                        page_token=page_token,
                        image_bytes=raw,
                        filename=name,
                    )
                )
            if not media_ids:
                raise MetaGraphError("Could not read any listing photos to upload.")
            post_id = create_page_feed_post(
                page_id=page_id,
                page_token=page_token,
                message=caption,
                media_fbids=media_ids,
                scheduled_unix=scheduled_unix,
            )
        except MetaGraphError as exc:
            record.error_message = str(exc)[:2000]
            record.status = "failed"
            db.add(record)
            db.commit()
            if exc.is_token_error:
                _mark_token_error(db, account, str(exc))
                _emit(
                    {
                        "lot_id": lot_id,
                        "calendar_key": calendar_key,
                        "ok": False,
                        "post_id": record.id,
                        "needs_reconnect": True,
                        "error": str(exc),
                    }
                )
                break
            _emit(
                {
                    "lot_id": lot_id,
                    "calendar_key": calendar_key,
                    "ok": False,
                    "post_id": record.id,
                    "error": str(exc),
                }
            )
            continue

        record.facebook_post_id = post_id
        record.status = "scheduled" if scheduled_unix else "posted"
        record.posted_at = None if scheduled_unix else datetime.now(timezone.utc)
        record.error_message = None
        _clear_token_error(db, account)
        db.add(record)
        db.commit()
        _emit(
            {
                "lot_id": lot_id,
                "calendar_key": calendar_key,
                "ok": True,
                "post_id": record.id,
                "facebook_post_id": post_id,
                "status": record.status,
                "scheduled_publish_time": record.scheduled_publish_time,
            }
        )
    return results


def list_posts(
    db: Session,
    *,
    account_id: str,
    lot_id: str | None = None,
    limit: int = 50,
) -> list[FacebookPagePost]:
    q = db.query(FacebookPagePost).filter(FacebookPagePost.account_id == account_id)
    if lot_id:
        q = q.filter(FacebookPagePost.lot_id == lot_id)
    return q.order_by(FacebookPagePost.created_at.desc()).limit(max(1, min(limit, 200))).all()
