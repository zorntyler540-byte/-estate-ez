"""Catalog / marketplace limits (aligned with CTBids listing constraints)."""

from __future__ import annotations

from pathlib import Path

# None = no per-listing photo cap for now. Set to an int (e.g. 40) to re-enforce CTBids-style limits.
CTBIDS_MAX_PHOTOS_PER_LOT: int | None = None

_CATALOG_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})


def ctbids_lot_photo_delta_error(*, current: int, delta: int) -> str | None:
    """
    None if there is no cap, or if ``current + delta`` is within the per-listing photo cap; else an HTTP 400 detail string.

    Use after counting **actual** rows to add (saved files, photos to transfer, etc.), not only multipart part count.
    """
    if delta <= 0:
        return None
    cap = CTBIDS_MAX_PHOTOS_PER_LOT
    if cap is None:
        return None
    if current > cap:
        return (
            f"CTBids allows at most {cap} photos per listing; this lot has {current} (already over the limit). "
            "Remove photos before adding or moving more."
        )
    if current >= cap:
        return f"CTBids allows at most {cap} photos per listing; this lot already has {current}."
    if current + delta > cap:
        return (
            f"CTBids allows at most {cap} photos per listing; this lot has {current} and adding {delta} would exceed the limit."
        )
    return None


def count_catalog_image_files(root: Path) -> int:
    """Count image-like files under ``root`` (browser uploads are flattened or shallow)."""
    if not root.is_dir():
        return 0
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in _CATALOG_SUFFIXES:
            n += 1
    return n


def list_catalog_image_files_sorted(root: Path) -> list[Path]:
    """Catalog image paths under ``root`` (recursive), sorted for stable gallery order."""
    if not root.is_dir():
        return []
    paths = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _CATALOG_SUFFIXES]
    paths.sort(key=lambda p: (str(p.relative_to(root)).lower(), p.name.lower()))
    return paths
