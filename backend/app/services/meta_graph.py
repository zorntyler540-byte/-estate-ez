"""Pinned Meta Graph API client: Page feed posts, Page photo stories, and Instagram
content publishing (``/{ig-user-id}/media`` containers -> ``media_publish``).

Instagram calls use the connected Page's token; the IG professional account must be
linked to that Page. Groups and Marketplace are not supported by Graph for this app.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import requests

# Pin once. Bump this constant (and the tests that assert it) when Meta sunsets the version.
# Current stable as of 2026-10: Graph recognizes v26.0; v27.0 is not a Graph version yet.
GRAPH_API_VERSION = "v26.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
OAUTH_DIALOG_URL = f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth"

# Page feed posts and Page stories. Instagram adds instagram_basic + instagram_content_publish
# (both need Meta App Review before non-admin users can grant them).
PAGE_OAUTH_SCOPES = ("pages_show_list", "pages_manage_posts", "pages_read_engagement")
INSTAGRAM_SCOPES = ("instagram_basic", "instagram_content_publish")

_LOG = logging.getLogger(__name__)

# Graph OAuthException codes that mean "reconnect the Page".
_TOKEN_ERROR_CODES = frozenset({102, 190, 463, 467})
# Throttling / temporary Graph errors that are safe to retry later.
_TRANSIENT_CODES = frozenset({1, 2, 4, 17, 32, 341, 613, 80001, 80002})


class MetaGraphError(RuntimeError):
    """Graph API error. ``is_token_error`` means the stored Page/user token is dead."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: int | None = None,
        is_token_error: bool = False,
        is_transient: bool = False,
        unknown_outcome: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.is_token_error = is_token_error
        # Facebook said "try again later" (throttling, 5xx).
        self.is_transient = is_transient
        # The request may have reached Facebook (read timeout, dropped connection). Retrying a
        # non-idempotent call (feed post, story) could post twice.
        self.unknown_outcome = unknown_outcome


def oauth_scopes(*, instagram: bool = False) -> tuple[str, ...]:
    return PAGE_OAUTH_SCOPES + (INSTAGRAM_SCOPES if instagram else ())


def oauth_dialog_url(*, app_id: str, redirect_uri: str, state: str, instagram: bool = False) -> str:
    query = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": ",".join(oauth_scopes(instagram=instagram)),
        }
    )
    return f"{OAUTH_DIALOG_URL}?{query}"


def _parse_graph_error(payload: Any, status_code: int | None) -> MetaGraphError:
    err = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(err, dict):
        return MetaGraphError(
            "Facebook Graph API request failed",
            status_code=status_code,
        )
    message = str(err.get("message") or "Facebook Graph API request failed").strip()
    try:
        code = int(err.get("code")) if err.get("code") is not None else None
    except (TypeError, ValueError):
        code = None
    kind = str(err.get("type") or "")
    subcode = err.get("error_subcode")
    try:
        sub_n = int(subcode) if subcode is not None else None
    except (TypeError, ValueError):
        sub_n = None
    low = message.lower()
    # Do not match bare "session" / "expired" — those false-flag live Page tokens
    # (rate-limit copy, publishing windows, etc.). Codes 102/190/463/467 are the
    # reconnect cases; keep only unambiguous token phrases as a fallback.
    token_phrases = (
        "invalid oauth access token",
        "invalid access token",
        "cannot parse access token",
        "error validating access token",
        "session has expired",
        "session has been invalidated",
        "has been revoked",
    )
    is_token = (
        code in _TOKEN_ERROR_CODES
        or sub_n in _TOKEN_ERROR_CODES
        or (kind == "OAuthException" and any(tok in low for tok in token_phrases))
    )
    if is_token and "reconnect" not in low:
        message = f"{message} Reconnect the Facebook Page on Account settings."
    transient = (not is_token) and (
        err.get("is_transient") is True
        or code in _TRANSIENT_CODES
        or (status_code is not None and status_code >= 500)
    )
    return MetaGraphError(
        message,
        status_code=status_code,
        code=code,
        is_token_error=is_token,
        is_transient=transient,
    )


