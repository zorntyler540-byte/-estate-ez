"""Per-account Facebook Page OAuth, preview, and approved Page posts."""

from __future__ import annotations

import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from urllib.parse import urlencode

from fastapi import HTTPException
from jose import JWTError, jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalog_storage import resolve_catalog_storage_path
from app.config import cors_allow_origins, settings
from app.models import Account, EngagementSnapshot, FacebookOAuthState, FacebookPagePost, Lot, Photo, Sale, User
from app.services.ctseller import jpeg_bytes_for_ctseller_post
from app.services.ctbids_urls import CTBIDS_ITEM_URL, ctbids_sale_url
from app.services.facebook_caption import FOLLOW_HOOK_LINE, draft_captions, draft_facebook_caption, group_post_copy
from app.services.facebook_crypto import seal_facebook_secret, unseal_facebook_secret
from app.services.franchise_time import US_TIMEZONES, account_zone_name, to_local, zone_label
from app.services.meta_graph import (
    GRAPH_API_VERSION,
    MetaGraphError,
    create_page_feed_post,
    exchange_code_for_user_token,
    exchange_long_lived_user_token,
    get_instagram_account,
    get_me,
    list_user_pages,
    oauth_dialog_url,
    oauth_scopes,
    upload_unpublished_photo,
)
from app.services.photo_order import ordered_photos_for_listing
from app.services.photo_roles import photos_for_ctbids_listing
from app.services.roles import can_manage_integrations, is_owner

OAUTH_STATE_MINUTES = 15
OAUTH_COOKIE_NAME = "fb_oauth_jti"
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


