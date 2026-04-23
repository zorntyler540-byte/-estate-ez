"""Detect purple / lavender estate tape and build a high-contrast crop for SKU reading."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path


def _is_lavender_purple_tape_pixel(r: int, g: int, b: int) -> bool:
    """
    Heuristic mask for purple / lavender painter's-style tape (not exact hex — lighting varies).

    Typical lavenders: high blue, red >= green, moderate saturation vs white walls.
    Excludes obvious neutrals and greens; still may catch purple fabric — caller caps bbox area.
    """
    if max(r, g, b) < 55:
        return False
    if b < 95:
        return False
    if r < 85 or g < 55:
        return False
    # purple / lavender: blue channel strong vs green
    if b + 25 < max(r, g):
        return False
    # avoid gray (low spread)
    spread = max(r, g, b) - min(r, g, b)
    if spread < 28:
        return False
    # lavender: enough red+blue vs green
    if r + b < g + 110:
        return False
    return True


def purple_tape_focus_jpeg_from_path(path: Path) -> bytes | None:
    """
    Return a grayscale high-contrast JPEG of the purple-tape region, or None if no plausible region.

    Uses a downscaled pass for speed then maps bbox back to full resolution before crop.
    """
    try:
        from PIL import Image, ImageFilter, ImageOps
    except ImportError:
        return None

    try:
        with Image.open(path) as im:
            rgb = im.convert("RGB")
    except Exception:
        return None

    w, h = rgb.size
    if w < 32 or h < 32:
        return None

    thumb = rgb.copy()
    thumb.thumbnail((640, 640), Image.Resampling.LANCZOS)
    tw, th = thumb.size
    px = thumb.load()
    mask = Image.new("L", (tw, th), 0)
    mp = mask.load()
    for y in range(th):
        for x in range(tw):
            r, g, b = px[x, y]
            if _is_lavender_purple_tape_pixel(r, g, b):
                mp[x, y] = 255

    mask = mask.filter(ImageFilter.MaxFilter(9))
    bbox = mask.getbbox()
    if not bbox:
        return None

    area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    if area < 140 or area > 0.52 * tw * th:
        return None

    sx = w / tw
    sy = h / th
    x0 = int(bbox[0] * sx)
    y0 = int(bbox[1] * sy)
    x1 = min(w, int(bbox[2] * sx) + 1)
    y1 = min(h, int(bbox[3] * sy) + 1)
    pad_x = max(6, int((x1 - x0) * 0.12))
    pad_y = max(6, int((y1 - y0) * 0.12))
    x0 = max(0, x0 - pad_x)
    y0 = max(0, y0 - pad_y)
    x1 = min(w, x1 + pad_x)
    y1 = min(h, y1 + pad_y)
    if x1 - x0 < 24 or y1 - y0 < 24:
        return None

    crop = rgb.crop((x0, y0, x1, y1))
    gray = crop.convert("L")
    hi = ImageOps.autocontrast(gray, cutoff=1)
    out = Image.merge("RGB", (hi, hi, hi))
    buf = BytesIO()
    out.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()
