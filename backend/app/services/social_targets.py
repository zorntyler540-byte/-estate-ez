"""Graph publishing for Instagram feed posts and Page / Instagram stories. No DB access.

Instagram fetches images itself, so each JPEG is staged in R2 and handed to Graph as a
short-lived presigned URL. Containers are created once and their ids are kept in the
caller's ``state`` (persisted on the publish job): a retry checks the container's
``status_code`` first and never calls ``media_publish`` on one that is already
``PUBLISHED``. That is what makes an Instagram retry safe even when Graph timed out.

Neither Instagram nor stories can be scheduled through Graph; the publish queue runs these
at slot time.
"""

from __future__ import annotations

import io
import logging
import time
from typing import Any, Callable, NamedTuple

from app.config import settings
from app.services.meta_graph import (
    MetaGraphError,
    create_instagram_carousel,
    create_instagram_container,
    create_page_photo_story,
    instagram_container_status,
    publish_instagram_container,
    upload_unpublished_photo,
)

_LOG = logging.getLogger(__name__)

PUBLISH_TARGET_FACEBOOK_PAGE = "facebook_page"
PUBLISH_TARGET_INSTAGRAM_FEED = "instagram_feed"
PUBLISH_TARGET_STORIES = "stories"
PUBLISH_TARGET_COPY_ONLY = "copy_only"
PUBLISHABLE_TARGETS = frozenset({PUBLISH_TARGET_FACEBOOK_PAGE, PUBLISH_TARGET_INSTAGRAM_FEED, PUBLISH_TARGET_STORIES})
# Graph has no scheduled publish for these; the queue posts them at slot time.
DEFERRED_TARGETS = frozenset({PUBLISH_TARGET_INSTAGRAM_FEED, PUBLISH_TARGET_STORIES})

IG_CAPTION_MAX = 2200
IG_MAX_CAROUSEL = 10
# Instagram feed rejects images outside 4:5 (portrait) .. 1.91:1 (landscape).
IG_FEED_MIN_RATIO = 4 / 5
IG_FEED_MAX_RATIO = 1.91
CONTAINER_POLL_SECONDS = 2.0
CONTAINER_POLL_TIMEOUT = 60.0
MEDIA_URL_SECONDS = 2 * 3600
SOCIAL_MEDIA_PREFIX = "social-media"


class TargetResult(NamedTuple):
    post_id: str | None
    instagram_media_id: str | None
    note: str | None = None


def instagram_enabled() -> bool:
    return bool(settings.meta_instagram_publishing)


def instagram_user_id(account: Any) -> str | None:
    """The linked IG professional account, only when Instagram publishing is switched on."""
    if not instagram_enabled():
        return None
    return str(getattr(account, "instagram_user_id", None) or "").strip() or None


