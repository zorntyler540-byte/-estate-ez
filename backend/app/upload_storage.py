from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.catalog_storage import upload_root
from app.services.image_preprocess import compress_upload_bytes_if_image

UPLOAD_ROOT = upload_root()


def new_batch_dir(account_id: str) -> Path:
    batch_id = str(uuid.uuid4())
    dest = (upload_root() / account_id / batch_id).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def resolve_account_upload_folder(account_id: str, input_folder: str) -> Path:
    """Resolve a job input folder, restricted to this account's upload root."""
    root = (upload_root() / account_id).resolve()
    folder = Path(input_folder).expanduser().resolve()
    try:
        folder.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid job upload path") from exc
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail="Job upload folder is missing")
    return folder


async def save_uploaded_files(
    dest_root: Path,
    files: list[UploadFile],
    *,
    concurrency: int = 12,
) -> None:
    """
    Save uploaded parts under dest_root in parallel (bounded by ``concurrency``).
    Image files may be passed through or resized/compressed with Pillow in a worker thread before writing.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    root = dest_root.resolve()
    sem = asyncio.Semaphore(concurrency)

    async def _save_one(uf: UploadFile) -> None:
        async with sem:
            body = await uf.read()
            try:
                body, stor_name = await asyncio.to_thread(
                    compress_upload_bytes_if_image,
                    body,
                    uf.filename or "",
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            raw_name = stor_name.replace("\\", "/").lstrip("/")
            rel = Path(raw_name)
            if ".." in rel.parts:
                raise HTTPException(status_code=400, detail=f"Invalid file path: {raw_name!r}")
            target = (root / rel).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid file path") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
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
            target.write_bytes(body)

    await asyncio.gather(*[_save_one(uf) for uf in files])
