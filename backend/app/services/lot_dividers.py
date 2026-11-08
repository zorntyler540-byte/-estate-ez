"""Blank divider frames (blank page, empty wall, covered lens) that separate lots in a batch upload."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from app.config import google_api_key, settings
from app.services.image_preprocess import load_pipeline_working_jpeg

_LOG = logging.getLogger(__name__)

# Gemini alone once skipped real white-plate / close-up frames as "blank", so a divider also has
# to be low-detail on the pixels. Edges are measured on a small grayscale copy so sensor noise and
# soft lighting on a wall do not count. A sheet edge or fold may cross the frame; a busy item may not.
_PROBE_EDGE = 160
_EDGE_LEVEL = 40
_CENTER_MARGIN = 0.2
MAX_DIVIDER_CENTER_EDGE_FRACTION = 0.04
MAX_DIVIDER_FRAME_EDGE_FRACTION = 0.06
# Blurry item frames also pass the pixel check; each costs one small Flash call.
MAX_CONTEXT_CHECKS_PER_BATCH = 40
_CONTEXT_THUMB_EDGE = 512


def _strong_edge_fraction(edges: Image.Image) -> float | None:
    hist = edges.histogram()
    total = sum(hist)
    return sum(hist[_EDGE_LEVEL:]) / total if total else None


def frame_edge_fractions(path: Path) -> tuple[float, float] | None:
    """(center, whole-frame) share of strong-edge pixels; None when the file can't be read."""
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            gray = im.convert("L")
            gray.thumbnail((_PROBE_EDGE, _PROBE_EDGE))
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("divider check could not read %s: %s", path.name, exc)
        return None
    w, h = gray.size
    if w < 16 or h < 16:
        return None
    edges = gray.filter(ImageFilter.FIND_EDGES).crop((2, 2, w - 2, h - 2))
    ew, eh = edges.size
    mx, my = int(ew * _CENTER_MARGIN), int(eh * _CENTER_MARGIN)
    center = _strong_edge_fraction(edges.crop((mx, my, ew - mx, eh - my)))
    whole = _strong_edge_fraction(edges)
    if center is None or whole is None:
        return None
    return center, whole


def _fractions_look_blank(fracs: tuple[float, float] | None) -> bool:
    if fracs is None:
        return False
    center, whole = fracs
    return center <= MAX_DIVIDER_CENTER_EDGE_FRACTION and whole <= MAX_DIVIDER_FRAME_EDGE_FRACTION


def frame_looks_blank(path: Path) -> bool:
    return _fractions_look_blank(frame_edge_fractions(path))


def _row_can_be_divider(row: dict) -> bool:
    if str(row.get("lot_tape_marking") or "").strip():
        return False
    return not bool(row.get("shows_whole_lot"))


def _thumb(path: Path | None) -> bytes | None:
    if path is None:
        return None
    try:
        return load_pipeline_working_jpeg(path, max_edge=_CONTEXT_THUMB_EDGE, quality=80)
    except Exception:  # noqa: BLE001
        return None


def check_divider_in_context(path: Path, before: Path | None, after: Path | None) -> bool | None:
    """Flash judges the frame next to its neighbours. None when Gemini is unavailable or fails."""
    if not google_api_key():
        return None
    from pipeline.pipeline import check_lot_divider

    candidate = _thumb(path)
    if candidate is None:
        return None
    return check_lot_divider(path.name, candidate, _thumb(before), _thumb(after))


def find_lot_dividers(ordered: list[tuple[Path, str, dict]]) -> set[Path]:
    """Frames that end a lot: low-detail pixels AND Gemini agrees (in context when available).

    Either signal alone keeps the frame as a normal photo.
    """
    if not settings.catalog_divider_splits:
        return set()
    found: set[Path] = set()
    checks = 0
    for i, (path, _key, row) in enumerate(ordered):
        if not _row_can_be_divider(row):
            continue
        fracs = frame_edge_fractions(path)
        if not _fractions_look_blank(fracs):
            continue
        flagged = bool(row.get("is_lot_divider"))
        in_context: bool | None = None
        if checks < MAX_CONTEXT_CHECKS_PER_BATCH:
            checks += 1
            before = ordered[i - 1][0] if i > 0 else None
            after = ordered[i + 1][0] if i + 1 < len(ordered) else None
            in_context = check_divider_in_context(path, before, after)
        is_divider = in_context if in_context is not None else flagged
        _LOG.info(
            "divider candidate file=%s center_edges=%.3f frame_edges=%.3f triage_flag=%s context=%s divider=%s",
            path.name,
            fracs[0] if fracs else -1.0,
            fracs[1] if fracs else -1.0,
            flagged,
            in_context,
            is_divider,
        )
        if is_divider:
            found.add(path)
    return found


def is_lot_divider(path: Path, row: dict) -> bool:
    """Single-frame check without neighbours (triage flag + pixels)."""
    return _row_can_be_divider(row) and bool(row.get("is_lot_divider")) and frame_looks_blank(path)
