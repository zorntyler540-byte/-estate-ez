"""Blank divider frames (blank page, empty wall, covered lens) that separate lots in a batch upload.

Near-uniform white paper and covered/black lenses are dividers from a local pixel check.
Ambiguous frames (a crease, a shadow, a triage flag on a low-detail shot) get a Flash
neighbour check. Those calls run together, bounded by ``CATALOG_DIVIDER_CHECK_CONCURRENCY``.
There is no per-batch cap: a divider is never dropped because the batch is long.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

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
# A creased or shadowed sheet still gets the neighbour check. The strict limits above are only
# the fallback when that Gemini call is unavailable.
LOOSE_DIVIDER_CENTER_EDGE_FRACTION = 0.16
LOOSE_DIVIDER_FRAME_EDGE_FRACTION = 0.20
_CONTEXT_THUMB_EDGE = 512

# Near-uniform paper / wall / covered lens. Tight on purpose: a white plate, a figurine on a
# seamless backdrop, and a creased sheet stay ambiguous and go to the neighbour check.
_WHITE_LUMA = 220
_DARK_LUMA = 28
_CONFIDENT_WHITE_MEAN = 226.0
_CONFIDENT_WHITE_STDEV = 7.5
_CONFIDENT_WHITE_FRACTION = 0.985
_CONFIDENT_WHITE_CENTER_EDGE = 0.035
_CONFIDENT_WHITE_FRAME_EDGE = 0.05
_CONFIDENT_DARK_MEAN = 16.0
_CONFIDENT_DARK_STDEV = 6.0
_CONFIDENT_DARK_FRACTION = 0.98
_CONFIDENT_DARK_CENTER_EDGE = 0.03
_CONFIDENT_DARK_FRAME_EDGE = 0.045


@dataclass(frozen=True)
class BlankProbe:
    center_edges: float
    frame_edges: float
    mean_luma: float
    stdev_luma: float
    white_fraction: float
    dark_fraction: float


def _strong_edge_fraction(edges: Image.Image) -> float | None:
    hist = edges.histogram()
    total = sum(hist)
    return sum(hist[_EDGE_LEVEL:]) / total if total else None


def frame_blank_probe(path: Path) -> BlankProbe | None:
    """Edge density plus how uniform the frame is. None when the file can't be read."""
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
    stat = ImageStat.Stat(gray)
    hist = gray.histogram()
    total = sum(hist) or 1
    return BlankProbe(
        center_edges=center,
        frame_edges=whole,
        mean_luma=float(stat.mean[0]),
        stdev_luma=float(stat.stddev[0]),
        white_fraction=sum(hist[_WHITE_LUMA:]) / total,
        dark_fraction=sum(hist[:_DARK_LUMA]) / total,
    )


def frame_edge_fractions(path: Path) -> tuple[float, float] | None:
    """(center, whole-frame) share of strong-edge pixels; None when the file can't be read."""
    probe = frame_blank_probe(path)
    if probe is None:
        return None
    return probe.center_edges, probe.frame_edges


def _fractions_look_blank(fracs: tuple[float, float] | None) -> bool:
    if fracs is None:
        return False
    center, whole = fracs
    return center <= MAX_DIVIDER_CENTER_EDGE_FRACTION and whole <= MAX_DIVIDER_FRAME_EDGE_FRACTION


def frame_looks_blank(path: Path) -> bool:
    return _fractions_look_blank(frame_edge_fractions(path))


def _fractions_might_be_blank(fracs: tuple[float, float] | None) -> bool:
    if fracs is None:
        return False
    center, whole = fracs
    return center <= LOOSE_DIVIDER_CENTER_EDGE_FRACTION and whole <= LOOSE_DIVIDER_FRAME_EDGE_FRACTION


def frame_is_confident_blank(probe: BlankProbe | None) -> bool:
    """True for near-uniform white paper or a covered/black lens. Not for creases or items."""
    if probe is None:
        return False
    white = (
        probe.mean_luma >= _CONFIDENT_WHITE_MEAN
        and probe.stdev_luma <= _CONFIDENT_WHITE_STDEV
        and probe.white_fraction >= _CONFIDENT_WHITE_FRACTION
        and probe.center_edges <= _CONFIDENT_WHITE_CENTER_EDGE
        and probe.frame_edges <= _CONFIDENT_WHITE_FRAME_EDGE
    )
    dark = (
        probe.mean_luma <= _CONFIDENT_DARK_MEAN
        and probe.stdev_luma <= _CONFIDENT_DARK_STDEV
        and probe.dark_fraction >= _CONFIDENT_DARK_FRACTION
        and probe.center_edges <= _CONFIDENT_DARK_CENTER_EDGE
        and probe.frame_edges <= _CONFIDENT_DARK_FRAME_EDGE
    )
    return white or dark


