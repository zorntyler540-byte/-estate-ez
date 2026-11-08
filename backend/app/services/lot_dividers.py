"""Blank divider frames (blank page, empty wall, covered lens) that separate lots in a batch upload."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from app.services import image_preprocess  # noqa: F401  (registers the HEIC opener)

_LOG = logging.getLogger(__name__)

# Gemini alone once skipped real white-plate / close-up frames as "blank", so a divider also has
# to be nearly featureless on the pixels. Edges are measured on a small grayscale copy so sensor
# noise and soft lighting on a wall do not count. The middle of the frame (where any item photo
# puts its subject) must be empty; the border may show the table edge around a sheet of paper.
_PROBE_EDGE = 160
_EDGE_LEVEL = 40
_CENTER_MARGIN = 0.2
MAX_DIVIDER_CENTER_EDGE_FRACTION = 0.01
MAX_DIVIDER_FRAME_EDGE_FRACTION = 0.06


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


def frame_looks_blank(path: Path) -> bool:
    fracs = frame_edge_fractions(path)
    if fracs is None:
        return False
    center, whole = fracs
    return center <= MAX_DIVIDER_CENTER_EDGE_FRACTION and whole <= MAX_DIVIDER_FRAME_EDGE_FRACTION


def triage_row_is_divider(row: dict) -> bool:
    if not bool(row.get("is_lot_divider")):
        return False
    if str(row.get("lot_tape_marking") or "").strip():
        return False
    return not bool(row.get("shows_whole_lot"))


def is_lot_divider(path: Path, row: dict) -> bool:
    """Triage says separator AND the pixels agree. Either alone keeps the frame as a normal photo."""
    return triage_row_is_divider(row) and frame_looks_blank(path)
