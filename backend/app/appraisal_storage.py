from __future__ import annotations

import uuid
from pathlib import Path

_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})
_APPRAISAL_SUBDIR = "appraisals"

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def upload_root() -> Path:
    """Same upload root as catalog photos (``backend/data/uploads``)."""
    return (_BACKEND_DIR / "data" / "uploads").resolve()


def appraisal_storage_key(path: Path) -> str:
    resolved = path.resolve()
    root = upload_root()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def persist_appraisal_photo_bytes(
    account_id: str,
    check_id: str,
    *,
    index: int,
    data: bytes,
    suffix: str = ".jpg",
) -> str:
    """Write one appraisal frame under ``appraisals/{account_id}/{check_id}/``."""
    ext = (suffix or ".jpg").lower()
    if ext not in _IMAGE_SUFFIXES:
        ext = ".jpg"
    dest_dir = (upload_root() / _APPRAISAL_SUBDIR / account_id / check_id).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{index:02d}{ext}"
    dest.write_bytes(data)
    return appraisal_storage_key(dest)


def resolve_appraisal_storage_path(storage_path: str | None) -> Path:
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
    return candidates[0] if candidates else Path("__missing__")


def appraisal_photo_file_on_disk(storage_path: str | None) -> bool:
    p = resolve_appraisal_storage_path(storage_path)
    return p.is_file()
