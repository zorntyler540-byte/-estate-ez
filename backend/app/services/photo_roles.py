"""Catalog photo triage roles (persist + CT Seller listing filters)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

# Matches pipeline PhotoRole literals.
CATALOG_PHOTO_ROLES = frozenset({"hero", "item_front", "angle_only", "tape", "unusable"})


def normalize_catalog_photo_role(raw: object | None) -> str | None:
    """Return a known triage role or None."""
    if raw is None:
        return None
    role = str(raw).strip().lower()
    return role if role in CATALOG_PHOTO_ROLES else None


def is_sku_tape_photo_role(role: object | None) -> bool:
    """True when this frame is a dedicated estate SKU-tape shot."""
    return normalize_catalog_photo_role(role) == "tape"


def photo_roles_by_path(
    paths: list[Path],
    triage_rows: list[dict[str, Any]] | None,
    *,
    entries: list[tuple[Path, str]] | None = None,
) -> dict[Path, str | None]:
    """Map catalog Path objects to triage ``photo_role`` values."""
    role_by_key: dict[str, str | None] = {}
    for row in triage_rows or []:
        key = str(row.get("filename") or "").strip()
        if not key:
            continue
        role_by_key[key] = normalize_catalog_photo_role(row.get("photo_role"))

    key_by_path: dict[Path, str] = {}
    if entries:
        for path, key in entries:
            key_by_path[path] = (key or path.name).strip() or path.name

    out: dict[Path, str | None] = {}
    for path in paths:
        key = key_by_path.get(path) or path.name
        role = role_by_key.get(key)
        if role is None and key != path.name:
            role = role_by_key.get(path.name)
        if role is None:
            # Triage sometimes stores basename only / path-style keys.
            role = role_by_key.get(Path(key).name)
        out[path] = role
    return out


def filter_photos_excluding_sku_tape(photos: Iterable[Any]) -> list[Any]:
    """Keep catalog photos for CT Seller (drop dedicated ``tape`` role frames)."""
    return [p for p in photos if not is_sku_tape_photo_role(getattr(p, "photo_role", None))]
