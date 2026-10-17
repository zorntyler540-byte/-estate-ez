"""Draft Facebook Page captions with Gemini Flash; template fallback when the key is missing."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings

PACIFIC = ZoneInfo("America/Los_Angeles")
FOLLOW_HOOK_LINE = "Follow our Page for upcoming sales."
_LOG = logging.getLogger(__name__)

_SYSTEM = (
    "Identify brand/model details, condition flaws, and era. "
    "You are a professional estate specialist writing a Facebook Page post for an online estate auction. "
    "Output structured JSON only."
)


class FacebookCaptionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caption: str = Field(
        default="",
        description="Facebook Page caption. Include the auction link and close time. No hashtag spam.",
    )


def _format_close_time_portable(moment: datetime | None) -> str:
    """strftime %-d is glibc-only; keep a portable fallback for tests/Windows."""
    if moment is None:
        return ""
    local = moment.astimezone(PACIFIC) if moment.tzinfo else moment.replace(tzinfo=PACIFIC)
    day = local.day
    hour = local.strftime("%I").lstrip("0") or "12"
    ampm = local.strftime("%p")
    weekday = local.strftime("%A")
    month = local.strftime("%B")
    minute = local.strftime("%M")
    return f"{weekday}, {month} {day} at {hour}:{minute} {ampm} PT"


def template_caption(
    *,
    title: str,
    description: str,
    item_url: str,
    close_at: datetime | None,
    include_follow_hook: bool,
    current_bid: float | None = None,
    watchers: int | None = None,
) -> str:
    lines: list[str] = []
    name = (title or "").strip() or "Estate lot"
    lines.append(name)
    desc = (description or "").strip()
    if desc:
        lines.append("")
        lines.append(desc if len(desc) <= 420 else desc[:417].rstrip() + "…")
    extras: list[str] = []
    if watchers:
        extras.append(f"{watchers} watcher" if watchers == 1 else f"{watchers} watchers")
    if current_bid is not None and current_bid > 0:
        dollars = int(round(current_bid)) if abs(current_bid - round(current_bid)) < 0.001 else f"{current_bid:.2f}"
        extras.append(f"current bid ${dollars}")
    if extras:
        lines.append("")
        lines.append(", ".join(extras) + ".")
    close_s = _format_close_time_portable(close_at)
    if close_s:
        lines.append("")
        lines.append(f"Bidding closes {close_s}.")
    url = (item_url or "").strip()
    if url:
        lines.append("")
        lines.append(url)
    if include_follow_hook:
        lines.append("")
        lines.append(FOLLOW_HOOK_LINE)
    return "\n".join(lines).strip()


def apply_follow_hook(caption: str, *, include: bool) -> str:
    text = (caption or "").rstrip()
    hook = FOLLOW_HOOK_LINE
    if text.endswith(hook):
        text = text[: -len(hook)].rstrip()
    if include:
        if text:
            return f"{text}\n\n{hook}"
        return hook
    return text


def _ensure_gemini_env() -> None:
    key = (settings.google_api_key or "").strip()
    if key:
        os.environ.setdefault("GOOGLE_API_KEY", key)
        os.environ.setdefault("GEMINI_API_KEY", key)


def draft_facebook_caption(
    *,
    title: str,
    description: str,
    sku: str,
    item_url: str,
    close_at: datetime | None,
    include_follow_hook: bool,
    current_bid: float | None = None,
    watchers: int | None = None,
    sale_title: str = "",
) -> str:
    """Gemini Flash JSON caption, or the local template if the key is missing/fails."""
    fallback = template_caption(
        title=title,
        description=description,
        item_url=item_url,
        close_at=close_at,
        include_follow_hook=include_follow_hook,
        current_bid=current_bid,
        watchers=watchers,
    )
    if not (settings.google_api_key or "").strip():
        return fallback
    try:
        return _draft_with_gemini(
            title=title,
            description=description,
            sku=sku,
            item_url=item_url,
            close_at=close_at,
            include_follow_hook=include_follow_hook,
            current_bid=current_bid,
            watchers=watchers,
            sale_title=sale_title,
            fallback=fallback,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Facebook caption Gemini failed; using template: %s", exc)
        return fallback


def _draft_with_gemini(
    *,
    title: str,
    description: str,
    sku: str,
    item_url: str,
    close_at: datetime | None,
    include_follow_hook: bool,
    current_bid: float | None,
    watchers: int | None,
    sale_title: str,
    fallback: str,
) -> str:
    from google.genai import types

    from pipeline.gemini_schema import pydantic_model_to_gemini_schema
    from pipeline.pipeline import GEMINI_FLASH_MODEL, _get_client

    _ensure_gemini_env()
    client = _get_client()
    close_s = _format_close_time_portable(close_at) or "(close time unknown)"
    hook = (
        f'End with this exact line on its own: "{FOLLOW_HOOK_LINE}"'
        if include_follow_hook
        else "Do not ask people to follow the Page."
    )
    parts = [
        "Write one Facebook Page caption for this estate-auction lot.\n"
        "Staff will post it to the franchise Facebook Page (not Marketplace, not Groups).\n"
        "Include the CT Bids lot/auction link verbatim. Mention when bidding closes.\n"
        "2–6 short sentences or line breaks. No hashtag dump. No invented prices or provenance.\n"
        f"{hook}\n\n"
        f"Sale: {(sale_title or '').strip() or '(unnamed)'}\n"
        f"SKU: {(sku or '').strip() or '(none)'}\n"
        f"Title: {(title or '').strip()}\n"
        f"Description: {(description or '').strip() or '(none)'}\n"
        f"Link: {(item_url or '').strip() or '(none)'}\n"
        f"Closes: {close_s}\n"
        f"Watchers: {watchers if watchers is not None else '(unknown)'}\n"
        f"Current bid: {current_bid if current_bid is not None else '(unknown)'}\n"
    ]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=pydantic_model_to_gemini_schema(FacebookCaptionDraft),
        system_instruction=_SYSTEM,
        temperature=0.4,
    )
    response = client.models.generate_content(
        model=GEMINI_FLASH_MODEL,
        contents=parts,
        config=config,
    )
    if response.parsed is not None:
        draft = FacebookCaptionDraft.model_validate(response.parsed)
    elif response.text:
        draft = FacebookCaptionDraft.model_validate(json.loads(response.text))
    else:
        return fallback
    caption = (draft.caption or "").strip()
    if not caption:
        return fallback
    return apply_follow_hook(caption, include=include_follow_hook)
