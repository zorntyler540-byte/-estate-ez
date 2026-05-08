"""Catalog / marketplace limits (aligned with CTBids listing constraints)."""

from __future__ import annotations

from pathlib import Path

CTBIDS_MAX_PHOTOS_PER_LOT = 40

_CATALOG_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})


def count_catalog_image_files(root: Path) -> int:
    """Count image-like files under ``root`` (browser uploads are flattened or shallow)."""
    if not root.is_dir():
        return 0
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in _CATALOG_SUFFIXES:
            n += 1
    return n
