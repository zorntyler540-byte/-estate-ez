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


def list_catalog_image_files_sorted(root: Path) -> list[Path]:
    """Catalog image paths under ``root`` (recursive), sorted for stable gallery order."""
    if not root.is_dir():
        return []
    paths = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _CATALOG_SUFFIXES]
    paths.sort(key=lambda p: (str(p.relative_to(root)).lower(), p.name.lower()))
    return paths
