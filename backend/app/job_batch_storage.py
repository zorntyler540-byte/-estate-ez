"""Mirror job upload batches to R2 so catalog can run on a worker / other replica.

Local ``data/uploads/{account_id}/{batch_id}/`` is ephemeral on Railway unless a volume
is attached. When R2 is configured we also store the same files under
``job-batches/{account_id}/{batch_id}/…`` and restore them before ``process_job`` if
the local folder is missing (fixes "Input folder not found").
"""

from __future__ import annotations

import logging
from pathlib import Path

from app import object_storage
from app.catalog_storage import upload_root

logger = logging.getLogger(__name__)

_JOB_BATCH_PREFIX = "job-batches"
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})


def parse_batch_folder(folder: Path) -> tuple[str, str] | None:
    """Return ``(account_id, batch_id)`` when ``folder`` is under the upload root."""
    try:
        rel = folder.resolve().relative_to(upload_root())
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 2:
        return None
    account_id, batch_id = parts[0], parts[1]
    if not account_id or not batch_id or account_id == "catalog":
        return None
    return account_id, batch_id


def r2_key_for_file(account_id: str, batch_id: str, relative_name: str) -> str:
    rel = relative_name.replace("\\", "/").lstrip("/")
    return f"{_JOB_BATCH_PREFIX}/{account_id}/{batch_id}/{rel}"


def mirror_local_file_to_r2(local_path: Path, dest_root: Path) -> None:
    """Best-effort upload of one saved job file to R2 (no-op if R2 unset)."""
    if not object_storage.r2_configured():
        return
    parsed = parse_batch_folder(dest_root)
    if not parsed:
        return
    account_id, batch_id = parsed
    try:
        rel = local_path.resolve().relative_to(dest_root.resolve())
    except ValueError:
        return
    key = r2_key_for_file(account_id, batch_id, str(rel))
    try:
        object_storage.put_file(key, str(local_path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("R2 job-batch mirror failed key=%s err=%s", key, exc)


def mirror_folder_to_r2(folder: Path) -> int:
    """Mirror all images in a job batch folder to R2. Returns files uploaded."""
    if not object_storage.r2_configured():
        return 0
    parsed = parse_batch_folder(folder)
    if not parsed or not folder.is_dir():
        return 0
    account_id, batch_id = parsed
    n = 0
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        try:
            rel = path.resolve().relative_to(folder.resolve())
        except ValueError:
            continue
        key = r2_key_for_file(account_id, batch_id, str(rel))
        try:
            object_storage.put_file(key, str(path))
            n += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("R2 job-batch mirror failed key=%s err=%s", key, exc)
    return n


def restore_job_folder_from_r2(folder: Path) -> int:
    """
    If ``folder`` is missing or empty, download ``job-batches/…`` objects into it.
    Returns number of files restored (0 if nothing to restore).
    """
    if not object_storage.r2_configured():
        return 0
    parsed = parse_batch_folder(folder)
    if not parsed:
        return 0
    account_id, batch_id = parsed
    prefix = f"{_JOB_BATCH_PREFIX}/{account_id}/{batch_id}/"
    keys = object_storage.list_keys(prefix)
    if not keys:
        return 0
    folder.mkdir(parents=True, exist_ok=True)
    restored = 0
    for key in keys:
        rel = key[len(prefix) :] if key.startswith(prefix) else Path(key).name
        if not rel or ".." in Path(rel).parts:
            continue
        dest = (folder / rel).resolve()
        try:
            dest.relative_to(folder.resolve())
        except ValueError:
            continue
        if dest.exists() and dest.stat().st_size > 0:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            object_storage.download_file(key, str(dest))
            restored += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("R2 job-batch restore failed key=%s err=%s", key, exc)
    if restored:
        logger.info(
            "Restored %d job-batch file(s) from R2 account=%s batch=%s",
            restored,
            account_id,
            batch_id,
        )
    return restored


def ensure_job_input_folder(folder: Path) -> Path:
    """
    Ensure the job input directory exists with at least one image.
    Restores from R2 when the local path was lost (redeploy / other replica / worker).
    """
    folder = folder.resolve()
    has_images = folder.is_dir() and any(
        p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES for p in folder.rglob("*")
    )
    if has_images:
        return folder
    restore_job_folder_from_r2(folder)
    return folder
