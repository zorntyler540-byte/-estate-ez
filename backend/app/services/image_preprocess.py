from __future__ import annotations

from io import BytesIO
from pathlib import Path

try:
    from pillow_heif import register_heif_opener  # type: ignore[import-untyped]

    register_heif_opener()
except ImportError:
    # HEIC/HEIF uploads and folder jobs require ``pip install pillow-heif``.
    pass


def bytes_to_gemini_jpeg(
    data: bytes,
    *,
    max_edge: int = 1536,
    quality: int = 82,
) -> bytes:
    """Decode image bytes, downscale to fit max_edge, return JPEG bytes for Gemini."""
    from PIL import Image

    with Image.open(BytesIO(data)) as im:
        if im.mode in ("RGBA", "P"):
            rgba = im.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[3])
            im_rgb = background
        elif im.mode != "RGB":
            im_rgb = im.convert("RGB")
        else:
            im_rgb = im
        im_rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        out = BytesIO()
        im_rgb.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()


def prepare_gemini_image_file(path: Path, *, max_edge: int, quality: int) -> tuple[bytes, str]:
    """
    Read disk file and return (jpeg_bytes, image/jpeg).
    On decode failure, returns raw file bytes with MIME inferred from suffix.
    """
    raw = path.read_bytes()
    ext = path.suffix.lower()
    try:
        return bytes_to_gemini_jpeg(raw, max_edge=max_edge, quality=quality), "image/jpeg"
    except Exception:
        if ext == ".png":
            return raw, "image/png"
        if ext == ".webp":
            return raw, "image/webp"
        if ext in (".heic", ".heif"):
            # Do not send raw HEIC to Gemini; caller tiers will retry or fail.
            raise
        return raw, "image/jpeg"


def compress_upload_bytes_if_image(
    data: bytes,
    filename: str,
    *,
    max_edge: int = 2560,
    quality: int = 88,
) -> tuple[bytes, str]:
    """
    Normalize uploads for catalog + web: HEIC/HEIF → JPEG and ``.jpg`` name; resize other rasters.

    Returns ``(body, storage_relative_path)`` — storage path matches bytes (e.g. ``.heic`` → ``.jpg``).
    """
    raw_name = (filename or "unnamed").replace("\\", "/").lstrip("/")
    path = Path(raw_name)
    ext = path.suffix.lower()
    if ext in {".heic", ".heif"}:
        try:
            out = bytes_to_gemini_jpeg(data, max_edge=max_edge, quality=quality)
        except Exception as exc:
            raise ValueError(
                "Could not read HEIC/HEIF (install pillow-heif on the server, or use JPEG photos)."
            ) from exc
        return out, str(path.with_suffix(".jpg"))
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        return data, raw_name
    try:
        return bytes_to_gemini_jpeg(data, max_edge=max_edge, quality=quality), raw_name
    except Exception:
        return data, raw_name
