from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from app import object_storage
from app.catalog_limits import is_catalog_video_filename, video_content_type

_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})
_CATALOG_SUBDIR = "catalog"

# ``app/catalog_storage.py`` → ``backend/``
_BACKEND_DIR = Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)


def upload_root() -> Path:
    """Absolute catalog upload root (stable regardless of process cwd)."""
    return (_BACKEND_DIR / "data" / "uploads").resolve()


def persist_catalog_photo(account_id: str, source: Path, *, photo_id: str | None = None) -> tuple[str, str]:
    """
    Store an image at ``catalog/{account_id}/{photo_id}.ext``.

    Always writes a local copy under the upload root (cache / offline). When R2 is configured,
    also uploads the same key to the bucket so files survive Railway redeploys.
    Returns ``(photo_id, storage_path)`` for a new ``Photo`` row.
    """
    if not source.is_file():
        raise FileNotFoundError(f"Catalog source not found: {source}")
    pid = (photo_id or "").strip() or str(uuid.uuid4())
    suffix = source.suffix.lower()
    if suffix not in _IMAGE_SUFFIXES:
        suffix = ".jpg"
    dest_dir = (upload_root() / _CATALOG_SUBDIR / account_id).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{pid}{suffix}"
    shutil.copy2(source, dest)
    key = catalog_storage_key(dest)
    if object_storage.r2_configured():
        try:
            object_storage.put_file(key, str(dest))
        except Exception:
            # Fail the persist if cloud upload fails — otherwise DB would claim a durable file.
            dest.unlink(missing_ok=True)
            raise
    return pid, key


def persist_catalog_video(account_id: str, source: Path, *, video_id: str | None = None) -> tuple[str, str]:
    """Store a lot clip at ``catalog/{account_id}/videos/{video_id}.ext`` (local + R2)."""
    if not source.is_file():
        raise FileNotFoundError(f"Catalog video source not found: {source}")
    if not is_catalog_video_filename(source.name):
        raise ValueError("Video must be MP4, MOV, M4V, or WebM")
    vid = (video_id or "").strip() or str(uuid.uuid4())
    suffix = source.suffix.lower()
    dest_dir = (upload_root() / _CATALOG_SUBDIR / account_id / "videos").resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{vid}{suffix}"
    shutil.copy2(source, dest)
    key = catalog_storage_key(dest)
    if object_storage.r2_configured():
        try:
            object_storage.put_file(key, str(dest), content_type=video_content_type(dest.name))
        except Exception:
            dest.unlink(missing_ok=True)
            raise
    return vid, key


def catalog_storage_key(path: Path) -> str:
    """
    Store under ``data/uploads/…`` as a path relative to ``upload_root()`` so DB rows survive cwd changes
    and map 1:1 to R2 object keys.
    """
    resolved = path.resolve()
    root = upload_root()
    try:
        rel = resolved.relative_to(root)
        return rel.as_posix()
    except ValueError:
        # Fallback: keep absolute posix path (legacy rows use this too).
        return resolved.as_posix()


def _object_key_from_storage_path(storage_path: str) -> str | None:
    """Normalize a DB storage_path into an R2 object key (relative catalog/…)."""
    raw = (storage_path or "").strip().replace("\\", "/")
    if not raw:
        return None
    if raw.startswith("catalog/"):
        return raw
    if "/uploads/" in raw:
        tail = raw.split("/uploads/", 1)[-1].lstrip("/")
        return tail or None
    # Absolute local path under upload root
    try:
        p = Path(raw)
        if p.is_absolute():
            rel = p.resolve().relative_to(upload_root())
            return rel.as_posix()
    except (OSError, ValueError):
        pass
    return raw if "/" in raw else None


def thumb_object_key(storage_path: str | None, max_px: int) -> str | None:
    """Derived R2 key for a cached JPEG thumbnail of a catalog photo."""
    key = _object_key_from_storage_path(storage_path or "")
    if not key:
        return None
    px = max(64, min(int(max_px), 2048))
    if "." in key.rsplit("/", 1)[-1]:
        base, _ext = key.rsplit(".", 1)
        return f"{base}.thumb.{px}.jpg"
    return f"{key}.thumb.{px}.jpg"


def signed_catalog_url(
    storage_path: str | None,
    *,
    max_px: int | None = None,
    expires_seconds: int = 3600,
    generate_if_missing: bool = True,
) -> str:
    """
    Presigned HTTPS URL for the original object, or a cached thumbnail object.

    Thumbnail bytes are generated once and stored beside the original in R2 so later
    grid loads skip Railway egress.

    When ``generate_if_missing`` is False and no cached thumb exists, returns a signed
    URL for the original (fast for mobile grids — avoids Pillow work on the request path).
    """
    if not object_storage.r2_configured():
        raise RuntimeError("R2 is not configured")
    key = _object_key_from_storage_path(storage_path or "")
    if not key:
        raise FileNotFoundError("No storage key for photo")

    if max_px is None:
        return object_storage.presigned_get_url(key, expires_seconds=expires_seconds)

    # Fast path for mobile grids: skip R2 HEAD + Pillow; sign the original.
    # expo-image downscales for the 72px row thumb.
    if not generate_if_missing:
        return object_storage.presigned_get_url(key, expires_seconds=expires_seconds)

    tkey = thumb_object_key(storage_path, max_px)
    if not tkey:
        raise FileNotFoundError("No thumb key for photo")
    try:
        exists = object_storage.object_exists(tkey)
    except Exception:
        exists = False
    if exists:
        return object_storage.presigned_get_url(tkey, expires_seconds=expires_seconds)

    from app.services.thumbnail_image import thumbnail_jpeg_bytes

    src = resolve_catalog_storage_path(storage_path)
    if not src.is_file():
        raise FileNotFoundError("Catalog image missing locally and thumb not in R2")
    body = thumbnail_jpeg_bytes(src, max(64, min(int(max_px), 2048)))
    object_storage.put_bytes(tkey, body, content_type="image/jpeg")
    return object_storage.presigned_get_url(tkey, expires_seconds=expires_seconds)


