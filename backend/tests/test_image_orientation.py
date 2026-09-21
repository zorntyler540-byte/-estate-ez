from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.services.image_preprocess import (
    compress_upload_bytes_if_image,
    image_bytes_need_orientation_reencode,
    jpeg_exif_orientation,
)


def _jpeg_with_orientation(width: int, height: int, orientation: int) -> bytes:
    im = Image.new("RGB", (width, height), (200, 40, 40))
    exif = Image.Exif()
    exif[0x0112] = orientation
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=90, exif=exif)
    return buf.getvalue()


def test_orientation_6_is_detected() -> None:
    data = _jpeg_with_orientation(40, 20, 6)
    with Image.open(BytesIO(data)) as im:
        assert jpeg_exif_orientation(im) == 6
    assert image_bytes_need_orientation_reencode(data, "phone.jpg") is True


def test_orientation_1_does_not_need_reencode() -> None:
    data = _jpeg_with_orientation(40, 20, 1)
    assert image_bytes_need_orientation_reencode(data, "upright.jpg") is False


def test_compress_bakes_exif_orientation_into_pixels() -> None:
    """Phone portrait shots are often 40x20 pixels + orientation 6; display should be 20x40."""
    data = _jpeg_with_orientation(40, 20, 6)
    out, name = compress_upload_bytes_if_image(data, "mobile-lot-01.jpg", max_edge=4096, quality=90)
    assert name.endswith(".jpg")
    with Image.open(BytesIO(out)) as im:
        assert jpeg_exif_orientation(im) in (0, 1)
        assert im.size[0] < im.size[1]
        assert im.size == (20, 40)