def encode_oauth_state(*, account_id: str, user_id: str, jti: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "purpose": "meta_oauth",
        "account_id": account_id,
        "user_id": user_id,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=OAUTH_STATE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_oauth_state(state: str) -> tuple[str, str, str]:
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
    jti = str(payload.get("jti") or "").strip()
    if not account_id or not user_id or not jti:
        raise HTTPException(status_code=400, detail="Invalid Facebook sign-in state.")
    return account_id, user_id, jti


def issue_oauth_state(db: Session, *, account_id: str, user_id: str) -> tuple[str, str, int]:
    """Persist a one-time Connect nonce. Returns ``(state_jwt, jti, max_age_seconds)``."""
    jti = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    db.add(
        FacebookOAuthState(
            jti=jti,
            account_id=account_id,
            user_id=user_id,
            expires_at=now + timedelta(minutes=OAUTH_STATE_MINUTES),
        )
    )
    db.commit()
    return encode_oauth_state(account_id=account_id, user_id=user_id, jti=jti), jti, OAUTH_STATE_MINUTES * 60


def consume_oauth_state(db: Session, *, state: str, cookie_jti: str | None) -> tuple[str, str]:
    account_id, user_id, jti = decode_oauth_state(state)
    cookie = (cookie_jti or "").strip()
    if not cookie or not hmac.compare_digest(cookie, jti):
        raise HTTPException(
            status_code=400,
            detail="Facebook sign-in must be finished in the same browser that started Connect.",
        )
    row = db.query(FacebookOAuthState).filter(FacebookOAuthState.jti == jti).first()
    if row is None or row.used_at is not None:
        raise HTTPException(status_code=400, detail="Facebook sign-in expired. Start Connect again.")
    expires = row.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires is not None and expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Facebook sign-in expired. Start Connect again.")
    if row.account_id != account_id or row.user_id != user_id:
        raise HTTPException(status_code=400, detail="Invalid Facebook sign-in state.")
    row.used_at = datetime.now(timezone.utc)
    db.add(row)
    db.commit()
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
    from app.services.social_targets import instagram_enabled

    instagram_on = instagram_enabled()
    ig_id = str(getattr(account, "instagram_user_id", None) or "").strip()
    return {
        "configured": configured,
        "connected": connected,
        "needs_reconnect": needs_reconnect,
        "calendar_auto_approve": bool(getattr(account, "facebook_calendar_auto_approve", False)),
        "timezone": account_zone_name(account),
        "timezone_label": zone_label(account_zone_name(account)),
        "timezone_choices": [{"value": value, "label": label} for value, label in US_TIMEZONES],
        "page_id": page_id or None,
        "page_name": page_name or None,
        "user_id": str(getattr(account, "facebook_user_id", None) or "").strip() or None,
        "connected_at": getattr(account, "facebook_connected_at", None),
        "token_error": token_error or None,
        "graph_api_version": GRAPH_API_VERSION,
        "oauth_scopes": list(oauth_scopes(instagram=instagram_on)),
        "instagram_enabled": instagram_on,
        "instagram_connected": bool(connected and instagram_on and ig_id),
        "instagram_username": (str(getattr(account, "instagram_username", None) or "").strip() or None),
    }


def oauth_start_url(db: Session, current: User) -> tuple[str, str, int]:
    require_meta_configured()
    require_facebook_poster(current)
    state, jti, max_age = issue_oauth_state(db, account_id=current.account_id, user_id=current.id)
    from app.services.social_targets import instagram_enabled

    url = oauth_dialog_url(
        app_id=settings.meta_app_id.strip(),
        redirect_uri=meta_oauth_redirect_uri(),
        state=state,
        instagram=instagram_enabled(),
    )
    return url, jti, max_age


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


def complete_oauth(db: Session, *, code: str, state: str, cookie_jti: str | None = None) -> str:
    require_meta_configured()
    try:
        account_id, user_id = consume_oauth_state(db, state=state, cookie_jti=cookie_jti)
    except HTTPException as exc:
        return frontend_oauth_redirect(status="error", error=str(exc.detail))
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


def _linked_instagram(page_id: str, page_token: str) -> dict[str, str] | None:
    """IG professional account on the Page, or None (Instagram off, not linked, or no permission)."""
    from app.services.social_targets import instagram_enabled

    if not instagram_enabled():
        return None
    try:
        return get_instagram_account(page_id=page_id, page_token=page_token)
    except MetaGraphError as exc:
        _LOG.warning("Instagram lookup failed for Page %s: %s", page_id, exc)
        return None


def _store_selected_page(db: Session, account: Account, page: dict) -> None:
    linked = _linked_instagram(str(page["id"]), str(page["access_token"]))
    account.instagram_user_id = (linked or {}).get("id") or None
    account.instagram_username = ((linked or {}).get("username") or "")[:255] or None
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
    account.instagram_user_id = None
    account.instagram_username = None
    db.add(account)
    db.commit()
    return connection_status(account)


def refresh_instagram(db: Session, account: Account) -> dict:
    """Re-read which Instagram account is linked to the connected Page (after linking it in Meta)."""
    require_meta_configured()
    page_id, token = _page_token(account)
    db.commit()
    linked = _linked_instagram(page_id, token)
    account.instagram_user_id = (linked or {}).get("id") or None
    account.instagram_username = ((linked or {}).get("username") or "")[:255] or None
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
            FacebookPagePost.status.in_(("posted", "scheduled", "publishing")),
        )
        .order_by(FacebookPagePost.created_at.desc())
        .all()
    )


