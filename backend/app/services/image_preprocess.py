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
    from PIL import Image, ImageOps

    from app.services.photo_captured_at import captured_at_from_pil, exif_bytes_with_datetime

    with Image.open(BytesIO(data)) as im:
        captured = captured_at_from_pil(im)
        # Phone JPEGs often rely on EXIF Orientation; without this, saved catalog bytes stay sideways.
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
        im_rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        out = BytesIO()
        save_kw: dict = {"format": "JPEG", "quality": quality, "optimize": True}
        if captured is not None:
            try:
                save_kw["exif"] = exif_bytes_with_datetime(captured)
            except Exception:
                pass
        im_rgb.save(out, **save_kw)
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
    max_edge: int | None = None,
    quality: int | None = None,
) -> tuple[bytes, str]:
    """
    Normalize uploads for catalog + web: HEIC/HEIF → JPEG and ``.jpg`` name; resize other rasters
    using ``CATALOG_UPLOAD_MAX_EDGE`` / ``CATALOG_UPLOAD_JPEG_QUALITY`` (override with ``max_edge`` / ``quality``).

    Returns ``(body, storage_relative_path)`` — storage path matches bytes (e.g. ``.heic`` → ``.jpg``).
    """
    from app.config import settings

    me = int(settings.catalog_upload_max_edge if max_edge is None else max_edge)
    q = int(settings.catalog_upload_jpeg_quality if quality is None else quality)
    raw_name = (filename or "unnamed").replace("\\", "/").lstrip("/")
    path = Path(raw_name)
    ext = path.suffix.lower()
    if ext in {".heic", ".heif"}:
        try:
            out = bytes_to_gemini_jpeg(data, max_edge=me, quality=q)
        except Exception as exc:
            raise ValueError(
                "Could not read HEIC/HEIF (install pillow-heif on the server, or use JPEG photos)."
            ) from exc
        return out, str(path.with_suffix(".jpg"))
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        return data, raw_name
    # Skip a second JPEG pass only when pixels already match display orientation.
    if ext in {".jpg", ".jpeg"} and not image_bytes_need_orientation_reencode(data, filename):
        try:
            from PIL import Image

            with Image.open(BytesIO(data)) as im:
                w, h = im.size
            if max(int(w), int(h)) <= me:
                return data, raw_name
        except Exception:
            pass
    try:
        return bytes_to_gemini_jpeg(data, max_edge=me, quality=q), raw_name
    except Exception:
        return data, raw_name


def jpeg_exif_orientation(im) -> int:
    """EXIF Orientation (tag 274). Phones often store it on the root IFD or the Exif IFD."""
    try:
        exif = im.getexif()
    except Exception:
        return 1
    if not exif:
        return 1
    for src in (exif,):
        try:
            raw = src.get(0x0112)
            if raw is not None:
                return int(raw)
        except Exception:
            pass
    try:
        nested = exif.get_ifd(0x8769)
        raw = nested.get(0x0112) if nested else None
        if raw is not None:
            return int(raw)
    except Exception:
        pass
    return 1


def image_bytes_need_orientation_reencode(data: bytes, filename: str) -> bool:
    """
    True when re-encoding would apply EXIF orientation or HEIC→JPEG — avoids double-compressing
    already-upright catalog bytes from a prior successful upload.
    """
    ext = Path((filename or "").replace("\\", "/").lstrip("/")).suffix.lower()
    if ext in (".heic", ".heif"):
        return True
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        return False
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as im:
            return jpeg_exif_orientation(im) not in (0, 1)
    except Exception:
        return True


def normalize_job_folder_image(job_folder: Path, photo_path: Path) -> Path:
    """
    For images in a job input folder (browser folder upload or server path), rewrite files that still
    rely on EXIF orientation (or HEIC) so ``Photo.storage_path`` pixels match upright display.

    Returns the path to use for processing (may change when ``.heic`` becomes ``.jpg``).
    """
    job_folder = job_folder.resolve()
    photo_path = photo_path.resolve()
    try:
        rel = photo_path.relative_to(job_folder)
    except ValueError:
        return photo_path
    rel_key = rel.as_posix()
    try:
        body = photo_path.read_bytes()
    except OSError:
        return photo_path
    if not image_bytes_need_orientation_reencode(body, rel_key):
        return photo_path
    try:
        out_body, stor_name = compress_upload_bytes_if_image(body, rel_key)
    except (ValueError, OSError):
        return photo_path
    except Exception:
        return photo_path
    out_rel = Path(stor_name.replace("\\", "/").lstrip("/"))
    target = (job_folder / out_rel).resolve()
    try:
        target.relative_to(job_folder)
    except ValueError:
        return photo_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not out_body:
        return photo_path
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_bytes(out_body)
        tmp.replace(target)
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return photo_path
    if target.resolve() != photo_path.resolve() and photo_path.exists():
        photo_path.unlink(missing_ok=True)
    return target
