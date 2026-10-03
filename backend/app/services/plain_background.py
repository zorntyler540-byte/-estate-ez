"""Studio-color background for Janet (JL) furniture listing photos.

The cutout model runs in a child process so its memory leaves when the photo is done.
The original file is kept beside the catalog photo until someone restores it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from app.catalog_storage import (
    _object_key_from_storage_path,
    upload_root,
)
from app import object_storage

PLAIN_BG_COLORS: dict[str, tuple[int, int, int]] = {
    "studio": (243, 240, 234),
    "white": (255, 255, 255),
    "gray": (226, 226, 229),
}
PLAIN_BG_MAX_EDGE = 1600
# A lot can hold this many photos. Apply runs them a couple at a time (see PLAIN_BG_CHUNK).
PLAIN_BG_MAX_PHOTOS = 40
# One cutout per request stays under the ~60s proxy limit. Select-all sends the next photo after.
PLAIN_BG_CHUNK = 1
# Light cutout model. Override with PLAIN_BG_REMBG_MODEL=u2net for a heavier pass.
PLAIN_BG_MODEL = os.environ.get("PLAIN_BG_REMBG_MODEL", "u2netp").strip() or "u2netp"

_FURNITURE_TITLE = re.compile(
    r"\b("
    r"sofas?|couches?|loveseats?|settees?|chairs?|recliners?|ottomans?|sectionals?|"
    r"dressers?|night ?stands?|armoires?|wardrobes?|bookcases?|bookshelves?|hutches?|"
    r"credenzas?|sideboards?|buffets?|cabinets?|vanit(?:y|ies)|headboards?|footboards?|"
    r"desks?|tables?|benches?|stools?|futons?|chifforobes?|chiffoniers?|"
    r"chests?(?: of drawers)?|bed ?frames?"
    r")\b",
    re.IGNORECASE,
)


def sku_prefix(sku: str | None) -> str:
    """Same leading-letter rule as the sale list (JL91500 → JL)."""
    raw = (sku or "").strip()
    if not raw:
        return ""
    upper = raw.upper()
    if upper.startswith("CLONE"):
        rest = upper[5:]
        letters = re.match(r"^[A-Z]+", rest)
        return f"CLONE{letters.group(0)}" if letters else "CLONE"
    lead = re.match(r"^([A-Za-z]+)", raw)
    if lead:
        return lead.group(1).upper()
    tail = re.search(r"([A-Za-z]+)\d*$", raw)
    return tail.group(1).upper() if tail else ""


def lot_is_janet_furniture(
    sku: str | None,
    category: str | None,
    title: str | None = "",
) -> bool:
    """JL lots whose category or title is furniture. Other clients stay untouched."""
    if sku_prefix(sku) != "JL":
        return False
    if "furniture" in (category or "").lower():
        return True
    return bool(_FURNITURE_TITLE.search(title or ""))


def plain_bg_sidecar_key(storage_path: str | None) -> str | None:
    key = _object_key_from_storage_path(storage_path or "")
    if not key:
        return None
    name = key.rsplit("/", 1)[-1]
    if "." not in name:
        return None
    base, _ext = key.rsplit(".", 1)
    return f"{base}.plainbg-src.jpg"


def plain_bg_sidecar_path(storage_path: str | None) -> Path | None:
    key = plain_bg_sidecar_key(storage_path)
    if not key:
        return None
    return (upload_root() / key).resolve()


def plain_bg_original_saved(storage_path: str | None) -> bool:
    path = plain_bg_sidecar_path(storage_path)
    if path is not None and path.is_file():
        return True
    key = plain_bg_sidecar_key(storage_path)
    if not key or not object_storage.r2_configured():
        return False
    try:
        return bool(object_storage.object_exists(key))
    except Exception:
        return False


def _put_sidecar_cloud(storage_path: str, local: Path) -> None:
    key = plain_bg_sidecar_key(storage_path)
    if not key or not object_storage.r2_configured():
        return
    object_storage.put_file(key, str(local))


def _delete_sidecar_cloud(storage_path: str) -> None:
    key = plain_bg_sidecar_key(storage_path)
    if not key or not object_storage.r2_configured():
        return
    try:
        object_storage.delete_object(key)
    except Exception:
        pass


def ensure_plain_bg_original(storage_path: str, src: Path) -> bool:
    """Copy the current file once. Returns True when this call created the copy."""
    dest = plain_bg_sidecar_path(storage_path)
    if dest is None or not src.is_file():
        return False
    if dest.is_file() or plain_bg_original_saved(storage_path):
        if not dest.is_file() and object_storage.r2_configured():
            key = plain_bg_sidecar_key(storage_path)
            if key:
                dest.parent.mkdir(parents=True, exist_ok=True)
                object_storage.download_file(key, str(dest))
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    _put_sidecar_cloud(storage_path, dest)
    return True


def discard_plain_bg_original(storage_path: str) -> None:
    dest = plain_bg_sidecar_path(storage_path)
    if dest is not None:
        dest.unlink(missing_ok=True)
    _delete_sidecar_cloud(storage_path)


def jpeg_bytes_rgb(im: Image.Image, *, quality: int = 88) -> bytes:
    rgb = im.convert("RGB") if im.mode != "RGB" else im
    buf = BytesIO()
    rgb.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def downscale_rgb(im: Image.Image, max_edge: int = PLAIN_BG_MAX_EDGE) -> Image.Image:
    rgb = im.convert("RGB") if im.mode != "RGB" else im
    if max(rgb.size) > max_edge:
        rgb = rgb.copy()
        rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return rgb


# A furniture photo should keep most of the piece. Close-ups of wood and tape-measure
# frames often come back as a sliver on the color — those stay as shot.
_MIN_COVERAGE = 0.42
_MIN_BBOX_FRACTION = 0.50
_MIN_SOLIDITY = 0.72
_KEPT_TOO_LITTLE = "Left as shot. The cutout would have removed too much of the piece."


def composite_cutout(cut: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """Place an RGBA cutout on a flat color. Refuses a mask that would eat the piece."""
    rgba = cut.convert("RGBA")
    alpha = rgba.getchannel("A")
    hist = alpha.histogram()
    count = sum(hist) or 1
    mean = sum(i * n for i, n in enumerate(hist)) / count
    if mean < 12:
        raise RuntimeError("Could not find the piece in this photo")
    if mean > 248:
        raise RuntimeError("Could not separate the piece from the background")
    kept = sum(hist[128:])
    coverage = kept / count
    if coverage < _MIN_COVERAGE:
        raise RuntimeError(_KEPT_TOO_LITTLE)
    mask = alpha.point(lambda p: 255 if p >= 128 else 0)
    bbox = mask.getbbox()
    if bbox is None:
        raise RuntimeError(_KEPT_TOO_LITTLE)
    width, height = alpha.size
    box_w = bbox[2] - bbox[0]
    box_h = bbox[3] - bbox[1]
    if box_w < _MIN_BBOX_FRACTION * width or box_h < _MIN_BBOX_FRACTION * height:
        raise RuntimeError(_KEPT_TOO_LITTLE)
    solidity = kept / float(box_w * box_h)
    if solidity < _MIN_SOLIDITY:
        raise RuntimeError(_KEPT_TOO_LITTLE)
    soft = alpha.filter(ImageFilter.GaussianBlur(radius=0.8))
    canvas = Image.new("RGB", rgba.size, color)
    canvas.paste(rgba.convert("RGB"), mask=soft)
    return canvas


def prepare_jpeg_bytes(raw: bytes) -> bytes:
    with Image.open(BytesIO(raw)) as im:
        turned = ImageOps.exif_transpose(im)
        if turned is None:
            turned = im
        rgb = downscale_rgb(turned)
    return jpeg_bytes_rgb(rgb, quality=90)


def render_plain_backgrounds(
    sources: dict[str, bytes],
    color_name: str,
    *,
    timeout_s: int = 55,
) -> tuple[dict[str, bytes], dict[str, str]]:
    """Cut out each JPEG in a child process. Returns (ok jpeg bytes, id → error)."""
    if color_name not in PLAIN_BG_COLORS:
        raise ValueError(f"Unknown background color {color_name}")
    if not sources:
        return {}, {}
    with tempfile.TemporaryDirectory(prefix="plainbg-") as tmp:
        root = Path(tmp)
        in_dir = root / "in"
        out_dir = root / "out"
        in_dir.mkdir()
        out_dir.mkdir()
        names: dict[str, str] = {}
        for index, (photo_id, raw) in enumerate(sources.items()):
            safe = re.sub(r"[^a-zA-Z0-9_-]+", "", photo_id)[:80] or "photo"
            name = f"{index:03d}-{safe}"
            names[photo_id] = name
            (in_dir / f"{name}.jpg").write_bytes(prepare_jpeg_bytes(raw))
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.services.plain_background_cut",
                color_name,
                str(in_dir),
                str(out_dir),
            ],
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(err[:500] or "Background cutout failed")
        ok: dict[str, bytes] = {}
        errors: dict[str, str] = {}
        for photo_id, name in names.items():
            out_jpg = out_dir / f"{name}.jpg"
            err_path = out_dir / f"{name}.err"
            if out_jpg.is_file():
                ok[photo_id] = out_jpg.read_bytes()
            elif err_path.is_file():
                errors[photo_id] = err_path.read_text(encoding="utf-8")[:400]
            else:
                errors[photo_id] = "Background cutout did not return a photo"
        return ok, errors
