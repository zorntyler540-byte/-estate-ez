"""Authenticated What's new feed. The JSON file is read once per process."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response

from app.auth import get_current_user
from app.models import User
from app.release_notes import ReleaseNoteLatestOut, ReleaseNoteListOut, latest_release_note, list_release_notes

router = APIRouter(tags=["release-notes"])

PlatformQuery = Literal["web", "mobile", "all"]


def _cache(response: Response) -> None:
    # The file changes only when a deploy ships. Private so a shared cache cannot mix accounts.
    response.headers["Cache-Control"] = "private, max-age=300"


@router.get("/release-notes/latest", response_model=ReleaseNoteLatestOut)
def get_latest_release_note(
    response: Response,
    platform: PlatformQuery = Query(
        "all",
        description="web, mobile, or all. A note marked all matches every platform.",
    ),
    _current: User = Depends(get_current_user),
) -> ReleaseNoteLatestOut:
    _cache(response)
    return ReleaseNoteLatestOut(note=latest_release_note(platform))


@router.get("/release-notes", response_model=ReleaseNoteListOut)
def get_release_notes(
    response: Response,
    platform: PlatformQuery = Query(
        "all",
        description="web, mobile, or all. A note marked all matches every platform.",
    ),
    _current: User = Depends(get_current_user),
) -> ReleaseNoteListOut:
    _cache(response)
    return ReleaseNoteListOut(notes=list_release_notes(platform))