def suggest_schedule_slots(
    *,
    now: datetime | None = None,
    close_at: datetime | None = None,
    tz: str | None = None,
) -> list[dict]:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    label = zone_label(tz, at=moment)
    candidates: list[tuple[datetime, str]] = []
    if close_at is not None:
        morning = to_local(close_at, tz).replace(hour=9, minute=0, second=0, microsecond=0)
        candidates.append((morning - timedelta(days=3), f"3 days before close, 9:00 AM {label}"))
        candidates.append((morning - timedelta(days=2), f"2 days before close, 9:00 AM {label}"))
        candidates.append((morning, f"Morning of close, 9:00 AM {label}"))
    tomorrow = to_local(moment, tz).replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if not any(abs((c[0] - tomorrow).total_seconds()) < 60 for c in candidates):
        candidates.append((tomorrow, f"Tomorrow morning, 9:00 AM {label}"))
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

    from app.services.click_tracking import (
        CHANNEL_GROUP_SHARE,
        CHANNEL_PAGE_POST,
        apply_tracked_urls,
        tracked_pair,
    )

    tz = account_zone_name(account)
    close_times: list[datetime] = []
    staged: list[dict] = []
    for lid in ids:
        lot = by_id[lid]
        snapshot = _latest_snapshot(db, lot)
        photos = _lot_photos(db, lot)[:MAX_PAGE_PHOTOS]
        dest_item = ctbids_lot_url(lot, snapshot=snapshot)
        dest_sale = ctbids_sale_url(
            sale_id=getattr(lot, "ctseller_sale_id", None),
            auction_url=(snapshot.auction_url if snapshot else "") or "",
            item_url=dest_item,
        )
        _di, _ds, item_url, sale_url = tracked_pair(
            db,
            account_id=account.id,
            lot=lot,
            channel=CHANNEL_PAGE_POST,
            item_url=dest_item,
            sale_url=dest_sale,
        )
        _gdi, _gds, group_item_url, group_sale_url = tracked_pair(
            db,
            account_id=account.id,
            lot=lot,
            channel=CHANNEL_GROUP_SHARE,
            item_url=dest_item,
            sale_url=dest_sale,
        )
        close_at = snapshot.sale_ends_at if snapshot else None
        if close_at is not None:
            close_times.append(close_at)
        existing = _existing_posts(db, account.id, lot.id)
        staged.append(
            {
                "urls": (dest_item, dest_sale, item_url, sale_url, group_item_url, group_sale_url),
                "request": {
                    "title": lot.auction_title or "",
                    "description": lot.professional_description or "",
                    "sku": lot.sku or "",
                    "item_url": item_url,
                    "close_at": close_at,
                    "include_follow_hook": include_follow_hook,
                    "current_bid": float(snapshot.price) if snapshot else None,
                    "watchers": int(snapshot.watchers) if snapshot else None,
                    "sale_title": (snapshot.sale_title if snapshot else None) or _sale_title(db, lot),
                    "sale_url": sale_url,
                    "tz": tz,
                },
                "draft": {
                    "lot_id": lot.id,
                    "sku": lot.sku,
                    "title": lot.auction_title or "",
                    "item_url": item_url,
                    "sale_ends_at": close_at,
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
                    "error": None if photos else "This lot has no listing photos (SKU-tape frames are never posted).",
                },
            }
        )
    # Tracked links are stored; release the connection before the Gemini calls.
    db.commit()
    captions = draft_captions([row["request"] for row in staged], drafter=draft_facebook_caption)
    drafts: list[dict] = []
    for row, caption in zip(staged, captions):
        dest_item, dest_sale, item_url, sale_url, group_item_url, group_sale_url = row["urls"]
        caption = apply_tracked_urls(
            caption,
            dest_item=dest_item,
            dest_sale=dest_sale,
            short_item=item_url,
            short_sale=sale_url,
        )
        group_caption = group_post_copy(
            title=row["request"]["title"],
            description=row["request"]["description"],
            caption=caption,
            item_url=group_item_url,
            close_at=row["request"]["close_at"],
            sale_url=group_sale_url,
            tz=tz,
        )
        group_caption = apply_tracked_urls(
            group_caption,
            dest_item=dest_item,
            dest_sale=dest_sale,
            short_item=group_item_url,
            short_sale=group_sale_url,
        )
        drafts.append({**row["draft"], "caption": caption, "group_caption": group_caption})
    close_at = min(close_times) if close_times else None
    db.commit()
    return {
        **status,
        "include_follow_hook": include_follow_hook,
        "follow_hook_line": FOLLOW_HOOK,
        "schedule_suggestions": suggest_schedule_slots(close_at=close_at, tz=account_zone_name(account)),
        "lots": drafts,
    }


class _PhotoRef(NamedTuple):
    """Detached photo fields so Graph uploads never lazy-load from the session."""

    id: str
    storage_path: str | None
    original_name: str | None


