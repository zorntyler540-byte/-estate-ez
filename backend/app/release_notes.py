"""What's new notes shipped with the API.

Edit ``release_notes.json`` and deploy. Both clients ask for the newest entry
and show it once per ``id``. Nothing is stored in the database.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

PlatformName = Literal["web", "mobile", "all"]
PLATFORMS = frozenset({"web", "mobile", "all"})
DEFAULT_PATH = Path(__file__).with_name("release_notes.json")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_ITEMS = 12
_MAX_TITLE = 140
_MAX_ITEM = 600


class ReleaseNote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    date: str
    title: str
    platform: PlatformName = "all"
    items: list[str]


class ReleaseNoteLatestOut(BaseModel):
    note: ReleaseNote | None = None


class ReleaseNoteListOut(BaseModel):
    notes: list[ReleaseNote]


def clear_release_notes_cache() -> None:
    _load_cached.cache_clear()


def load_release_notes(path: Path | None = None) -> tuple[ReleaseNote, ...]:
    """Notes in file order. A repeated id keeps the later entry."""
    source = path if path is not None else DEFAULT_PATH
    return _load_cached(str(source))


def notes_for_platform(notes: tuple[ReleaseNote, ...] | list[ReleaseNote], platform: str) -> list[ReleaseNote]:
    """Newest date first. A later entry wins when two notes share a date.

    ``all`` returns every note. ``web`` and ``mobile`` also include notes marked ``all``.
    """
    want = (platform or "all").strip().lower()
    if want not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}")
    ranked: list[tuple[str, int, ReleaseNote]] = []
    for index, note in enumerate(notes):
        if want != "all" and note.platform not in {want, "all"}:
            continue
        ranked.append((note.date, index, note))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in ranked]


def latest_release_note(platform: str = "all", *, path: Path | None = None) -> ReleaseNote | None:
    rows = notes_for_platform(load_release_notes(path), platform)
    return rows[0] if rows else None


def list_release_notes(platform: str = "all", *, path: Path | None = None) -> list[ReleaseNote]:
    return notes_for_platform(load_release_notes(path), platform)


@lru_cache(maxsize=16)
def _load_cached(path_str: str) -> tuple[ReleaseNote, ...]:
    path = Path(path_str)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error("release notes file missing: %s", path)
        return ()
    except json.JSONDecodeError:
        logger.exception("release notes file is not valid JSON: %s", path)
        return ()
    except OSError:
        logger.exception("release notes file could not be read: %s", path)
        return ()

    if isinstance(payload, dict):
        raw_notes = payload.get("notes", [])
    elif isinstance(payload, list):
        raw_notes = payload
    else:
        logger.error("release notes file must be a list or an object with notes: %s", path)
        return ()
    if not isinstance(raw_notes, list):
        logger.error("release notes 'notes' must be a list: %s", path)
        return ()

    parsed: list[tuple[int, ReleaseNote]] = []
    for index, raw in enumerate(raw_notes):
        note = _parse_entry(raw, index)
        if note is not None:
            parsed.append((index, note))

    last: dict[str, tuple[int, ReleaseNote]] = {}
    for index, note in parsed:
        if note.id in last:
            logger.warning("release note id %s repeated; keeping the later entry", note.id)
        last[note.id] = (index, note)
    ordered = [pair[1] for pair in sorted(last.values(), key=lambda pair: pair[0])]
    return tuple(ordered)


def _parse_entry(raw: object, index: int) -> ReleaseNote | None:
    label = index + 1
    if not isinstance(raw, dict):
        logger.warning("release note %s skipped: not an object", label)
        return None
    note_id = str(raw.get("id") or "").strip().lower()
    if not _ID_RE.fullmatch(note_id):
        logger.warning("release note %s skipped: id must be a lowercase slug", label)
        return None
    date = str(raw.get("date") or "").strip()
    if not _DATE_RE.fullmatch(date):
        logger.warning("release note %s skipped: date must be YYYY-MM-DD", label)
        return None
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        logger.warning("release note %s skipped: date is not a real calendar day", label)
        return None
    title = str(raw.get("title") or "").strip()
    if not title or len(title) > _MAX_TITLE:
        logger.warning("release note %s skipped: title is missing or too long", label)
        return None
    platform = str(raw.get("platform") or "all").strip().lower()
    if platform not in PLATFORMS:
        logger.warning("release note %s skipped: platform must be web, mobile, or all", label)
        return None
    raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        logger.warning("release note %s skipped: items must be a list of strings", label)
        return None
    items: list[str] = []
    for item in raw_items:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            items.append(text[:_MAX_ITEM])
    if not items:
        logger.warning("release note %s skipped: no bullet items", label)
        return None
    if len(items) > _MAX_ITEMS:
        logger.warning("release note %s skipped: more than %s items", label, _MAX_ITEMS)
        return None
    return ReleaseNote(id=note_id, date=date, title=title, platform=platform, items=items)  # type: ignore[arg-type]
