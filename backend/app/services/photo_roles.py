"""Catalog photo triage roles (persist + CT Seller listing filters)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# Matches pipeline PhotoRole literals.
CATALOG_PHOTO_ROLES = frozenset({"hero", "item_front", "angle_only", "tape", "unusable"})


@dataclass(frozen=True)
class PhotoListingAttrs:
    """Persisted triage fields used for listing gallery order and role badges."""

    photo_role: str | None = None
    distinct_item_id: str | None = None
    is_primary_front: bool = False
    shows_whole_lot: bool = False
    shows_measuring_scale: bool = False
    label_legibility: str | None = None


def _as_bool(value: object | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _label_legibility(raw: object | None) -> str | None:
    text = str(raw or "").strip().lower()
    return text if text in {"clear", "partial", "none"} else None


def repaired_listing_attrs(triage_rows: list[dict[str, Any]] | None) -> dict[str, PhotoListingAttrs]:
    """Map triage filename (and basename) to listing attrs with extra Group tags demoted.

    Flash often labels a single figurine as ``hero``. Keep ``hero`` only for true group shots
    (``shows_whole_lot``) or, when none exist, the first cover-tagged frame.
    """
    rows: list[tuple[str, dict[str, Any]]] = []
    for row in triage_rows or []:
        key = str(row.get("filename") or "").strip()
        if key:
            rows.append((key, row))

    has_whole_lot = any(
        _as_bool(row.get("shows_whole_lot"))
        and normalize_catalog_photo_role(row.get("photo_role")) not in {"tape", "unusable"}
        for _key, row in rows
    )
    cover_key: str | None = None
    if not has_whole_lot:
        for key, row in rows:
            if normalize_catalog_photo_role(row.get("photo_role")) == "hero":
                cover_key = key
                break

    out: dict[str, PhotoListingAttrs] = {}
    for key, row in rows:
        role = normalize_catalog_photo_role(row.get("photo_role"))
        whole = _as_bool(row.get("shows_whole_lot"))
        measure = _as_bool(row.get("shows_measuring_scale"))
        primary = _as_bool(row.get("is_primary_front_for_item"))
        item_id = str(row.get("distinct_item_id") or "").strip() or None
        label = _label_legibility(row.get("label_legibility"))

        if role in {"tape", "unusable"}:
            pass
        elif measure:
            if role == "hero":
                role = "angle_only"
        elif whole:
            role = "hero"
        elif role == "hero" and key != cover_key:
            role = "item_front"

        attrs = PhotoListingAttrs(
            photo_role=role,
            distinct_item_id=item_id,
            is_primary_front=primary,
            shows_whole_lot=whole,
            shows_measuring_scale=measure,
            label_legibility=label,
        )
        out[key] = attrs
        base = Path(key).name
        if base != key:
            out[base] = attrs
    return out


def listing_attrs_by_path(
    paths: list[Path],
    triage_rows: list[dict[str, Any]] | None,
    *,
    entries: list[tuple[Path, str]] | None = None,
) -> dict[Path, PhotoListingAttrs]:
    """Map catalog Path objects to repaired listing triage attrs."""
    by_key = repaired_listing_attrs(triage_rows)
    key_by_path: dict[Path, str] = {}
    if entries:
        for path, key in entries:
            key_by_path[path] = (key or path.name).strip() or path.name

    empty = PhotoListingAttrs()
    out: dict[Path, PhotoListingAttrs] = {}
    for path in paths:
        key = key_by_path.get(path) or path.name
        attrs = by_key.get(key)
        if attrs is None and key != path.name:
            attrs = by_key.get(path.name)
        if attrs is None:
            attrs = by_key.get(Path(key).name)
        out[path] = attrs or empty
    return out



def normalize_catalog_photo_role(raw: object | None) -> str | None:
    """Return a known triage role or None."""
    if raw is None:
        return None
    role = str(raw).strip().lower()
    return role if role in CATALOG_PHOTO_ROLES else None


def is_sku_tape_photo_role(role: object | None) -> bool:
    """True when this frame is a dedicated estate SKU-tape shot."""
    return normalize_catalog_photo_role(role) == "tape"


def is_hero_eligible_photo_role(role: object | None) -> bool:
    """False for SKU-tape and unusable frames — they must never be the listing cover."""
    normalized = normalize_catalog_photo_role(role)
    return normalized not in ("tape", "unusable")


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
    """Keep catalog photos for CT Seller / CT Bids (drop dedicated ``tape`` role frames)."""
    return [p for p in photos if not is_sku_tape_photo_role(getattr(p, "photo_role", None))]


def photos_for_ctbids_listing(photos: Iterable[Any]) -> list[Any]:
    """Photos to upload to seller.ctbids.com — SKU-tape close-ups are never posted."""
    return filter_photos_excluding_sku_tape(photos)
