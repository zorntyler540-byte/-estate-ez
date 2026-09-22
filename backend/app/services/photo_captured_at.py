"""Camera / capture timestamps for catalog photos.

``Photo.created_at`` is when the file hit the server. Phone Sync and folder
jobs often write a whole lot in a few seconds, which is not shoot time.
Prefer EXIF DateTimeOriginal (or a sidecar written from the phone shutter).
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

EXIF_DATETIME = 0x0132
EXIF_DATETIME_ORIGINAL = 0x9003
EXIF_DATETIME_DIGITIZED = 0x9004
EXIF_IFD = 0x8769


def captured_at_from_epoch_ms(raw: int | float | str | None) -> datetime | None:
    if raw is None or raw == "":
        return None
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n > 10_000_000_000:
        n = n / 1000.0
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_exif_datetime(raw: object) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text[:26], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def captured_at_from_image_bytes(data: bytes) -> datetime | None:
    if not data:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(BytesIO(data)) as im:
            return captured_at_from_pil(im)
    except Exception:
        return None


def captured_at_from_filename(path: Path) -> datetime | None:
    """Phone capture writes ``{Date.now()}.jpg`` when EXIF was stripped."""
    stem = path.stem
    if stem.isdigit() and 12 <= len(stem) <= 16:
        return captured_at_from_epoch_ms(stem)
    return None


def captured_at_from_path(path: Path) -> datetime | None:
    side = captured_at_from_sidecar(path)
    if side is not None:
        return side
    try:
        from_bytes = captured_at_from_image_bytes(path.read_bytes())
    except OSError:
        from_bytes = None
    if from_bytes is not None:
        return from_bytes
    return captured_at_from_filename(path)


def captured_at_from_pil(im) -> datetime | None:
    try:
        exif = im.getexif()
    except Exception:
        return None
    if not exif:
        return None
    sources = [exif]
    try:
        nested = exif.get_ifd(EXIF_IFD)
        if nested:
            sources.append(nested)
    except Exception:
        pass
    for src in sources:
        for tag in (EXIF_DATETIME_ORIGINAL, EXIF_DATETIME_DIGITIZED, EXIF_DATETIME):
            try:
                parsed = parse_exif_datetime(src.get(tag))
            except Exception:
                parsed = None
            if parsed is not None:
                return parsed
    return None


def sidecar_path(image_path: Path) -> Path:
    return Path(str(image_path) + ".captured_at")


def captured_at_from_sidecar(image_path: Path) -> datetime | None:
    path = sidecar_path(image_path)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        if text.isdigit():
            ms = int(text)
            if ms > 10_000_000_000:
                ms = ms // 1000
            return datetime.fromtimestamp(ms, tz=timezone.utc)
        return aware_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except (ValueError, OSError, OverflowError):
        return None


def write_captured_at_sidecar(image_path: Path, captured_at: datetime) -> Path:
    path = sidecar_path(image_path)
    path.write_text(aware_utc(captured_at).isoformat(), encoding="utf-8")
    return path


def exif_bytes_with_datetime(captured_at: datetime, *, orientation: int | None = None) -> bytes:
    from PIL.Image import Exif

    stamp = aware_utc(captured_at).strftime("%Y:%m:%d %H:%M:%S")
    exif = Exif()
    exif[EXIF_DATETIME] = stamp
    exif[EXIF_DATETIME_ORIGINAL] = stamp
    exif[EXIF_DATETIME_DIGITIZED] = stamp
    if orientation is not None and int(orientation) not in (0, 1):
        exif[0x0112] = int(orientation)
    return exif.tobytes()
