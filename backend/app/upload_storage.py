from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from datetime import datetime

from fastapi import HTTPException, UploadFile

from app.catalog_limits import should_store_as_catalog_image
from app.catalog_storage import upload_root
from app.job_batch_storage import mirror_local_file_to_r2
from app.services.image_preprocess import compress_upload_bytes_if_image
from app.services.photo_captured_at import (
    captured_at_from_filename,
    captured_at_from_image_bytes,
    write_captured_at_sidecar,
)

UPLOAD_ROOT = upload_root()


def new_batch_dir(account_id: str) -> Path:
    batch_id = str(uuid.uuid4())
    dest = (upload_root() / account_id / batch_id).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _confined_target(root: Path, raw_name: str) -> Path:
    rel = Path(raw_name.replace("\\", "/").lstrip("/"))
    if ".." in rel.parts:
        raise HTTPException(status_code=400, detail=f"Invalid file path: {raw_name!r}")
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc
    return target


def _already_received(target: Path) -> bool:
    try:
        return target.is_file() and target.stat().st_size > 0
    except OSError:
        return False


def received_file_names(root: Path) -> list[str]:
    """Catalog stills already saved in a job folder (relative POSIX names, sorted)."""
    from app.catalog_limits import list_catalog_image_files_sorted

    base = root.resolve()
    return [p.relative_to(base).as_posix() for p in list_catalog_image_files_sorted(base)]


async def save_uploaded_files(
    dest_root: Path,
    files: list[UploadFile],
    *,
    concurrency: int = 12,
    captured_at: datetime | None = None,
    skip_existing: bool = False,
) -> list[str]:
    """
    Save uploaded parts under dest_root in parallel (bounded by ``concurrency``).
    Image files may be passed through or resized/compressed with Pillow in a worker thread before writing.
    When R2 is configured, each saved file is also mirrored under ``job-batches/…`` so a
    worker or other API replica can restore the batch if local disk is gone.

    ``skip_existing``: phone uploads use stable names (``mobile-<draft>-NN.jpg``), so a non-empty
    file already stored under that name is the same photo arriving again (retry / resume) and
    counts as success. Without it, collisions are renamed ``_2`` (browser folder uploads).

    Returns the stored relative names for every still in ``files`` (written or already present).
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    root = dest_root.resolve()
    sem = asyncio.Semaphore(concurrency)
    stored: list[str] = []

    async def _save_one(uf: UploadFile) -> None:
        async with sem:
            if skip_existing and uf.filename:
                early = _confined_target(root, uf.filename)
                if _already_received(early):
                    stored.append(early.relative_to(root).as_posix())
                    return
            body = await uf.read()
            # A phone clip named ``mobile-…-01.jpg`` must not become a catalog still.
            if not should_store_as_catalog_image(body, uf.filename or ""):
                return
            cap = (
                captured_at_from_image_bytes(body)
                or captured_at_from_filename(Path(uf.filename or ""))
                or captured_at
            )
            try:
                body, stor_name = await asyncio.to_thread(
                    compress_upload_bytes_if_image,
                    body,
                    uf.filename or "",
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            raw_name = stor_name.replace("\\", "/").lstrip("/")
            target = _confined_target(root, raw_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            if skip_existing and _already_received(target):
                stored.append(target.relative_to(root).as_posix())
                return
            if target.exists() and target.stat().st_size > 0:
                stem = target.stem
                suffix = target.suffix
                n = 2
                while target.exists():
                    target = (target.parent / f"{stem}_{n}{suffix}").resolve()
                    try:
                        target.relative_to(root)
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail="Invalid file path") from exc
                    n += 1
            if not body:
                raise HTTPException(
                    status_code=400,
                    detail=f"Empty image file: {raw_name!r} (retake or re-export the photo).",
                )
            # Write-then-rename: a concurrent retry of the same name must never see a
            # half-written file and count it as already received.
            tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
            tmp.write_bytes(body)
            os.replace(tmp, target)
            if cap is not None:
                side = write_captured_at_sidecar(target, cap)
                await asyncio.to_thread(mirror_local_file_to_r2, side, root)
            await asyncio.to_thread(mirror_local_file_to_r2, target, root)
            stored.append(target.relative_to(root).as_posix())

    await asyncio.gather(*[_save_one(uf) for uf in files])
    return stored
