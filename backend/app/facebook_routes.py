"""Facebook Page OAuth and approved feed posts (not Marketplace, Groups, or Instagram)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import Account, EngagementSnapshot, FacebookPagePost, Lot, Sale, User
from app.schemas import (
    FacebookCalendarApproveIn,
    FacebookCalendarCancelIn,
    FacebookCalendarPreviewIn,
    FacebookClickReportOut,
    FacebookGroupsOut,
    FacebookOAuthStartOut,
    FacebookPageChoiceOut,
    FacebookPagePostOut,
    FacebookPreviewIn,
    FacebookPreviewOut,
    FacebookPublishIn,
    FacebookPublishOut,
    FacebookPublishResultOut,
    FacebookSelectPageIn,
    FacebookSettingsIn,
    FacebookStatusOut,
)
from app import facebook_post_tasks
from app.services.facebook_calendar import (
    CALENDAR_SOURCE,
    calendar_preview,
    calendar_publish_items,
    cancel_calendar_slots,
    sync_facebook_calendar_after_snapshot,
)
from app.config import settings
from app.services.facebook_page import (
    OAUTH_COOKIE_NAME,
    complete_oauth,
    connection_status,
    disconnect,
    frontend_oauth_redirect,
    list_posts,
    listed_pages,
    oauth_start_url,
    preview_posts,
    publish_posts,
    require_facebook_poster,
    select_page,
)
from app.services.facebook_groups import sale_facebook_groups_payload
from app.services.roles import can_manage_integrations, is_owner
from app import audience_tasks

router = APIRouter()


def _account(db: Session, current: User) -> Account:
    account = db.query(Account).filter(Account.id == current.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def _status_out(account: Account) -> FacebookStatusOut:
    return FacebookStatusOut.model_validate(connection_status(account))


def _post_out(row: FacebookPagePost) -> FacebookPagePostOut:
    try:
        lot_ids = json.loads(row.lot_ids_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        lot_ids = []
    try:
        photo_ids = json.loads(row.photo_ids_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        photo_ids = []
    if not isinstance(lot_ids, list):
        lot_ids = []
    if not isinstance(photo_ids, list):
        photo_ids = []
    return FacebookPagePostOut(
        id=row.id,
        lot_id=row.lot_id,
        lot_ids=[str(x) for x in lot_ids],
        photo_ids=[str(x) for x in photo_ids],
        facebook_page_id=row.facebook_page_id or "",
        facebook_post_id=row.facebook_post_id,
        caption=row.caption or "",
        include_follow_hook=bool(row.include_follow_hook),
        source=row.source or "lot",
        status=row.status,
        scheduled_publish_time=row.scheduled_publish_time,
        posted_at=row.posted_at,
        error_message=row.error_message,
        created_at=row.created_at,
    )


@router.get("/integrations/facebook/status", response_model=FacebookStatusOut)
def facebook_status(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FacebookStatusOut:
    return _status_out(_account(db, current))


@router.get("/integrations/facebook/oauth/start", response_model=FacebookOAuthStartOut)
def facebook_oauth_start(
    response: Response,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FacebookOAuthStartOut:
    url, jti, max_age = oauth_start_url(db, current)
    # Production web and API are different Railway sites; Lax cookies from a
    # cross-site fetch are dropped. None+Secure lets the Connect cookie stick
    # so the Facebook callback still binds to the owner who started it.
    response.set_cookie(
        key=OAUTH_COOKIE_NAME,
        value=jti,
        max_age=max_age,
        httponly=True,
        secure=settings.is_production,
        samesite="none" if settings.is_production else "lax",
        path="/integrations/facebook/oauth",
    )
    return FacebookOAuthStartOut(url=url)


@router.get("/integrations/facebook/oauth/callback")
def facebook_oauth_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    if error:
        detail = (error_description or error or "Facebook sign-in was cancelled.").strip()
        redirect = RedirectResponse(frontend_oauth_redirect(status="error", error=detail), status_code=302)
        redirect.delete_cookie(OAUTH_COOKIE_NAME, path="/integrations/facebook/oauth")
        return redirect
    if not code or not state:
        redirect = RedirectResponse(
            frontend_oauth_redirect(status="error", error="Facebook did not return an authorization code."),
            status_code=302,
        )
        redirect.delete_cookie(OAUTH_COOKIE_NAME, path="/integrations/facebook/oauth")
        return redirect
    url = complete_oauth(
        db,
        code=code,
        state=state,
        cookie_jti=request.cookies.get(OAUTH_COOKIE_NAME),
    )
    redirect = RedirectResponse(url, status_code=302)
    redirect.delete_cookie(OAUTH_COOKIE_NAME, path="/integrations/facebook/oauth")
    return redirect


@router.get("/integrations/facebook/pages", response_model=list[FacebookPageChoiceOut])
def facebook_list_pages(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[FacebookPageChoiceOut]:
    if not can_manage_integrations(current.role):
        raise HTTPException(status_code=403, detail="Owner access required")
    pages = listed_pages(db, _account(db, current))
    return [FacebookPageChoiceOut.model_validate(row) for row in pages]


@router.post("/integrations/facebook/pages/select", response_model=FacebookStatusOut)
def facebook_select_page(
    payload: FacebookSelectPageIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FacebookStatusOut:
    if not is_owner(current):
        raise HTTPException(status_code=403, detail="Owner access required")
    return FacebookStatusOut.model_validate(select_page(db, _account(db, current), payload.page_id.strip()))


@router.delete("/integrations/facebook/connection", response_model=FacebookStatusOut)
def facebook_disconnect(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FacebookStatusOut:
    if not is_owner(current):
        raise HTTPException(status_code=403, detail="Owner access required")
    return FacebookStatusOut.model_validate(disconnect(db, _account(db, current)))


@router.patch("/integrations/facebook/settings", response_model=FacebookStatusOut)
def facebook_patch_settings(
    payload: FacebookSettingsIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FacebookStatusOut:
    require_facebook_poster(current)
    account = _account(db, current)
    account.facebook_calendar_auto_approve = bool(payload.calendar_auto_approve)
    db.add(account)
    db.commit()
    db.refresh(account)
    if account.facebook_calendar_auto_approve:
        sale_ids = {
            int(row[0])
            for row in db.query(EngagementSnapshot.ctseller_sale_id)
            .filter(EngagementSnapshot.account_id == account.id)
            .distinct()
            .all()
        }
        for sale_id in sale_ids:
            try:
                sync_facebook_calendar_after_snapshot(
                    db, account.id, sale_id, publish_new_slots=True
                )
            except Exception:
                continue
    return _status_out(account)


@router.post("/facebook/posts/preview", response_model=FacebookPreviewOut)
def facebook_preview(
    payload: FacebookPreviewIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FacebookPreviewOut:
    require_facebook_poster(current)
    data = preview_posts(
        db,
        account=_account(db, current),
        lot_ids=payload.lot_ids,
        include_follow_hook=payload.include_follow_hook,
    )
    return FacebookPreviewOut.model_validate(data)


def _publish_out(
    *,
    results: list[dict],
    queued: bool = False,
    batch_id: str | None = None,
    active: bool = False,
    total: int = 0,
    done: int = 0,
    posted: int = 0,
    failed: int = 0,
) -> FacebookPublishOut:
    return FacebookPublishOut(
        queued=queued,
        batch_id=batch_id,
        active=active,
        total=total,
        done=done,
        posted=posted,
        failed=failed,
        results=[FacebookPublishResultOut.model_validate(row) for row in results],
    )


@router.post("/facebook/posts", response_model=FacebookPublishOut)
def facebook_publish(
    payload: FacebookPublishIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FacebookPublishOut:
    require_facebook_poster(current)
    items = [item.model_dump() for item in payload.items]
    if len(items) > facebook_post_tasks.SYNC_LOT_LIMIT:
        batch_id = facebook_post_tasks.start_facebook_posts_in_background(
            account_id=current.account_id,
            user_id=current.id,
            items=items,
            include_follow_hook=payload.include_follow_hook,
            confirm=payload.confirm,
            force=payload.force,
            source=payload.source or "lot",
        )
        row = facebook_post_tasks.facebook_post_batch_status(batch_id, current.account_id) or {}
        return _publish_out(
            results=list(row.get("results") or []),
            queued=True,
            batch_id=batch_id,
            active=bool(row.get("active", True)),
            total=int(row.get("total") or len(items)),
            done=int(row.get("done") or 0),
            posted=int(row.get("posted") or 0),
            failed=int(row.get("failed") or 0),
        )
    results = publish_posts(
        db,
        account=_account(db, current),
        user=current,
        items=items,
        include_follow_hook=payload.include_follow_hook,
        confirm=payload.confirm,
        force=payload.force,
        source=payload.source or "lot",
    )
    posted = sum(1 for row in results if row.get("ok"))
    failed = sum(1 for row in results if not row.get("ok") and not row.get("skipped"))
    return _publish_out(
        results=results,
        queued=False,
        active=False,
        total=len(items),
        done=len(results),
        posted=posted,
        failed=failed,
    )


@router.get("/facebook/posts/batches/{batch_id}", response_model=FacebookPublishOut)
def facebook_publish_batch(
    batch_id: str,
    current: User = Depends(get_current_user),
) -> FacebookPublishOut:
    require_facebook_poster(current)
    row = facebook_post_tasks.facebook_post_batch_status(batch_id.strip(), current.account_id)
    if not row:
        raise HTTPException(status_code=404, detail="Facebook post batch not found")
    return _publish_out(
        results=list(row.get("results") or []),
        queued=True,
        batch_id=batch_id.strip(),
        active=bool(row.get("active")),
        total=int(row.get("total") or 0),
        done=int(row.get("done") or 0),
        posted=int(row.get("posted") or 0),
        failed=int(row.get("failed") or 0),
    )


@router.post(
    "/engagement/sales/{ctseller_sale_id}/calendar/facebook/preview",
    response_model=FacebookPreviewOut,
)
def facebook_preview_calendar(
    ctseller_sale_id: int,
    payload: FacebookCalendarPreviewIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FacebookPreviewOut:
    require_facebook_poster(current)
    data = calendar_preview(
        db,
        account=_account(db, current),
        ctseller_sale_id=int(ctseller_sale_id),
        slot_keys=list(payload.slot_keys or []),
        include_follow_hook=payload.include_follow_hook,
    )
    return FacebookPreviewOut.model_validate(data)


@router.post(
    "/engagement/sales/{ctseller_sale_id}/calendar/facebook/cancel",
    response_model=FacebookPublishOut,
)
def facebook_cancel_calendar(
    ctseller_sale_id: int,
    payload: FacebookCalendarCancelIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FacebookPublishOut:
    require_facebook_poster(current)
    results = cancel_calendar_slots(
        db,
        account=_account(db, current),
        ctseller_sale_id=int(ctseller_sale_id),
        slot_keys=list(payload.slot_keys or []),
    )
    posted = sum(1 for row in results if row.get("ok"))
    failed = sum(1 for row in results if not row.get("ok") and not row.get("skipped"))
    return _publish_out(
        results=results,
        queued=False,
        active=False,
        total=len(results),
        done=len(results),
        posted=posted,
        failed=failed,
    )


@router.post(
    "/engagement/sales/{ctseller_sale_id}/calendar/facebook",
    response_model=FacebookPublishOut,
)
def facebook_approve_calendar(
    ctseller_sale_id: int,
    payload: FacebookCalendarApproveIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FacebookPublishOut:
    require_facebook_poster(current)
    account = _account(db, current)
    publishable, skipped = calendar_publish_items(
        db,
        account=account,
        ctseller_sale_id=int(ctseller_sale_id),
        slot_keys=list(payload.slot_keys or []),
        captions=dict(payload.captions or {}),
        include_follow_hook=payload.include_follow_hook,
        post_now=bool(payload.post_now),
    )
    if not publishable:
        failed = sum(1 for row in skipped if not row.get("ok") and not row.get("skipped"))
        return _publish_out(
            results=skipped,
            queued=False,
            total=len(skipped),
            done=len(skipped),
            failed=failed,
        )
    if len(publishable) > facebook_post_tasks.SYNC_LOT_LIMIT:
        batch_id = facebook_post_tasks.start_facebook_posts_in_background(
            account_id=current.account_id,
            user_id=current.id,
            items=publishable,
            include_follow_hook=payload.include_follow_hook,
            confirm=payload.confirm,
            force=payload.force,
            source=CALENDAR_SOURCE,
        )
        row = facebook_post_tasks.facebook_post_batch_status(batch_id, current.account_id) or {}
        return _publish_out(
            results=skipped + list(row.get("results") or []),
            queued=True,
            batch_id=batch_id,
            active=bool(row.get("active", True)),
            total=int(row.get("total") or len(publishable)) + len(skipped),
            done=int(row.get("done") or 0) + len(skipped),
            posted=int(row.get("posted") or 0),
            failed=int(row.get("failed") or 0),
        )
    results = publish_posts(
        db,
        account=account,
        user=current,
        items=publishable,
        include_follow_hook=payload.include_follow_hook,
        confirm=payload.confirm,
        force=payload.force,
        source=CALENDAR_SOURCE,
    )
    combined = skipped + results
    posted = sum(1 for row in combined if row.get("ok"))
    failed = sum(1 for row in combined if not row.get("ok") and not row.get("skipped"))
    return _publish_out(
        results=combined,
        queued=False,
        active=False,
        total=len(combined),
        done=len(combined),
        posted=posted,
        failed=failed,
    )


@router.get("/sales/{sale_id}/facebook-groups", response_model=FacebookGroupsOut)
def sale_facebook_groups(
    sale_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FacebookGroupsOut:
    """Real Gemini/Search group hits for this sale, plus Facebook search-link fallbacks.

    Does not call the Graph API. Hunt itself is ``POST /sales/{id}/audience-hunt``.
    """
    sale = (
        db.query(Sale)
        .filter(Sale.id == sale_id, Sale.account_id == current.account_id)
        .first()
    )
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    account = _account(db, current)
    lots = (
        db.query(Lot)
        .filter(Lot.account_id == current.account_id, Lot.sale_id == sale.id)
        .order_by(Lot.created_at.asc())
        .all()
    )
    hunt = audience_tasks.sale_hunt_status(sale_id=sale.id, account_id=current.account_id)
    return FacebookGroupsOut.model_validate(
        sale_facebook_groups_payload(sale=sale, account=account, lots=lots, hunt_status=hunt)
    )


@router.get("/facebook/clicks", response_model=FacebookClickReportOut)
def facebook_sale_clicks(
    sale_id: str = Query(..., min_length=1),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FacebookClickReportOut:
    sale = (
        db.query(Sale)
        .filter(Sale.id == sale_id, Sale.account_id == current.account_id)
        .first()
    )
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    from app.services.click_tracking import sale_click_report

    return FacebookClickReportOut.model_validate(
        sale_click_report(db, account_id=current.account_id, sale_id=sale.id)
    )


@router.get("/facebook/posts", response_model=list[FacebookPagePostOut])
def facebook_list_posts(
    lot_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[FacebookPagePostOut]:
    rows = list_posts(db, account_id=current.account_id, lot_id=lot_id, limit=limit)
    return [_post_out(row) for row in rows]
