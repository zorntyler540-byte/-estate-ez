"""AI background for one listing hero.

Gemini edits the cover so the merchandise stays as photographed and the room
is replaced with a flat studio color. Detail, tape, and measurement frames
are never sent.
"""

from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from app.config import settings
from app.services.plain_background import PLAIN_BG_COLORS, jpeg_bytes_rgb, prepare_jpeg_bytes

# Closest match the image model accepts. Keep the cover's orientation.
_ASPECTS: tuple[tuple[str, float], ...] = (
    ("1:1", 1.0),
    ("2:3", 2 / 3),
    ("3:2", 3 / 2),
    ("3:4", 3 / 4),
    ("4:3", 4 / 3),
    ("9:16", 9 / 16),
    ("16:9", 16 / 9),
    ("21:9", 21 / 9),
)

_COLOR_WORDS = {
    "studio": "a seamless warm off-white studio sweep, soft ivory, not gray and not pure white",
    "white": "a seamless pure white studio sweep",
    "gray": "a seamless light gray studio sweep",
}

_PROMPT = """Edit this estate-sale listing photo.
Keep every piece of merchandise exactly as photographed: same objects, same count, same angle, same colors, same wear, chips, stains, labels, and hardware.
Remove only the room: walls, floor, carpet, clutter, and anything that is not the merchandise.
Place the merchandise on {backdrop}.
Add only a soft contact shadow under the merchandise so it does not look pasted on.
Do not add props, people, text, logos, a new room, or a lifestyle setting.
Do not crop off any part of the merchandise. Do not repair damage. Do not beautify the item.
Return one edited photograph."""


def closest_aspect_ratio(width: int, height: int) -> str:
    """Pick a supported output ratio so a wide sofa stays wide and a tall chair stays tall."""
    if width <= 0 or height <= 0:
        return "1:1"
    ratio = width / height
    return min(_ASPECTS, key=lambda item: abs(item[1] - ratio))[0]


def hero_background_prompt(color_name: str) -> str:
    backdrop = _COLOR_WORDS.get(color_name)
    if backdrop is None:
        raise ValueError(f"Unknown background color {color_name}")
    return _PROMPT.format(backdrop=backdrop)


def _part_image_bytes(part: object) -> bytes | None:
    inline = getattr(part, "inline_data", None)
    if inline is not None:
        data = getattr(inline, "data", None)
        if isinstance(data, (bytes, bytearray)) and data:
            return bytes(data)
        if isinstance(data, str) and data.strip():
            try:
                return base64.b64decode(data)
            except Exception:
                return None
    as_image = getattr(part, "as_image", None)
    if callable(as_image):
        try:
            image = as_image()
        except Exception:
            image = None
        raw = getattr(image, "image_bytes", None) if image is not None else None
        if isinstance(raw, (bytes, bytearray)) and raw:
            return bytes(raw)
    return None


def _iter_parts(response: object):
    parts = getattr(response, "parts", None)
    if parts:
        yield from parts
        return
    for cand in getattr(response, "candidates", None) or []:
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", None) or []:
            yield part


def image_bytes_from_response(response: object) -> bytes | None:
    """The last image the model returned. Earlier parts can be drafts."""
    found: bytes | None = None
    for part in _iter_parts(response):
        raw = _part_image_bytes(part)
        if raw:
            found = raw
    return found


def _jpeg_from_model(raw: bytes) -> bytes:
    with Image.open(BytesIO(raw)) as im:
        rgb = im.convert("RGB")
    return jpeg_bytes_rgb(rgb, quality=90)


def render_hero_ai_background(raw: bytes, color_name: str) -> bytes:
    """Ask Gemini to keep the piece and replace the room. Raises RuntimeError on failure."""
    if color_name not in PLAIN_BG_COLORS:
        raise ValueError(f"Unknown background color {color_name}")
    key = (settings.google_api_key or "").strip()
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is not configured on the server.")
    prepared = prepare_jpeg_bytes(raw)
    with Image.open(BytesIO(prepared)) as im:
        aspect = closest_aspect_ratio(im.size[0], im.size[1])
    prompt = hero_background_prompt(color_name)
    try:
        from google import genai
        from google.genai import types

        from app.core.gemini_slots import gemini_slot

        client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=50_000),
        )
        with gemini_slot():
            response = client.models.generate_content(
                model=(settings.hero_bg_gemini_model or "gemini-nano-banana-2.1").strip(),
                contents=[
                    types.Part.from_bytes(data=prepared, mime_type="image/jpeg"),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio=aspect, image_size="1K"),
                ),
            )
    except RuntimeError:
        raise
    except Exception as exc:
        message = str(exc).strip().splitlines()[0][:240] if str(exc).strip() else "AI background failed"
        raise RuntimeError(f"AI background failed. {message}") from exc
    image = image_bytes_from_response(response)
    if not image:
        text = (getattr(response, "text", None) or "").strip()
        detail = text[:240] if text else "The model did not return a photo."
        raise RuntimeError(f"AI background did not return a photo. {detail}")
    try:
        return _jpeg_from_model(image)
    except Exception as exc:
        raise RuntimeError("AI background returned a photo that could not be saved.") from exc


def render_hero_backgrounds(
    sources: dict[str, bytes],
    color_name: str,
) -> tuple[dict[str, bytes], dict[str, str]]:
    """One hero. A failure leaves that photo as shot and reports why."""
    ok: dict[str, bytes] = {}
    errors: dict[str, str] = {}
    for photo_id, raw in sources.items():
        try:
            ok[photo_id] = render_hero_ai_background(raw, color_name)
        except RuntimeError as exc:
            errors[photo_id] = str(exc)[:400]
    if sources and not ok and errors:
        raise RuntimeError(next(iter(errors.values())))
    return ok, errors
