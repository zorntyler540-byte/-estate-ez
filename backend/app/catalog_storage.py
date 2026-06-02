from __future__ import annotations

import shutil
import uuid
from pathlib import Path

_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})
_CATALOG_SUBDIR = "catalog"

# ``app/catalog_storage.py`` → ``backend/``
_BACKEND_DIR = Path(__file__).resolve().parent.parent


def upload_root() -> Path:
    """Absolute catalog upload root (stable regardless of process cwd)."""
    return (_BACKEND_DIR / "data" / "uploads").resolve()


def persist_catalog_photo(account_id: str, source: Path, *, photo_id: str | None = None) -> tuple[str, str]:
    """
    Copy an image into ``catalog/{account_id}/{photo_id}.ext`` under the upload root.

    Permanent catalog copies survive job batch-folder cleanup and DB purge (rows only).
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
    return pid, catalog_storage_key(dest)


def catalog_storage_key(path: Path) -> str:
    """
    Store under ``data/uploads/…`` as a path relative to ``upload_root()`` so DB rows survive cwd changes
    and are easier to relocate behind a volume mount.
    """
    resolved = path.resolve()
    root = upload_root()
    try:
        rel = resolved.relative_to(root)
        return rel.as_posix()
    except ValueError:
        # Fallback: keep absolute posix path (legacy rows use this too).
        return resolved.as_posix()


def resolve_catalog_storage_path(storage_path: str | None) -> Path:
    """
    Resolve a ``Photo.storage_path`` to a file on disk.

    Tries stored value as-is, then ``upload_root / relative_key``, then legacy suffix after ``uploads/``.
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
    return candidates[0].resolve() if candidates else p.resolve()


def catalog_photo_file_on_disk(storage_path: str | None) -> bool:
    if not storage_path or not str(storage_path).strip():
        return False
    try:
        return resolve_catalog_storage_path(storage_path).is_file()
    except OSError:
        return False
