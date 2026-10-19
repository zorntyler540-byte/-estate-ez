"""Watcher snapshot ingest and the staff Push queue / social calendar."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import User
from app.schemas import (
    EngagementIngestOut,
    EngagementSaleOut,
    EngagementSaleSummary,
    EngagementSnapshotIn,
    SocialCalendarPost,
)
from app.services.engagement_store import (
    apply_engagement_snapshot,
    engagement_calendar,
    engagement_sale_detail,
    list_engagement_sales,
)

router = APIRouter()


@router.post("/integrations/engagement/snapshots", response_model=EngagementIngestOut)
def ingest_engagement_snapshot(
    payload: EngagementSnapshotIn,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EngagementIngestOut:
    """Store one sale's live counts and score Push / Needs eyes / No interest / Protect / Let Ride.

    The body has no bidder fields. Unknown keys are rejected.
    """
    return apply_engagement_snapshot(db, account_id=current.account_id, payload=payload)


@router.get("/engagement/sales", response_model=list[EngagementSaleSummary])
def get_engagement_sales(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[EngagementSaleSummary]:
    return list_engagement_sales(db, current.account_id)


@router.get("/engagement/sales/{ctseller_sale_id}", response_model=EngagementSaleOut)
def get_engagement_sale(
    ctseller_sale_id: int,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EngagementSaleOut:
    detail = engagement_sale_detail(db, current.account_id, ctseller_sale_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="No engagement snapshot for that sale yet")
    return detail


@router.get(
    "/engagement/sales/{ctseller_sale_id}/calendar",
    response_model=list[SocialCalendarPost],
)
def get_engagement_calendar(
    ctseller_sale_id: int,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SocialCalendarPost]:
    posts = engagement_calendar(db, current.account_id, ctseller_sale_id)
    if posts is None:
        raise HTTPException(status_code=404, detail="No engagement snapshot for that sale yet")
    return posts