def fit_instagram_feed(jpeg: bytes) -> bytes:
    """Center-crop into Instagram's feed aspect range; JPEGs already inside it are returned as-is."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(jpeg)) as img:
            width, height = img.size
            if not width or not height:
                return jpeg
            ratio = width / height
            if IG_FEED_MIN_RATIO <= ratio <= IG_FEED_MAX_RATIO:
                return jpeg
            if ratio < IG_FEED_MIN_RATIO:
                new_h = int(width / IG_FEED_MIN_RATIO)
                top = (height - new_h) // 2
                box = (0, top, width, top + new_h)
            else:
                new_w = int(height * IG_FEED_MAX_RATIO)
                left = (width - new_w) // 2
                box = (left, 0, left + new_w, height)
            out = io.BytesIO()
            img.convert("RGB").crop(box).save(out, format="JPEG", quality=88)
            return out.getvalue()
    except Exception:  # noqa: BLE001
        _LOG.warning("Could not crop image for Instagram; sending it unchanged", exc_info=True)
        return jpeg


def stage_public_jpeg(*, account_id: str, record_id: str, photo_id: str, jpeg: bytes, kind: str) -> str:
    """Put a JPEG in R2 and return a presigned GET URL Instagram can fetch."""
    from app.object_storage import presigned_get_url, put_bytes, r2_configured

    if not r2_configured():
        raise MetaGraphError(
            "Instagram needs R2 storage for public image URLs (set R2_* on the API and worker)."
        )
    key = _staged_key(account_id, record_id, photo_id, kind)
    put_bytes(key, jpeg, content_type="image/jpeg")
    return presigned_get_url(key, expires_seconds=MEDIA_URL_SECONDS)


def _staged_key(account_id: str, record_id: str, photo_id: str, kind: str) -> str:
    return f"{SOCIAL_MEDIA_PREFIX}/{account_id}/{record_id}/{photo_id}-{kind}.jpg"


def discard_staged_media(*, account_id: str, record_id: str, photo_ids: list[str]) -> None:
    """Best-effort delete of the R2 copies once Instagram no longer needs to fetch them."""
    from app.object_storage import delete_object, r2_configured

    if not r2_configured():
        return
    for photo_id in photo_ids:
        for kind in ("feed", "story"):
            delete_object(_staged_key(account_id, record_id, photo_id, kind))


def _wait_finished(container_id: str, page_token: str, *, sleep: Callable[[float], None]) -> None:
    deadline = time.monotonic() + CONTAINER_POLL_TIMEOUT
    while True:
        status = instagram_container_status(container_id=container_id, page_token=page_token)
        if status in ("FINISHED", "PUBLISHED"):
            return
        if status in ("ERROR", "EXPIRED"):
            raise MetaGraphError(
                f"Instagram could not process the image (container {status.lower()}). "
                "Check the photo size and format."
            )
        if time.monotonic() >= deadline:
            raise MetaGraphError("Instagram is still processing the image; will retry.", is_transient=True)
        sleep(CONTAINER_POLL_SECONDS)


def publish_instagram(
    *,
    ig_user_id: str,
    page_token: str,
    stage_urls: Callable[[], list[str]],
    caption: str,
    story: bool,
    state: dict[str, Any],
    persist: Callable[[], None],
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Single image, carousel, or story. Resumes from ``state["creation_id"]``."""
    if state.get("media_id"):
        return str(state["media_id"])
    creation = str(state.get("creation_id") or "")
    if creation:
        status = instagram_container_status(container_id=creation, page_token=page_token)
        if status == "PUBLISHED":
            state["media_id"] = creation
            persist()
            return creation
        if status in ("ERROR", "EXPIRED"):
            creation = ""
            state.pop("creation_id", None)
            persist()
    if not creation:
        urls = stage_urls()
        if not urls:
            raise MetaGraphError("Could not read any listing photos to upload.")
        text = (caption or "")[:IG_CAPTION_MAX]
        if story:
            creation = create_instagram_container(
                ig_user_id=ig_user_id, page_token=page_token, image_url=urls[0], media_type="STORIES"
            )
        elif len(urls) == 1:
            creation = create_instagram_container(
                ig_user_id=ig_user_id, page_token=page_token, image_url=urls[0], caption=text
            )
        else:
            children = [
                create_instagram_container(
                    ig_user_id=ig_user_id, page_token=page_token, image_url=url, is_carousel_item=True
                )
                for url in urls[:IG_MAX_CAROUSEL]
            ]
            creation = create_instagram_carousel(
                ig_user_id=ig_user_id, page_token=page_token, children=children, caption=text
            )
        state["creation_id"] = creation
        persist()
    _wait_finished(creation, page_token, sleep=sleep)
    try:
        media_id = publish_instagram_container(ig_user_id=ig_user_id, page_token=page_token, container_id=creation)
    except MetaGraphError as exc:
        if exc.is_token_error or not (exc.is_transient or exc.unknown_outcome):
            raise
        # Graph may have published before the error; the container knows.
        try:
            published = instagram_container_status(container_id=creation, page_token=page_token) == "PUBLISHED"
        except MetaGraphError:
            published = False
        if not published:
            # Safe to retry: the next attempt re-checks this container before publishing.
            raise MetaGraphError(str(exc), status_code=exc.status_code, code=exc.code, is_transient=True) from exc
        media_id = creation
    state["media_id"] = media_id
    persist()
    return media_id


def publish_facebook_story(
    *,
    page_id: str,
    page_token: str,
    jpeg: bytes,
    filename: str,
    state: dict[str, Any],
    persist: Callable[[], None],
) -> str:
    if state.get("fb_story_id"):
        return str(state["fb_story_id"])
    photo_id = upload_unpublished_photo(page_id=page_id, page_token=page_token, image_bytes=jpeg, filename=filename)
    story_id = create_page_photo_story(page_id=page_id, page_token=page_token, photo_id=photo_id)
    state["fb_story_id"] = story_id
    persist()
    return story_id


def publish_stories(
    *,
    page_id: str,
    page_token: str,
    ig_user_id: str | None,
    jpeg: bytes,
    filename: str,
    stage_url: Callable[[], str],
    state: dict[str, Any],
    persist: Callable[[], None],
    sleep: Callable[[float], None] = time.sleep,
) -> TargetResult:
    """Page story, then the Instagram story when Instagram is linked.

    Once the Page story is live an Instagram failure is a note on the row, never a retry of
    the whole slot (that would post the Page story twice).
    """
    fb_story = publish_facebook_story(
        page_id=page_id, page_token=page_token, jpeg=jpeg, filename=filename, state=state, persist=persist
    )
    if not ig_user_id:
        return TargetResult(fb_story, None, None)
    ig_state = state.setdefault("instagram", {})
    try:
        ig_story = publish_instagram(
            ig_user_id=ig_user_id,
            page_token=page_token,
            stage_urls=lambda: [stage_url()],
            caption="",
            story=True,
            state=ig_state,
            persist=persist,
            sleep=sleep,
        )
    except MetaGraphError as exc:
        _LOG.warning("Instagram story failed after the Page story posted: %s", exc)
        return TargetResult(fb_story, None, f"Facebook story is live; the Instagram story failed: {exc}")
    return TargetResult(fb_story, ig_story, None)
