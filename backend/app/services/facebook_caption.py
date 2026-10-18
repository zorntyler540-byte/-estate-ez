"""Draft Facebook Page captions with Gemini Flash; template fallback when the key is missing."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services.ctbids_urls import SALE_LINK_PREFIX, ctbids_sale_url, sale_link_line

PACIFIC = ZoneInfo("America/Los_Angeles")
FOLLOW_HOOK_LINE = "Follow our Page for upcoming sales."
_LOG = logging.getLogger(__name__)

SLOT_ANGLE_LEADS: dict[str, str] = {
    "collector_find": "Collector find",
    "midday": "Midday spotlight",
    "stories": "Still on the table tonight",
    "tiktok": "Would you bid on this?",
    "closing": "Closing countdown",
}

_ANGLE_PROMPTS: dict[str, str] = {
    "collector_find": (
        "Angle: morning collector find. Lead with why a collector would want this piece."
    ),
    "midday": (
        "Angle: midday listing spotlight. Fresh browse-the-photos energy. "
        "Do not reuse a collector-find opener."
    ),
    "stories": (
        "Angle: evening Stories-style urgency as a Page feed caption (not a poll sticker). "
        "Short, still-open-tonight energy."
    ),
    "tiktok": (
        "Angle: TikTok-style hook. Start with a punchy question or “Would you bid on this?” "
        "then the facts. This is a Page feed caption, not a shot list."
    ),
    "closing": (
        "Angle: closing-day countdown. Bidding is almost over. Urgent last-chance tone. "
        "Include the close time."
    ),
}

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
    angle: str = "",
    sale_url: str = "",
) -> str:
    lines: list[str] = []
    name = (title or "").strip() or "Estate lot"
    lead = SLOT_ANGLE_LEADS.get((angle or "").strip())
    if (angle or "").strip() == "tiktok":
        lines.append(f"Would you bid on this? {name}")
    elif lead:
        lines.append(f"{lead}: {name}")
    else:
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
    sale_line = sale_link_line(sale_url or ctbids_sale_url(item_url=url))
    if sale_line:
        lines.append("")
        lines.append(sale_line)
    if include_follow_hook:
        lines.append("")
        lines.append(FOLLOW_HOOK_LINE)
    return arrange_facebook_caption(
        "\n".join(lines).strip(),
        item_url=url,
        sale_url=sale_url or ctbids_sale_url(item_url=url),
        include_follow_hook=include_follow_hook,
    )


_BIDDING_LINE_RE = re.compile(r"^Bidding closes\b.+$", re.IGNORECASE)


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


def _collapse_blank_lines(text: str) -> str:
    lines = [line.rstrip() for line in (text or "").splitlines()]
    out: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if out and not blank:
                out.append("")
            blank = True
            continue
        out.append(line)
        blank = False
    return "\n".join(out).strip()


def arrange_facebook_caption(
    caption: str,
    *,
    item_url: str = "",
    sale_url: str = "",
    include_follow_hook: bool | None = None,
) -> str:
    """Hook/description, bidding close, lot URL, sale URL, optional follow line."""
    sale_line = sale_link_line(sale_url or ctbids_sale_url(item_url=item_url))
    lot_url = (item_url or "").strip()
    body_lines: list[str] = []
    bidding_lines: list[str] = []
    saw_follow = False
    for raw in (caption or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            if body_lines and body_lines[-1] != "":
                body_lines.append("")
            continue
        if stripped == FOLLOW_HOOK_LINE:
            saw_follow = True
            continue
        if sale_line and stripped == sale_line:
            continue
        if stripped.startswith(SALE_LINK_PREFIX):
            continue
        if lot_url and (stripped == lot_url or stripped.rstrip("/.") == lot_url.rstrip("/")):
            continue
        if lot_url and stripped.startswith(lot_url):
            rest = stripped[len(lot_url) :].strip(" -–—")
            if rest:
                body_lines.append(rest)
            continue
        if _BIDDING_LINE_RE.match(stripped):
            bidding_lines.append(stripped if stripped.endswith(".") else f"{stripped}.")
            continue
        body_lines.append(line)
    include_follow = bool(include_follow_hook) if include_follow_hook is not None else saw_follow
    parts: list[str] = []
    body = _collapse_blank_lines("\n".join(body_lines))
    if body:
        parts.append(body)
    if bidding_lines:
        parts.append(bidding_lines[0])
    if lot_url:
        parts.append(lot_url)
    if sale_line:
        parts.append(sale_line)
    if include_follow:
        parts.append(FOLLOW_HOOK_LINE)
    return "\n\n".join(parts).strip()


def ensure_sale_link(caption: str, sale_url: str) -> str:
    """Keep caption layout: body, close, lot URL, sale URL, follow."""
    return arrange_facebook_caption(caption, sale_url=sale_url)


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
    angle: str = "",
    sale_url: str = "",
) -> str:
    """Gemini Flash JSON caption, or the local template if the key is missing/fails."""
    sale_url = sale_url or ctbids_sale_url(item_url=item_url)
    fallback = template_caption(
        title=title,
        description=description,
        item_url=item_url,
        close_at=close_at,
        include_follow_hook=include_follow_hook,
        current_bid=current_bid,
        watchers=watchers,
        angle=angle,
        sale_url=sale_url,
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
            angle=angle,
            sale_url=sale_url,
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
    angle: str = "",
    sale_url: str = "",
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
    angle_line = _ANGLE_PROMPTS.get((angle or "").strip(), "")
    sale_line = sale_link_line(sale_url or ctbids_sale_url(item_url=item_url))
    sale_instruction = (
        f'After the lot URL, include this exact line: "{sale_line}"'
        if sale_line
        else "If you have no overall sale URL, omit it."
    )
    parts = [
        "Write one Facebook Page caption for this estate-auction lot.\n"
        "Staff will post it to the franchise Facebook Page (not Marketplace, not Groups).\n"
        "Order, top to bottom: hook and description first; then a 'Bidding closes …' line; "
        "then the CT Bids lot/item URL on its own line; then the overall sale line; then the follow line.\n"
        "Put links at the bottom, never at the top.\n"
        f"{sale_instruction}\n"
        "Buyers cannot navigate from an item page back to the sale, so both links are required.\n"
        "2–6 short sentences or line breaks. No hashtag dump. No invented prices or provenance.\n"
        f"{hook}\n"
        f"{angle_line}\n\n"
        f"Sale: {(sale_title or '').strip() or '(unnamed)'}\n"
        f"SKU: {(sku or '').strip() or '(none)'}\n"
        f"Title: {(title or '').strip()}\n"
        f"Description: {(description or '').strip() or '(none)'}\n"
        f"Lot link: {(item_url or '').strip() or '(none)'}\n"
        f"Sale link: {sale_line or '(none)'}\n"
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
    caption = apply_follow_hook(caption, include=include_follow_hook)
    return arrange_facebook_caption(
        caption,
        item_url=item_url,
        sale_url=sale_url or ctbids_sale_url(item_url=item_url),
        include_follow_hook=include_follow_hook,
    )
