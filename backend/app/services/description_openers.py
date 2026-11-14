"""Listing-description openers: one banned-phrase list, then a per-sale check.

The listing author (``GEMINI_PRO_MODEL``, currently gemini-2.5-pro) is told to
open on a specific fact. After it writes, we compare the first sentence with
descriptions already stored for the same sale. A banned opener or a repeated
opening is rewritten in place — the first sentence only — up to a couple of
tries. Lots keep running in parallel: this reads sibling text when it checks
and does not take a sale lock. Two items that finish together can still pick
similar openers.

The rewrite call stays compatible with Gemini 2.5 and with the 3.x models that
2.5 Pro is rerouted to. It does not set ``temperature``, ``thinking_budget``,
or a prefilled model turn.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings

_LOG = logging.getLogger(__name__)

# Edit this list to change the author prompt and the post-check together.
# Matched as whole words inside the first sentence (case and punctuation ignored).
BANNED_DESCRIPTION_OPENERS: tuple[str, ...] = (
    "Add a touch",
    "A perfect",
    "A classic",
    "A delightful",
    "A charming",
    "Elevate",
    "ready to",
    "statement piece for any",
)

# Discouraged filler. Same first-sentence match as the banned openers; the
# prompt tells the model to avoid overusing these rather than to never use them.
DISCOURAGED_DESCRIPTION_FILLER: tuple[str, ...] = ("timeless",)

# Duplicate check: first N normalized words of the opening sentence.
OPENING_KEY_WORDS = 4

# Hard cap on model rewrites. The original draft stays if none of them is better.
DESCRIPTION_OPENER_MAX_RETRIES = 2

# How many other openings in the sale to show the rewrite, besides the conflict.
SIBLING_OPENING_KEY_LIMIT = 30

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.IGNORECASE)
_SENTENCE_ABBREV = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "st",
        "jr",
        "sr",
        "no",
        "approx",
        "fig",
        "in",
        "ft",
        "lb",
        "oz",
        "yd",
        "qt",
        "pt",
        "vs",
        "etc",
        "co",
        "inc",
        "ltd",
    }
)


class DescriptionFirstSentence(BaseModel):
    """Structured reply for the opener rewrite. One sentence, no extra prose."""

    model_config = ConfigDict(extra="forbid")

    first_sentence: str = Field(
        default="",
        description=(
            "Replacement opening sentence only. Start with a specific maker, material, "
            "era, detail, or what the item is. Do not include later sentences."
        ),
    )


def description_opener_prompt_clause() -> str:
    """Shared author / rewrite instruction. Built from the lists above."""
    banned = ", ".join(f'"{phrase}"' for phrase in BANNED_DESCRIPTION_OPENERS)
    filler = ", ".join(f'"{phrase}"' for phrase in DISCOURAGED_DESCRIPTION_FILLER)
    return (
        "Open the first sentence with something specific to this item "
        "(maker/brand, material, era, a standout detail, or what it is), "
        "not a generic lifestyle hook. "
        f"Do not use these openers or filler: {banned}. "
        f"Avoid overusing {filler}."
    )


def professional_description_schema_hint() -> str:
    """Schema blurb for the listing description field. Same opener rule."""
    return (
        "Warm CTBids seller paragraph (~50 words). "
        + description_opener_prompt_clause()
        + " Then the title hook in natural words. Not 'This is a…' or a condition essay. "
        "Marks and exact sizes belong in specification lines."
    )


def opener_rewrite_system_instruction() -> str:
    return (
        "You are a professional estate specialist revising one sentence of a CT Bids auction listing. "
        "Output structured JSON only. Rewrite only the opening sentence. "
        "Keep the warm seller tone and about the same length as the sentence you replace. "
        "Do not invent brands, dates, materials, or flaws that are not already in the title or description. "
        + description_opener_prompt_clause()
    )


def normalized_words(text: str) -> list[str]:
    """Lowercase word tokens. Punctuation, em dashes, and extra space drop out."""
    folded = (
        (text or "")
        .replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", " ")
        .replace("\u201d", " ")
    )
    return [match.group(0).lower() for match in _WORD_RE.finditer(folded)]


def split_first_sentence(text: str) -> tuple[str, str]:
    """Split ``text`` into the first sentence and everything after it.

    Periods inside abbreviations (``St.``, ``in.``, ``U.S.``) stay in the first
    sentence. An em dash does not end a sentence.
    """
    raw = (text or "").strip()
    if not raw:
        return "", ""
    index = 0
    length = len(raw)
    while index < length:
        if raw[index] in ".!?":
            cursor = index - 1
            while cursor >= 0 and raw[cursor].isalpha():
                cursor -= 1
            token = raw[cursor + 1 : index].lower()
            nxt = index + 1
            while nxt < length and raw[nxt] in "\"'”’)]}":
                nxt += 1
            at_end = nxt >= length
            boundary = at_end or raw[nxt].isspace()
            abbrev = token in _SENTENCE_ABBREV or (len(token) == 1 and token.isalpha())
            if boundary and not abbrev:
                return raw[: index + 1].strip(), raw[index + 1 :].strip()
        index += 1
    return raw, ""


def opening_words(text: str) -> list[str]:
    first, _rest = split_first_sentence(text)
    return normalized_words(first)


def opening_key(text: str, *, words: int = OPENING_KEY_WORDS) -> str:
    """Opening pattern: the first few normalized words of the first sentence."""
    return " ".join(opening_words(text)[:words])


def _contains_phrase(words: list[str], phrase: str) -> bool:
    phrase_words = normalized_words(phrase)
    if not phrase_words or len(phrase_words) > len(words):
        return False
    size = len(phrase_words)
    return any(words[index : index + size] == phrase_words for index in range(len(words) - size + 1))


def matched_opener_phrases(text: str) -> list[str]:
    """Banned openers and discouraged filler found in the first sentence."""
    words = opening_words(text)
    if not words:
        return []
    hits: list[str] = []
    for phrase in (*BANNED_DESCRIPTION_OPENERS, *DISCOURAGED_DESCRIPTION_FILLER):
        if _contains_phrase(words, phrase):
            hits.append(phrase)
    return hits


def opener_conflicts(text: str, sibling_descriptions: list[str]) -> list[str]:
    """Sibling first sentences whose opening pattern matches ``text``."""
    key = opening_key(text)
    if not key:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for sibling in sibling_descriptions:
        if opening_key(sibling) != key:
            continue
        first, _rest = split_first_sentence(sibling)
        sample = (first or key).strip()
        marker = sample.lower()
        if marker in seen:
            continue
        seen.add(marker)
        found.append(sample)
    return found


def sibling_opening_keys(sibling_descriptions: list[str], *, limit: int = SIBLING_OPENING_KEY_LIMIT) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for sibling in sibling_descriptions:
        key = opening_key(sibling)
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
        if len(keys) >= limit:
            break
    return keys


def rewrite_reason(text: str, sibling_descriptions: list[str]) -> str | None:
    """Why this description should get a new first sentence, or None."""
    hits = matched_opener_phrases(text)
    conflicts = opener_conflicts(text, sibling_descriptions)
    if not hits and not conflicts:
        return None
    parts: list[str] = []
    if hits:
        parts.append("banned:" + "|".join(hits))
    if conflicts:
        parts.append("duplicate")
    return ";".join(parts)


def opener_issue_score(text: str, sibling_keys: set[str]) -> tuple[int, int]:
    """Lower is better: banned-phrase count, then whether the opening is already used."""
    key = opening_key(text)
    duplicate = 1 if key and key in sibling_keys else 0
    return (len(matched_opener_phrases(text)), duplicate)


def replace_first_sentence(description: str, new_first: str) -> str:
    """Swap the opening sentence and keep every later sentence."""
    cleaned = (new_first or "").strip().strip("\"'“”")
    cleaned = split_first_sentence(cleaned)[0].strip()
    if not cleaned:
        return description
    _first, rest = split_first_sentence(description)
    if rest and cleaned.endswith(rest):
        cleaned = cleaned[: -len(rest)].strip()
    if not cleaned:
        return description
    if cleaned[-1] not in ".!?":
        cleaned += "."
    if not rest:
        return cleaned
    return f"{cleaned} {rest}"


def listing_author_model() -> str:
    """Listing-author model id, read at call time.

    ``run_single_lot_pipeline`` copies ``settings.gemini_pro_model`` into
    ``GEMINI_PRO_MODEL`` before the check. A 2.5 Pro id that Google serves as a
    3.x model is still just this string.
    """
    configured = os.environ.get("GEMINI_PRO_MODEL", "").strip()
    if configured:
        return configured
    return (settings.gemini_pro_model or "gemini-2.5-pro").strip() or "gemini-2.5-pro"


def build_opener_rewrite_config() -> Any:
    """JSON config for the rewrite. No temperature and no thinking budget."""
    from google.genai import types

    from pipeline.gemini_schema import pydantic_model_to_gemini_schema

    return types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=pydantic_model_to_gemini_schema(DescriptionFirstSentence),
        system_instruction=opener_rewrite_system_instruction(),
    )


def opener_rewrite_user_text(*, title: str, description: str, avoid: list[str]) -> str:
    """Single user turn. Not a chat and not a prefilled model reply."""
    first, rest = split_first_sentence(description)
    lines = [
        "Rewrite ONLY the first sentence. Put that sentence in first_sentence.",
        "The sentences after it will be kept unchanged — do not include them in your reply.",
        "",
        f"Title: {title.strip() or '(none)'}",
        f"First sentence to replace: {first}",
    ]
    if rest:
        lines.append(f"Later sentences (keep, do not repeat): {rest}")
    lines.append("")
    lines.append("Do not start the same way as any of these, and do not use them as filler:")
    for item in avoid:
        lines.append(f"- {item}")
    return "\n".join(lines)


def _avoid_lines(
    *,
    conflicts: list[str],
    sibling_keys: list[str],
    prior_attempts: list[str],
) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return
        marker = cleaned.lower()
        if marker in seen:
            return
        seen.add(marker)
        lines.append(cleaned)

    for phrase in BANNED_DESCRIPTION_OPENERS:
        add(phrase)
    for phrase in DISCOURAGED_DESCRIPTION_FILLER:
        add(f"{phrase} (avoid overuse)")
    for conflict in conflicts:
        add(conflict)
    for key in sibling_keys:
        add(key)
    for attempt in prior_attempts:
        add(attempt)
    return lines


def _sentence_from_response(response: Any) -> str:
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        try:
            if isinstance(parsed, DescriptionFirstSentence):
                return (parsed.first_sentence or "").strip()
            if isinstance(parsed, dict):
                return str(parsed.get("first_sentence") or "").strip()
            model = DescriptionFirstSentence.model_validate(parsed)
            return (model.first_sentence or "").strip()
        except Exception:
            pass
    text = str(getattr(response, "text", None) or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except Exception:
        return split_first_sentence(text)[0].strip()
    if isinstance(data, dict):
        return str(data.get("first_sentence") or "").strip()
    return ""


def _usage_tier(model: str) -> str:
    return "pro" if "pro" in (model or "").lower() else "flash"


def _default_generate(*, model: str, contents: str, config: Any) -> Any:
    from pipeline.pipeline import _gemini_generate, _record_usage

    response = _gemini_generate(model, contents, config)
    _record_usage(_usage_tier(model), response)
    return response


def _read_siblings(
    sibling_descriptions: Callable[[], list[str]] | list[str] | None,
    sale_id: str | None,
) -> list[str]:
    if sibling_descriptions is None:
        return []
    try:
        rows = sibling_descriptions() if callable(sibling_descriptions) else sibling_descriptions
    except Exception:
        _LOG.warning(
            "Listing description opener could not read sale siblings sale_id=%s",
            sale_id or "",
            exc_info=True,
        )
        return []
    out: list[str] = []
    for row in rows or []:
        text = str(row or "").strip()
        if text:
            out.append(text)
    return out


def load_sale_descriptions(
    db: Any,
    sale_id: str,
    *,
    exclude_lot_id: str | None = None,
) -> list[str]:
    """Descriptions already authored on this sale.

    Plain SELECT. No row lock, so parallel lots are not queued behind each other.
    """
    from app.models import Lot

    query = db.query(Lot.professional_description).filter(Lot.sale_id == sale_id)
    if exclude_lot_id:
        query = query.filter(Lot.id != exclude_lot_id)
    descriptions: list[str] = []
    for (text,) in query.all():
        cleaned = (text or "").strip()
        if cleaned:
            descriptions.append(cleaned)
    return descriptions


def sale_description_loader(
    db: Any,
    sale_id: str,
    *,
    exclude_lot_id: str | None = None,
) -> Callable[[], list[str]]:
    """Read sibling descriptions when the check runs, then release the connection.

    Uses a short session on the job's database, so it sees rows other workers
    have committed and does not pin the job session during the Gemini rewrite.
    No row lock — parallel lots keep authoring.
    """
    bind = db.get_bind()

    def _load() -> list[str]:
        from sqlalchemy.orm import sessionmaker

        other = sessionmaker(bind=bind, autoflush=False, autocommit=False, future=True)()
        try:
            return load_sale_descriptions(other, sale_id, exclude_lot_id=exclude_lot_id)
        finally:
            other.close()

    return _load


def maybe_rewrite_description_opener(
    description: str,
    *,
    sibling_descriptions: Callable[[], list[str]] | list[str] | None = None,
    title: str = "",
    sale_id: str | None = None,
    enabled: bool | None = None,
    generate: Callable[..., Any] | None = None,
    max_retries: int = DESCRIPTION_OPENER_MAX_RETRIES,
) -> str:
    """Return ``description``, with a new first sentence when the opening is stock or repeated.

    Never raises. A model error or a rewrite that is no better keeps the best text so far.
    ``enabled`` defaults to ``DESCRIPTION_OPENER_REWRITE`` (on).
    """
    original = description or ""
    if enabled is None:
        enabled = bool(settings.description_opener_rewrite)
    if not enabled or not original.strip():
        return original
    try:
        return _rewrite_description_opener(
            original,
            sibling_descriptions=sibling_descriptions,
            title=title,
            sale_id=sale_id,
            generate=generate,
            max_retries=max_retries,
        )
    except Exception:
        _LOG.warning(
            "Listing description opener rewrite failed sale_id=%s",
            sale_id or "",
            exc_info=True,
        )
        return original


def _rewrite_description_opener(
    original: str,
    *,
    sibling_descriptions: Callable[[], list[str]] | list[str] | None,
    title: str,
    sale_id: str | None,
    generate: Callable[..., Any] | None,
    max_retries: int,
) -> str:
    siblings = _read_siblings(sibling_descriptions, sale_id)
    reason = rewrite_reason(original, siblings)
    if reason is None or max_retries < 1:
        return original

    sibling_keys = {key for key in (opening_key(row) for row in siblings) if key}
    best = original
    best_rank: tuple[tuple[int, int], int] = (opener_issue_score(original, sibling_keys), 0)
    prior_attempts: list[str] = []
    attempts = 0
    generate_fn = generate or _default_generate

    for attempt in range(1, max_retries + 1):
        if opener_issue_score(best, sibling_keys) == (0, 0):
            break
        source = best
        avoid = _avoid_lines(
            conflicts=opener_conflicts(source, siblings),
            sibling_keys=sibling_opening_keys(siblings),
            prior_attempts=prior_attempts,
        )
        try:
            response = generate_fn(
                model=listing_author_model(),
                contents=opener_rewrite_user_text(title=title, description=source, avoid=avoid),
                config=build_opener_rewrite_config(),
            )
        except Exception:
            _LOG.warning(
                "Listing description opener rewrite attempt failed sale_id=%s attempt=%s",
                sale_id or "",
                attempt,
                exc_info=True,
            )
            break
        attempts += 1
        sentence = _sentence_from_response(response)
        if not sentence:
            continue
        prior_attempts.append(split_first_sentence(sentence)[0])
        candidate = replace_first_sentence(original, sentence)
        rank = (opener_issue_score(candidate, sibling_keys), attempt)
        if rank < best_rank:
            best = candidate
            best_rank = rank

    if best != original:
        _LOG.info(
            "Listing description opener rewritten sale_id=%s attempts=%s reason=%s before=%r after=%r",
            sale_id or "",
            attempts,
            reason,
            split_first_sentence(original)[0][:180],
            split_first_sentence(best)[0][:180],
        )
    elif attempts:
        _LOG.info(
            "Listing description opener rewrite kept original sale_id=%s attempts=%s reason=%s opening=%r",
            sale_id or "",
            attempts,
            reason,
            split_first_sentence(original)[0][:180],
        )
    return best
