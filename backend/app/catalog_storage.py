from __future__ import annotations

from pathlib import Path

# ``app/catalog_storage.py`` → ``backend/``
_BACKEND_DIR = Path(__file__).resolve().parent.parent


def upload_root() -> Path:
    """Absolute catalog upload root (stable regardless of process cwd)."""
    return (_BACKEND_DIR / "data" / "uploads").resolve()


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