def resolve_catalog_storage_path(storage_path: str | None) -> Path:
    """
    Resolve a ``Photo.storage_path`` to a readable local file.

    Prefer an existing local cache; if missing and R2 is configured, download the object into
    the local cache path and return it.
    """
    raw = (storage_path or "").strip()
    if not raw:
        return Path("__missing__")
    root = upload_root()
    candidates: list[Path] = []
    p = Path(raw)
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(root / raw)
    norm = raw.replace("\\", "/")
    if "/uploads/" in norm:
        tail = norm.split("/uploads/", 1)[-1].lstrip("/")
        if tail:
            candidates.append(root / tail)
    for c in candidates:
        try:
            resolved = c.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved

    key = _object_key_from_storage_path(raw)
    if key and object_storage.r2_configured():
        cache = (root / key).resolve()
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            object_storage.download_file(key, str(cache))
            if cache.is_file():
                return cache
        except Exception as exc:  # noqa: BLE001
            logger.warning("R2 download failed for %s: %s", key, exc)

    return candidates[0].resolve() if candidates else p.resolve()


def _local_catalog_file(storage_path: str | None) -> Path | None:
    """Return an existing local file for this storage path (no R2 download)."""
    raw = (storage_path or "").strip()
    if not raw:
        return None
    root = upload_root()
    candidates: list[Path] = []
    p = Path(raw)
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(root / raw)
    norm = raw.replace("\\", "/")
    if "/uploads/" in norm:
        tail = norm.split("/uploads/", 1)[-1].lstrip("/")
        if tail:
            candidates.append(root / tail)
    for c in candidates:
        try:
            resolved = c.resolve()
            if resolved.is_file():
                return resolved
        except OSError:
            continue
    return None


def migrate_local_photos_to_r2(
    photo_rows: list[tuple[str, str | None]],
    *,
    limit: int = 500,
    offset: int = 0,
    skip_if_remote: bool = True,
) -> dict[str, int | list[str] | bool | None]:
    """
    Upload existing local catalog files to R2 using the same object keys.

    Processes ``photo_rows[offset : offset + limit]`` so callers can page through
    large catalogs without re-checking earlier rows each time.
    """
    if not object_storage.r2_configured():
        raise RuntimeError("R2 is not configured")

    total = len(photo_rows)
    start = max(0, int(offset))
    end = min(total, start + max(1, int(limit)))
    window = photo_rows[start:end]

    uploaded = 0
    skipped_remote = 0
    skipped_missing = 0
    failed = 0
    errors: list[str] = []

    for photo_id, storage_path in window:
        key = _object_key_from_storage_path(storage_path or "")
        if not key:
            skipped_missing += 1
            continue
        local = _local_catalog_file(storage_path)
        if local is None:
            try:
                if skip_if_remote and object_storage.object_exists(key):
                    skipped_remote += 1
                else:
                    skipped_missing += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                if len(errors) < 20:
                    errors.append(f"{photo_id}: {exc}")
            continue
        try:
            if skip_if_remote and object_storage.object_exists(key):
                skipped_remote += 1
                continue
            object_storage.put_file(key, str(local))
            uploaded += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            if len(errors) < 20:
                errors.append(f"{photo_id}: {exc}")
            logger.warning("R2 migrate failed for %s (%s): %s", photo_id, key, exc)

    next_offset: int | None = end if end < total else None
    return {
        "uploaded": uploaded,
        "skipped_already_in_r2": skipped_remote,
        "skipped_missing_local": skipped_missing,
        "failed": failed,
        "errors": errors,
        "offset": start,
        "next_offset": next_offset,
        "total_photos": total,
        "done": next_offset is None,
    }


def catalog_photo_file_on_disk(storage_path: str | None) -> bool:
    if not storage_path or not str(storage_path).strip():
        return False
    if _local_catalog_file(storage_path) is not None:
        return True
    key = _object_key_from_storage_path(str(storage_path))
    if key and object_storage.r2_configured():
        try:
            return object_storage.object_exists(key)
        except Exception:  # noqa: BLE001
            return False
    return False


def delete_catalog_photo(storage_path: str | None) -> None:
    """Remove local cache (if any) and R2 object (if configured)."""
    raw = (storage_path or "").strip()
    if not raw:
        return
    root = upload_root()
    candidates: list[Path] = []
    p = Path(raw)
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(root / raw)
    norm = raw.replace("\\", "/")
    if "/uploads/" in norm:
        tail = norm.split("/uploads/", 1)[-1].lstrip("/")
        if tail:
            candidates.append(root / tail)
    for c in candidates:
        try:
            resolved = c.resolve()
            if resolved.is_file():
                resolved.unlink(missing_ok=True)
        except OSError:
            continue
    key = _object_key_from_storage_path(raw)
    if key and object_storage.r2_configured():
        object_storage.delete_object(key)
