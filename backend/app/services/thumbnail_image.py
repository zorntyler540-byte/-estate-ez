from __future__ import annotations

try:
    from pillow_heif import register_heif_opener  # type: ignore[import-untyped]

    register_heif_opener()
except ImportError:
    # HEIC/HEIF catalog photos need the same opener as ``image_preprocess`` for ``Image.open``.
    pass

from io import BytesIO
from pathlib import Path


def thumbnail_jpeg_bytes(src: Path, max_px: int) -> bytes:
    """Resize image to fit within max_px square; output JPEG bytes."""
    from PIL import Image, ImageOps

    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "P"):
            rgba = im.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[3])
            im_rgb = background
        elif im.mode != "RGB":
            im_rgb = im.convert("RGB")
        else:
            im_rgb = im
        # Bilinear is much faster than LANCZOS for large→small dashboard thumbs; quality is fine at listing sizes.
        resample = Image.Resampling.BILINEAR if max_px <= 512 else Image.Resampling.LANCZOS
        im_rgb.thumbnail((max_px, max_px), resample)
        buf = BytesIO()
        im_rgb.save(buf, format="JPEG", quality=86, optimize=True)
        return buf.getvalue()