def _photo_jpeg(photo: Photo | _PhotoRef) -> bytes | None:
    try:
        path = resolve_catalog_storage_path(photo.storage_path)
        if not path.is_file():
            return None
        return jpeg_bytes_for_ctseller_post(path)
    except (OSError, ValueError, FileNotFoundError):
        return None


def check_publish_request(*, account: Account, items: list[dict], confirm: bool) -> tuple[str, str]:
    """Request-time checks so a bad publish is a 400, not a failed background batch."""
    require_meta_configured()
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Preview the post and click Post to Page. Nothing is sent without that.",
        )
    page = _page_token(account)
    if not items:
        raise HTTPException(status_code=400, detail="Pick at least one lot.")
    if len(items) > MAX_PAGE_POSTS:
        raise HTTPException(status_code=400, detail=f"Post at most {MAX_PAGE_POSTS} lots at a time.")
    return page


def _item_target(item: dict) -> str:
    from app.services.social_targets import PUBLISH_TARGET_FACEBOOK_PAGE

    return str(item.get("publish_target") or "").strip() or PUBLISH_TARGET_FACEBOOK_PAGE


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# A reserved Instagram / Stories slot that the queue reaches this late is not posted.
DEFERRED_MAX_LATE = timedelta(hours=2)
DEFERRED_MISSED_NOTE = (
    "The publisher did not reach this slot within 2 hours of its time, so it was not posted. "
    "Approve it again or use Post now."
)
UNKNOWN_OUTCOME_NOTE = (
    "Facebook did not answer, so this may have posted. Check the Page before posting this slot again."
)


