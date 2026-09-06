"""Catalog photo triage roles (persist + CT Seller listing filters)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
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


_SKU_TAPE_NAME_HINTS = (
    "skutape",
    "lottape",
    "estatetape",
    "sku-tape",
    "lot-tape",
    "estate-tape",
)


def filename_looks_like_sku_tape(name: object | None) -> bool:
    """True when the original filename is an obvious dedicated SKU-tape shot."""
    raw = str(name or "").strip().lower()
    if not raw:
        return False
    base = Path(raw.replace("\\", "/")).name
    compact = base.replace(" ", "").replace("_", "").replace("-", "")
    if any(h.replace("-", "") in compact for h in _SKU_TAPE_NAME_HINTS):
        return True
    return "sku tape" in base or "lot tape" in base or "estate tape" in base


# Dedicated tape close-ups usually paint a large share of the frame this color.
# Mid coverage + not a group shot: omit. High coverage: omit even if Gemini
# marked shows_whole_lot (tape filling the frame is not a product photo).
_CTBIDS_TAPE_COVERAGE_MIN = 0.20
_CTBIDS_TAPE_COVERAGE_CLOSEUP = 0.35


def _photo_name_looks_like_sku_tape(photo: Any) -> bool:
    if filename_looks_like_sku_tape(getattr(photo, "original_name", None)):
        return True
    storage = getattr(photo, "storage_path", None)
    return bool(storage) and filename_looks_like_sku_tape(Path(str(storage)).name)


def should_omit_from_ctbids_listing(photo: Any, *, path: Path | None = None) -> bool:
    """Drop dedicated SKU-tape / unusable frames from CT Bids posts.

    Relies on stored ``photo_role`` first. Also catches add-on shots and mis-tagged
    tape close-ups via filename and a cheap color-coverage check. Group/item shots
    with tape at the edge (``shows_whole_lot``) stay in the listing unless the
    frame is almost entirely tape.
    """
    role = normalize_catalog_photo_role(getattr(photo, "photo_role", None))
    if role in {"tape", "unusable"}:
        return True
    if _photo_name_looks_like_sku_tape(photo):
        return True
    if bool(getattr(photo, "shows_measuring_scale", False)):
        return False
    whole = bool(getattr(photo, "shows_whole_lot", False))
    if path is None:
        return False
    try:
        from app.services.tape_focus import estate_tape_pixel_ratio

        # Yellow is used for measuring tape and also matches wood/beige — omit
        # only purple / blue / neon-green estate lot tape.
        ratio = estate_tape_pixel_ratio(path, include_yellow=False)
    except Exception:
        return False
    if ratio is None:
        return False
    if ratio >= _CTBIDS_TAPE_COVERAGE_CLOSEUP:
        return True
    return ratio >= _CTBIDS_TAPE_COVERAGE_MIN and not whole


def detected_sku_tape_role_for_file(path: Path, original_name: object | None = None) -> str | None:
    """Return ``tape`` when a newly saved file is a dedicated SKU-tape close-up."""
    probe = SimpleNamespace(
        photo_role=None,
        original_name=original_name or path.name,
        shows_whole_lot=False,
        shows_measuring_scale=False,
        storage_path=str(path),
    )
    if should_omit_from_ctbids_listing(probe, path=path):
        return "tape"
    return None


def coerce_sku_tape_listing_attrs(path: Path, listing: PhotoListingAttrs) -> PhotoListingAttrs:
    """Force dedicated SKU-tape close-ups to ``tape`` when persisting catalog photos."""
    if listing.photo_role == "unusable":
        return listing
    probe = SimpleNamespace(
        photo_role=listing.photo_role,
        original_name=path.name,
        shows_whole_lot=listing.shows_whole_lot,
        shows_measuring_scale=listing.shows_measuring_scale,
        storage_path=str(path),
    )
    if listing.photo_role == "tape" or not should_omit_from_ctbids_listing(probe, path=path):
        return listing
    return PhotoListingAttrs(
        photo_role="tape",
        distinct_item_id=listing.distinct_item_id,
        is_primary_front=False,
        shows_whole_lot=listing.shows_whole_lot,
        shows_measuring_scale=listing.shows_measuring_scale,
        label_legibility=listing.label_legibility,
    )


def assign_detected_sku_tape_roles(photos: Iterable[Any], listing: Iterable[Any]) -> int:
    """Set ``photo_role='tape'`` on frames omitted from a CT Bids listing. Returns count changed."""
    kept_ids = {getattr(photo, "id", id(photo)) for photo in listing}
    changed = 0
    for photo in photos:
        if getattr(photo, "id", id(photo)) in kept_ids:
            continue
        role = normalize_catalog_photo_role(getattr(photo, "photo_role", None))
        if role in {"tape", "unusable"}:
            continue
        photo.photo_role = "tape"
        changed += 1
    return changed


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


def filter_photos_excluding_sku_tape(
    photos: Iterable[Any],
    *,
    path_for: Any | None = None,
) -> list[Any]:
    """Keep catalog photos for CT Seller / CT Bids (drop dedicated SKU-tape frames)."""
    kept: list[Any] = []
    for photo in photos:
        path = path_for(photo) if path_for is not None else None
        if not should_omit_from_ctbids_listing(photo, path=path):
            kept.append(photo)
    return kept


def photos_for_ctbids_listing(
    photos: Iterable[Any],
    *,
    path_for: Any | None = None,
) -> list[Any]:
    """Photos to upload to seller.ctbids.com — SKU-tape close-ups are never posted."""
    return filter_photos_excluding_sku_tape(photos, path_for=path_for)