def graph_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """HTTP helper tests patch. ``path`` is ``/me`` or a full URL."""
    url = path if path.startswith("http") else f"{GRAPH_API_BASE}{path}"
    try:
        response = requests.request(
            method.upper(),
            url,
            params=params,
            data=data,
            files=files,
            timeout=timeout,
        )
    except requests.ConnectTimeout as exc:
        raise MetaGraphError(f"Could not reach Facebook Graph API: {exc}", is_transient=True) from exc
    except requests.RequestException as exc:
        raise MetaGraphError(
            f"Facebook Graph API did not answer: {exc}",
            is_transient=True,
            unknown_outcome=True,
        ) from exc
    payload: Any
    try:
        payload = response.json()
    except ValueError:
        payload = {"error": {"message": (response.text or "").strip() or response.reason}}
    if not response.ok:
        raise _parse_graph_error(payload, response.status_code)
    if isinstance(payload, dict) and payload.get("error"):
        raise _parse_graph_error(payload, response.status_code)
    if not isinstance(payload, dict):
        return {"data": payload}
    return payload


def exchange_code_for_user_token(
    *,
    app_id: str,
    app_secret: str,
    redirect_uri: str,
    code: str,
) -> dict[str, Any]:
    return graph_request(
        "GET",
        "/oauth/access_token",
        params={
            "client_id": app_id,
            "client_secret": app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
    )


def exchange_long_lived_user_token(
    *,
    app_id: str,
    app_secret: str,
    short_lived_token: str,
) -> dict[str, Any]:
    return graph_request(
        "GET",
        "/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_lived_token,
        },
    )


def debug_token(*, app_id: str, app_secret: str, input_token: str) -> dict[str, Any]:
    return graph_request(
        "GET",
        "/debug_token",
        params={
            "input_token": input_token,
            "access_token": f"{app_id}|{app_secret}",
        },
    )


def get_me(user_token: str) -> dict[str, Any]:
    return graph_request("GET", "/me", params={"access_token": user_token, "fields": "id,name"})


def list_user_pages(user_token: str) -> list[dict[str, Any]]:
    payload = graph_request(
        "GET",
        "/me/accounts",
        params={
            "access_token": user_token,
            "fields": "id,name,access_token,tasks",
            "limit": 100,
        },
    )
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        page_id = str(row.get("id") or "").strip()
        token = str(row.get("access_token") or "").strip()
        if not page_id or not token:
            continue
        tasks = row.get("tasks") if isinstance(row.get("tasks"), list) else []
        task_names = {str(t).upper() for t in tasks}
        can_post = (not task_names) or ("CREATE_CONTENT" in task_names) or ("MANAGE" in task_names)
        out.append(
            {
                "id": page_id,
                "name": str(row.get("name") or "").strip() or page_id,
                "access_token": token,
                "can_post": can_post,
            }
        )
    return out


def upload_unpublished_photo(
    *,
    page_id: str,
    page_token: str,
    image_bytes: bytes,
    filename: str = "lot.jpg",
) -> str:
    """Upload a photo that is not published on its own (for a multi-photo feed post)."""
    payload = graph_request(
        "POST",
        f"/{page_id}/photos",
        data={"access_token": page_token, "published": "false"},
        files={"source": (filename, image_bytes, "image/jpeg")},
        timeout=90.0,
    )
    photo_id = str(payload.get("id") or "").strip()
    if not photo_id:
        raise MetaGraphError("Facebook accepted the photo upload but returned no id")
    return photo_id


def create_page_feed_post(
    *,
    page_id: str,
    page_token: str,
    message: str,
    media_fbids: list[str],
    scheduled_unix: int | None = None,
) -> str:
    """One feed post with attached unpublished photos. Schedule with Unix time or publish now."""
    data: dict[str, Any] = {
        "access_token": page_token,
        "message": message,
    }
    for index, fbid in enumerate(media_fbids):
        data[f"attached_media[{index}]"] = f'{{"media_fbid":"{fbid}"}}'
    if scheduled_unix:
        data["published"] = "false"
        data["scheduled_publish_time"] = str(int(scheduled_unix))
    else:
        data["published"] = "true"
    payload = graph_request("POST", f"/{page_id}/feed", data=data, timeout=90.0)
    post_id = str(payload.get("id") or "").strip()
    if not post_id:
        raise MetaGraphError("Facebook accepted the post but returned no id")
    return post_id


