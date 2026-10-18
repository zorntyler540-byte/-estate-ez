"""Pinned Meta Graph API client for Facebook Page posting.

Instagram, Stories, Groups, and Marketplace are out of v1. Keep Instagram
publish calls out of this module until a dedicated publisher exists — Graph
``/{ig-user-id}/media`` is a different object type and token shape.
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

# Page posting v1. Instagram later: instagram_basic + instagram_content_publish (app review).
PAGE_OAUTH_SCOPES = ("pages_show_list", "pages_manage_posts", "pages_read_engagement")
INSTAGRAM_SCOPES_FUTURE = ("instagram_basic", "instagram_content_publish")

_LOG = logging.getLogger(__name__)

# Graph OAuthException codes that mean "reconnect the Page".
_TOKEN_ERROR_CODES = frozenset({102, 190, 463, 467})


class MetaGraphError(RuntimeError):
    """Graph API error. ``is_token_error`` means the stored Page/user token is dead."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: int | None = None,
        is_token_error: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.is_token_error = is_token_error


def oauth_dialog_url(*, app_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": ",".join(PAGE_OAUTH_SCOPES),
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
    return MetaGraphError(message, status_code=status_code, code=code, is_token_error=is_token)


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
    except requests.RequestException as exc:
        raise MetaGraphError(f"Could not reach Facebook Graph API: {exc}") from exc
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