def publish_item(
    db: Session,
    *,
    account: Account,
    user_id: str,
    page_id: str,
    page_token: str,
    item: dict,
    include_follow_hook: bool,
    force: bool,
    source: str,
    now: datetime | None = None,
    state: dict | None = None,
    persist_state=None,
    on_claimed=None,
) -> dict:
    """Publish one item to its target. Never holds a DB transaction during a Graph call.

    Result keys beyond the API row: ``retryable`` (safe to try again later),
    ``deferred_until`` (Instagram / Stories slot reserved; publish at that time), and
    ``needs_reconnect``. ``state`` carries Graph progress across retries; set
    ``state["phase"] = "reserved"`` to publish a slot reserved earlier.
    """
    from app.services.click_tracking import (
        CHANNEL_PAGE_POST,
        bind_links_to_post,
        rewrite_copy_with_tracked_links,
    )
    from app.services.facebook_calendar import (
        AUTO_APPROVE_MAX_ATTEMPTS,
        PINNED_STATUSES,
        auto_retry_backoff,
        calendar_row_for_key,
        migrate_calendar_key,
    )
    from app.services.social_targets import (
        DEFERRED_TARGETS,
        IG_MAX_CAROUSEL,
        PUBLISH_TARGET_FACEBOOK_PAGE,
        PUBLISH_TARGET_INSTAGRAM_FEED,
        PUBLISHABLE_TARGETS,
        TargetResult,
        fit_instagram_feed,
        instagram_user_id,
        publish_instagram,
        publish_stories,
        stage_public_jpeg,
    )

    state = state if state is not None else {}

    def persist() -> None:
        if persist_state is not None:
            persist_state(state)

    account_id = account.id
    ig_user_id = instagram_user_id(account)
    moment = now or datetime.now(timezone.utc)
    lot_id = str(item.get("lot_id") or "").strip()
    caption = str(item.get("caption") or "").strip()
    scheduled = item.get("scheduled_publish_time")
    if isinstance(scheduled, str):
        scheduled = datetime.fromisoformat(scheduled) if scheduled.strip() else None
    calendar_key = migrate_calendar_key(str(item.get("calendar_key") or "").strip()) or None
    calendar_sale_id = item.get("calendar_sale_id")
    try:
        calendar_sale_id = int(calendar_sale_id) if calendar_sale_id is not None else None
    except (TypeError, ValueError):
        calendar_sale_id = None
    target = _item_target(item)
    reserved_phase = state.get("phase") == "reserved"

    def out(**fields) -> dict:
        return {"lot_id": lot_id, "calendar_key": calendar_key, "publish_target": target, **fields}

    if target not in PUBLISHABLE_TARGETS:
        return out(ok=False, skipped=True, error="This slot is copy-only; nothing is posted automatically.")
    if target == PUBLISH_TARGET_INSTAGRAM_FEED and not ig_user_id:
        return out(
            ok=False,
            error="Link an Instagram professional account to the Facebook Page before posting Instagram slots.",
        )
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.account_id == account_id).first()
    if not lot:
        return out(ok=False, error="Lot not found")
    if not caption:
        return out(ok=False, error="Caption is empty")
    cal_row = calendar_row_for_key(db, account_id, calendar_key) if calendar_key else None
    if reserved_phase:
        if cal_row is None or cal_row.status not in ("scheduled", "failed") or cal_row.facebook_post_id:
            cancelled = cal_row is not None and cal_row.status == "cancelled"
            return out(
                ok=False,
                skipped=True,
                error="Owner unapproved this slot." if cancelled else "This slot is no longer reserved.",
            )
        when = _as_aware_utc(cal_row.scheduled_publish_time)
        if when is not None and moment - when > DEFERRED_MAX_LATE:
            cal_row.status = "failed"
            cal_row.error_message = DEFERRED_MISSED_NOTE
            cal_row.review_note = DEFERRED_MISSED_NOTE
            cal_row.attempt_count = max(int(cal_row.attempt_count or 0), AUTO_APPROVE_MAX_ATTEMPTS)
            db.add(cal_row)
            db.commit()
            return out(ok=False, post_id=cal_row.id, error=DEFERRED_MISSED_NOTE)
    elif cal_row is not None and cal_row.status in PINNED_STATUSES:
        # A calendar slot posts once. ``force`` only re-posts plain lot posts.
        return out(ok=False, skipped=True, error="This Push-calendar slot is already scheduled or posted.")
    if not calendar_key:
        existing = _existing_posts(db, account_id, lot.id)
        if existing and not force:
            return out(
                ok=False,
                skipped=True,
                error="Already posted or scheduled to this Page. Confirm to post again.",
            )
    raw_photo_ids = item.get("photo_ids") or []
    if not isinstance(raw_photo_ids, list):
        raw_photo_ids = []
    photos = _photos_for_item(db, lot, [str(pid) for pid in raw_photo_ids if str(pid).strip()])
    if not photos:
        return out(ok=False, error="This lot has no listing photos (SKU-tape frames are never posted).")
    scheduled_at = None
    scheduled_unix = None
    if scheduled is not None and not reserved_phase:
        try:
            scheduled_at = validate_scheduled_time(scheduled, now=now)
        except HTTPException as exc:
            return out(ok=False, skipped=True, error=f"{exc.detail} Use Post now to publish immediately.")
        scheduled_unix = int(scheduled_at.timestamp())
    # Instagram / Stories approved ahead of time: pin the slot now, post at slot time.
    reserve = target in DEFERRED_TARGETS and scheduled_at is not None

    photo_refs = [_PhotoRef(p.id, p.storage_path, p.original_name) for p in photos]
    if reserved_phase:
        won = (
            db.query(FacebookPagePost)
            .filter(
                FacebookPagePost.id == cal_row.id,
                FacebookPagePost.status == cal_row.status,
                FacebookPagePost.facebook_post_id.is_(None),
            )
            .update({"status": "publishing", "claimed_at": moment}, synchronize_session=False)
        )
        if won != 1:
            db.rollback()
            return out(ok=False, skipped=True, error="This Push-calendar slot is already being posted.")
        record_id = cal_row.id
        caption = cal_row.caption or caption
        db.commit()
    else:
        fields = {
            "user_id": user_id,
            "lot_id": lot.id,
            "lot_ids_json": json.dumps([lot.id]),
            "photo_ids_json": json.dumps([ref.id for ref in photo_refs]),
            "facebook_page_id": page_id,
            "caption": caption,
            "include_follow_hook": include_follow_hook,
            "source": (source or "lot")[:32],
            "calendar_key": calendar_key,
            "calendar_sale_id": calendar_sale_id,
            "publish_target": target,
            "status": "scheduled" if reserve else "publishing",
            "claimed_at": None if reserve else moment,
            "scheduled_publish_time": scheduled_at,
            "facebook_post_id": None,
            "instagram_media_id": None,
            "posted_at": None,
            "error_message": None,
        }
        if reserve:
            fields.update({"review_note": None, "attempt_count": 0, "next_attempt_at": None})
        if cal_row is not None:
            # Compare-and-set so two publishers cannot both claim a failed/cancelled slot.
            won = (
                db.query(FacebookPagePost)
                .filter(FacebookPagePost.id == cal_row.id, FacebookPagePost.status == cal_row.status)
                .update(fields, synchronize_session=False)
            )
            if won != 1:
                db.rollback()
                return out(ok=False, skipped=True, error="This Push-calendar slot is already being posted.")
            db.commit()
            record_id = cal_row.id
        else:
            record = FacebookPagePost(account_id=account_id, **fields)
            db.add(record)
            try:
                db.flush()
                record_id = record.id
                db.commit()
            except IntegrityError:
                db.rollback()
                return out(ok=False, skipped=True, error="This Push-calendar slot is already being posted.")
        try:
            caption = rewrite_copy_with_tracked_links(
                db,
                account_id=account_id,
                lot=lot,
                text=caption,
                channel=CHANNEL_PAGE_POST,
                calendar_key=calendar_key or "",
                facebook_post_id=record_id,
            )
            bind_links_to_post(
                db,
                account_id=account_id,
                lot_id=lot_id,
                channel=CHANNEL_PAGE_POST,
                calendar_key=calendar_key or "",
                facebook_post_id=record_id,
            )
            db.query(FacebookPagePost).filter(FacebookPagePost.id == record_id).update(
                {"caption": caption}, synchronize_session=False
            )
            db.commit()
        except Exception:
            db.rollback()
            _LOG.exception("Tracked links failed for Facebook post %s; posting untracked URLs", record_id)
    if on_claimed is not None:
        on_claimed(record_id)
    if reserve:
        return out(
            ok=True,
            post_id=record_id,
            status="scheduled",
            scheduled_publish_time=scheduled_at,
            deferred_until=scheduled_at,
        )

    # The claim and links are committed, so the session holds no connection here.
    # Nothing below may touch an ORM attribute until the Graph calls finish.
    error: MetaGraphError | None = None
    result: TargetResult | None = None
    try:
        jpegs = [(ref, raw) for ref in photo_refs if (raw := _photo_jpeg(ref))]
        if not jpegs:
            raise MetaGraphError("Could not read any listing photos to upload.")
        if target == PUBLISH_TARGET_FACEBOOK_PAGE:
            media_ids = [
                upload_unpublished_photo(
                    page_id=page_id,
                    page_token=page_token,
                    image_bytes=raw,
                    filename=(ref.original_name or "lot.jpg").rsplit(".", 1)[0][:60] + ".jpg",
                )
                for ref, raw in jpegs
            ]
            result = TargetResult(
                create_page_feed_post(
                    page_id=page_id,
                    page_token=page_token,
                    message=caption,
                    media_fbids=media_ids,
                    scheduled_unix=scheduled_unix,
                ),
                None,
            )
        elif target == PUBLISH_TARGET_INSTAGRAM_FEED:
            media = publish_instagram(
                ig_user_id=str(ig_user_id),
                page_token=page_token,
                stage_urls=lambda: [
                    stage_public_jpeg(
                        account_id=account_id,
                        record_id=record_id,
                        photo_id=ref.id,
                        jpeg=fit_instagram_feed(raw),
                        kind="feed",
                    )
                    for ref, raw in jpegs[:IG_MAX_CAROUSEL]
                ],
                caption=caption,
                story=False,
                state=state.setdefault("instagram", {}),
                persist=persist,
            )
            result = TargetResult(None, media)
        else:
            ref, raw = jpegs[0]
            result = publish_stories(
                page_id=page_id,
                page_token=page_token,
                ig_user_id=ig_user_id,
                jpeg=raw,
                filename=(ref.original_name or "lot.jpg").rsplit(".", 1)[0][:60] + ".jpg",
                stage_url=lambda: stage_public_jpeg(
                    account_id=account_id, record_id=record_id, photo_id=ref.id, jpeg=raw, kind="story"
                ),
                state=state,
                persist=persist,
            )
    except MetaGraphError as exc:
        error = exc
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("Social post crashed record=%s target=%s", record_id, target)
        error = MetaGraphError(f"Post failed: {exc}")

    record = db.get(FacebookPagePost, record_id)
    if error is not None:
        attempts = int(record.attempt_count or 0) + 1
        unknown = bool(error.unknown_outcome)
        record.status = "failed"
        record.claimed_at = None
        record.error_message = (UNKNOWN_OUTCOME_NOTE if unknown else str(error))[:2000]
        record.attempt_count = max(attempts, AUTO_APPROVE_MAX_ATTEMPTS) if unknown else attempts
        record.next_attempt_at = moment + auto_retry_backoff(attempts)
        if unknown:
            record.review_note = UNKNOWN_OUTCOME_NOTE
        elif calendar_key and attempts >= AUTO_APPROVE_MAX_ATTEMPTS:
            record.review_note = (
                f"Stopped retrying after {attempts} failed attempts. Fix the error, then Approve again."
            )
        db.add(record)
        db.commit()
        if error.is_token_error:
            _mark_token_error(db, account, str(error))
            return out(ok=False, post_id=record_id, needs_reconnect=True, error=str(error))
        return out(
            ok=False,
            post_id=record_id,
            error=record.error_message,
            retryable=bool(error.is_transient and not unknown),
        )

    record.facebook_post_id = result.post_id
    record.instagram_media_id = result.instagram_media_id
    record.status = "scheduled" if scheduled_unix and target == PUBLISH_TARGET_FACEBOOK_PAGE else "posted"
    record.posted_at = None if record.status == "scheduled" else datetime.now(timezone.utc)
    record.claimed_at = None
    record.error_message = None
    record.review_note = result.note
    record.attempt_count = 0
    record.next_attempt_at = None
    _clear_token_error(db, account)
    db.add(record)
    db.commit()
    return out(
        ok=True,
        post_id=record_id,
        facebook_post_id=result.post_id or result.instagram_media_id,
        instagram_media_id=result.instagram_media_id,
        status=record.status,
        scheduled_publish_time=record.scheduled_publish_time,
        note=result.note,
    )


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
    """Publish items in order in this thread. The publish queue calls ``publish_item`` per job."""
    page_id, page_token = check_publish_request(account=account, items=items, confirm=confirm)
    user_id = user.id
    from app.services.facebook_calendar import migrate_calendar_key

    results: list[dict] = []

    def _emit(row: dict) -> dict:
        results.append(row)
        if on_result is not None:
            on_result(row)
        return row

    for index, item in enumerate(items):
        if on_lot is not None:
            on_lot(str(item.get("lot_id") or "").strip())
        row = publish_item(
            db,
            account=account,
            user_id=user_id,
            page_id=page_id,
            page_token=page_token,
            item=item,
            include_follow_hook=include_follow_hook,
            force=force,
            source=source,
            now=now,
        )
        _emit(row)
        if row.get("needs_reconnect"):
            for rest in items[index + 1 :]:
                _emit(
                    {
                        "lot_id": str(rest.get("lot_id") or ""),
                        "calendar_key": migrate_calendar_key(str(rest.get("calendar_key") or "")) or None,
                        "ok": False,
                        "skipped": True,
                        "needs_reconnect": True,
                        "error": "Not sent: reconnect the Facebook Page first.",
                    }
                )
            break
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