def update_scheduled_page_post(
    *,
    post_id: str,
    page_token: str,
    scheduled_unix: int,
) -> None:
    """Move an unpublished scheduled feed post. No-op helpers must not publish it."""
    graph_request(
        "POST",
        f"/{post_id}",
        data={
            "access_token": page_token,
            "published": "false",
            "is_published": "false",
            "scheduled_publish_time": str(int(scheduled_unix)),
        },
        timeout=60.0,
    )


def delete_page_post(*, post_id: str, page_token: str) -> None:
    graph_request(
        "DELETE",
        f"/{post_id}",
        params={"access_token": page_token},
        timeout=60.0,
    )


def create_page_photo_story(*, page_id: str, page_token: str, photo_id: str) -> str:
    """Publish an uploaded unpublished photo as a Page story (stories cannot be scheduled)."""
    payload = graph_request(
        "POST",
        f"/{page_id}/photo_stories",
        data={"access_token": page_token, "photo_id": photo_id},
        timeout=90.0,
    )
    post_id = str(payload.get("post_id") or payload.get("id") or "").strip()
    if not post_id:
        raise MetaGraphError("Facebook accepted the story but returned no id")
    return post_id


def get_instagram_account(*, page_id: str, page_token: str) -> dict[str, str] | None:
    """The Instagram professional account linked to a Page, or None."""
    payload = graph_request(
        "GET",
        f"/{page_id}",
        params={"access_token": page_token, "fields": "instagram_business_account{id,username}"},
    )
    linked = payload.get("instagram_business_account") if isinstance(payload, dict) else None
    if not isinstance(linked, dict) or not str(linked.get("id") or "").strip():
        return None
    return {"id": str(linked["id"]).strip(), "username": str(linked.get("username") or "").strip()}


def create_instagram_container(
    *,
    ig_user_id: str,
    page_token: str,
    image_url: str,
    caption: str | None = None,
    media_type: str | None = None,
    is_carousel_item: bool = False,
) -> str:
    """One image container. ``media_type="STORIES"`` for a story; carousel children skip the caption."""
    data: dict[str, Any] = {"access_token": page_token, "image_url": image_url}
    if media_type:
        data["media_type"] = media_type
    if is_carousel_item:
        data["is_carousel_item"] = "true"
    elif caption:
        data["caption"] = caption
    payload = graph_request("POST", f"/{ig_user_id}/media", data=data, timeout=90.0)
    container = str(payload.get("id") or "").strip()
    if not container:
        raise MetaGraphError("Instagram accepted the image but returned no container id")
    return container


def create_instagram_carousel(*, ig_user_id: str, page_token: str, children: list[str], caption: str) -> str:
    payload = graph_request(
        "POST",
        f"/{ig_user_id}/media",
        data={
            "access_token": page_token,
            "media_type": "CAROUSEL",
            "children": ",".join(children),
            "caption": caption,
        },
        timeout=90.0,
    )
    container = str(payload.get("id") or "").strip()
    if not container:
        raise MetaGraphError("Instagram accepted the carousel but returned no container id")
    return container


def instagram_container_status(*, container_id: str, page_token: str) -> str:
    """``IN_PROGRESS`` / ``FINISHED`` / ``PUBLISHED`` / ``ERROR`` / ``EXPIRED``."""
    payload = graph_request(
        "GET",
        f"/{container_id}",
        params={"access_token": page_token, "fields": "status_code"},
    )
    return str(payload.get("status_code") or "").strip().upper()


def publish_instagram_container(*, ig_user_id: str, page_token: str, container_id: str) -> str:
    payload = graph_request(
        "POST",
        f"/{ig_user_id}/media_publish",
        data={"access_token": page_token, "creation_id": container_id},
        timeout=90.0,
    )
    media_id = str(payload.get("id") or "").strip()
    if not media_id:
        raise MetaGraphError("Instagram published the post but returned no media id")
    return media_id
