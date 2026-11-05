"""Upload names: only stable phone/web names are identity; camera names never drop photos."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.services.upload_names import is_stable_upload_name
from app.upload_storage import save_uploaded_files

DRAFT = "3f2a9c1d-1111-4222-8333-944445555666"


@pytest.mark.parametrize(
    "name",
    [
        f"mobile-{DRAFT}-01.jpg",
        f"mobile-{DRAFT}-vid-02.mov",
        f"append-{DRAFT}-12.jpg",
        "web-0123456789ab-0001.jpg",
        "web-0123456789ab-0042-IMG_1234.jpeg",
        "lot-035/web-0123456789ab-0007-image.heic",
    ],
)
def test_stable_upload_names(name: str) -> None:
    assert is_stable_upload_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "image.jpg",
        "IMG_1234.JPG",
        "IMG_1234_2.JPG",
        "1730000000000.jpg",
        "web-image.jpg",
        "web-0123-0001.jpg",
        "mobile-01.jpg",
        "",
        None,
    ],
)
def test_camera_names_are_not_identity(name) -> None:
    assert is_stable_upload_name(name) is False


def _jpeg(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="JPEG")
    return buf.getvalue()


def _part(name: str, body: bytes) -> SimpleNamespace:
    async def read() -> bytes:
        return body

    return SimpleNamespace(filename=name, read=read)


def _stored(dest) -> list[str]:
    return sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file() and p.suffix == ".jpg")


def test_repeated_ios_image_jpg_names_keep_every_photo(tmp_path, monkeypatch) -> None:
    """P0: iOS sends every picked photo as image.jpg — none may collapse even with skip_existing."""
    monkeypatch.setattr("app.upload_storage.mirror_local_file_to_r2", lambda *a, **k: None)
    dest = tmp_path / "batch"
    dest.mkdir()
    parts = [_part("image.jpg", _jpeg((i * 40, 0, 0))) for i in range(3)]

    async def _run() -> None:
        await save_uploaded_files(dest, parts[:1], skip_existing=True)
        await save_uploaded_files(dest, parts[1:], skip_existing=True)

    asyncio.run(_run())
    assert len(_stored(dest)) == 3


def test_stable_name_retry_is_stored_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.upload_storage.mirror_local_file_to_r2", lambda *a, **k: None)
    dest = tmp_path / "batch"
    dest.mkdir()
    name = "web-0123456789ab-0001-IMG_1234.jpg"

    async def _run() -> list[str]:
        await save_uploaded_files(dest, [_part(name, _jpeg((1, 2, 3)))], skip_existing=True)
        return await save_uploaded_files(dest, [_part(name, _jpeg((1, 2, 3)))], skip_existing=True)

    again = asyncio.run(_run())
    assert again == [name]
    assert _stored(dest) == [name]