def _row_has_tape_marking(row: dict) -> bool:
    return bool(str(row.get("lot_tape_marking") or "").strip())


def _row_can_be_divider(row: dict) -> bool:
    if _row_has_tape_marking(row):
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


def _edge_pair(probe: BlankProbe | None) -> tuple[float, float] | None:
    if probe is None:
        return None
    return probe.center_edges, probe.frame_edges


def _divider_check_workers(n: int) -> int:
    cap = max(1, int(settings.catalog_divider_check_concurrency))
    return max(1, min(n, cap))


def _check_ambiguous(
    pending: list[tuple[Path, Path | None, Path | None]],
) -> list[bool | None]:
    """Neighbour checks for every ambiguous frame. Bounded concurrency; none are skipped."""
    if not pending:
        return []
    workers = _divider_check_workers(len(pending))
    if workers == 1:
        return [check_divider_in_context(path, before, after) for path, before, after in pending]
    out: list[bool | None] = [None] * len(pending)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lot-divider") as pool:
        futures = {
            pool.submit(check_divider_in_context, path, before, after): i
            for i, (path, before, after) in enumerate(pending)
        }
        for future in as_completed(futures):
            i = futures[future]
            try:
                out[i] = future.result()
            except Exception:  # noqa: BLE001
                _LOG.warning(
                    "divider neighbour check failed file=%s",
                    pending[i][0].name,
                    exc_info=True,
                )
                out[i] = None
    return out


def find_lot_dividers(ordered: list[tuple[Path, str, dict]]) -> set[Path]:
    """Frames that end a lot.

    A near-uniform blank is a divider on its own. Anything less clear needs the neighbour
    check (or, if that call cannot run, both the triage flag and a strict blank). A frame
    that qualifies is always returned — a long batch does not drop later dividers.
    """
    if not settings.catalog_divider_splits:
        return set()
    found: set[Path] = set()
    pending: list[tuple[int, Path, Path | None, Path | None, bool, bool, BlankProbe | None]] = []
    for i, (path, _key, row) in enumerate(ordered):
        if _row_has_tape_marking(row):
            continue
        probe = frame_blank_probe(path)
        if probe is None:
            continue
        flagged = bool(row.get("is_lot_divider"))
        fracs = _edge_pair(probe)
        strict = _fractions_look_blank(fracs)
        if frame_is_confident_blank(probe):
            found.add(path)
            _LOG.info(
                "divider candidate file=%s center_edges=%.3f frame_edges=%.3f "
                "mean=%.1f stdev=%.2f triage_flag=%s context=local divider=%s",
                path.name,
                probe.center_edges,
                probe.frame_edges,
                probe.mean_luma,
                probe.stdev_luma,
                flagged,
                True,
            )
            continue
        # A real group shot is not a divider. A uniform frame already returned above.
        if not _row_can_be_divider(row):
            continue
        # Triage calling it a divider, or a loose edge test, spends one neighbour check.
        if not flagged and not _fractions_might_be_blank(fracs):
            continue
        before = ordered[i - 1][0] if i > 0 else None
        after = ordered[i + 1][0] if i + 1 < len(ordered) else None
        pending.append((i, path, before, after, flagged, strict, probe))

    if pending:
        _LOG.info(
            "divider neighbour checks candidates=%d workers=%d",
            len(pending),
            _divider_check_workers(len(pending)),
        )
    verdicts = _check_ambiguous([(path, before, after) for _i, path, before, after, _f, _s, _p in pending])
    for (_i, path, _before, _after, flagged, strict, probe), in_context in zip(pending, verdicts):
        # Neighbour check decides. Without it, only a strict blank that triage also flagged splits.
        # A local confident blank was already kept above and is not sent here.
        is_divider = in_context if in_context is not None else (flagged and strict)
        fracs = _edge_pair(probe)
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
    """Single-frame check without neighbours.

    A near-uniform blank counts even when triage did not flag it. Anything else still needs
    the triage flag and the strict edge test. Estate-tape markings are never dividers.
    """
    if _row_has_tape_marking(row):
        return False
    probe = frame_blank_probe(path)
    if probe is None:
        return False
    if frame_is_confident_blank(probe):
        return True
    return _row_can_be_divider(row) and bool(row.get("is_lot_divider")) and _fractions_look_blank(
        _edge_pair(probe)
    )
