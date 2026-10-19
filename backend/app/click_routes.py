"""Public Facebook click-tracking redirects. No auth — destination is stored per code."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.click_tracking import follow_tracked_link

router = APIRouter()


@router.api_route("/go/{code}", methods=["GET", "HEAD"], include_in_schema=True)
def follow_facebook_link(
    code: str,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    return follow_tracked_link(db, code=code, request=request)
