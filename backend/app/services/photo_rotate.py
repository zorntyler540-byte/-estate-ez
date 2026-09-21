"""Rotate catalog photo pixels (clockwise 90 / counter-clockwise 90 / 180) and bake EXIF."""

from __future__ import annotations

from io import BytesIO

ALLOWED_ROTATE_DEGREES = frozenset({90, -90, 180})


def rotate_image_bytes(data: bytes, degrees: int, *, quality: int = 90) -> bytes:
    """
    Bake EXIF orientation into pixels, then rotate.

    ``degrees`` matches the lot-details UI: positive 90 is clockwise (same as canvas rotate).
    Pillow's ``Image.rotate`` is counter-clockwise, so clockwise 90 is ``rotate(-90)``.
    """
    from PIL import Image, ImageOps

    if int(degrees) not in ALLOWED_ROTATE_DEGREES:
        raise ValueError("degrees must be 90, -90, or 180")
    q = max(40, min(int(quality), 95))
    with Image.open(BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im) or im
        # User degrees: +90 = clockwise. PIL: positive = CCW.
        im = im.rotate(-int(degrees), expand=True)
        if im.mode in ("RGBA", "P"):
            rgba = im.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[3])
            rgb = background
        elif im.mode != "RGB":
            rgb = im.convert("RGB")
        else:
            rgb = im
        out = BytesIO()
        rgb.save(out, format="JPEG", quality=q, optimize=True)
        return out.getvalue()
