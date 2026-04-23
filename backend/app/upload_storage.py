from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.services.image_preprocess import compress_upload_bytes_if_image

UPLOAD_ROOT = Path("data") / "uploads"


def new_batch_dir(account_id: str) -> Path:
    batch_id = str(uuid.uuid4())
    dest = (UPLOAD_ROOT / account_id / batch_id).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    return dest


async def save_uploaded_files(
    dest_root: Path,
    files: list[UploadFile],
    *,
    concurrency: int = 5,
) -> None:
    """
    Save uploaded parts under dest_root in parallel (bounded by ``concurrency``).
    Image files are resized/compressed with Pillow in a worker thread before writing.
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
            target.write_bytes(body)

    await asyncio.gather(*[_save_one(uf) for uf in files])
