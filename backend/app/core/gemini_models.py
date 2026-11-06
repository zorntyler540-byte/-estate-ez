"""Pinned Gemini model ids.

Call sites resolve a model at request time. Defaults match today's behavior:

* triage and gallery grouping: ``gemini-2.5-flash`` (``GEMINI_FLASH_MODEL``)
* final listing author: ``gemini-2.5-pro`` (follows ``GEMINI_PRO_MODEL`` until
  ``GEMINI_LISTING_MODEL`` is set)
* audience hunt and the field appraiser: ``gemini-2.5-pro``
* simple-lot Flash listing seed and tape OCR: ``gemini-3.8-flash``, which is
  what ``gemini-flash-latest`` resolves to today
* Facebook captions: ``gemini-2.5-flash``
* listing-cover background: ``gemini-nano-banana-2.1``

``-latest`` aliases are never sent to the API. ``gemini-flash-latest`` is
rewritten to ``gemini-3.8-flash`` so an existing deploy keeps current behavior.
Any other ``-latest`` id falls back to that role's pinned default.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping

_LOG = logging.getLogger(__name__)

DEFAULT_TRIAGE_MODEL = "gemini-2.5-flash"
DEFAULT_PRO_MODEL = "gemini-2.5-pro"
DEFAULT_LISTING_MODEL = "gemini-2.5-pro"
# Former ``gemini-flash-latest`` (listing Flash seed + tape check).
DEFAULT_LISTING_FLASH_MODEL = "gemini-3.8-flash"
DEFAULT_OCR_MODEL = "gemini-3.8-flash"
DEFAULT_CAPTION_MODEL = "gemini-2.5-flash"
DEFAULT_HERO_BG_MODEL = "gemini-nano-banana-2.1"

# Aliases Google moves without a deploy. Only ids this repo has documented.
_ALIAS_PINS = {
    "gemini-flash-latest": DEFAULT_LISTING_FLASH_MODEL,
}

_SETTINGS_FIELD_DEFAULTS = {
    "gemini_model": DEFAULT_LISTING_FLASH_MODEL,
    "gemini_triage_model": DEFAULT_TRIAGE_MODEL,
    "gemini_grouping_model": "",
    "gemini_ocr_model": "",
    "gemini_listing_model": "",
    "gemini_listing_flash_model": "",
    "gemini_caption_model": "",
    "gemini_flash_model": DEFAULT_TRIAGE_MODEL,
    "gemini_pro_model": DEFAULT_PRO_MODEL,
    "hero_bg_gemini_model": DEFAULT_HERO_BG_MODEL,
}

_warned: set[str] = set()


def default_for_settings_field(field_name: str) -> str:
    return _SETTINGS_FIELD_DEFAULTS.get(field_name, "")


def pin_model_id(model: str | None, *, fallback: str) -> str:
    """Return an explicit model id. Never returns a ``-latest`` alias."""
    raw = (model or "").strip()
    if not raw:
        return fallback
    key = raw.lower()
    if key.endswith("-latest"):
        pinned = _ALIAS_PINS.get(key, fallback)
        if key not in _warned:
            _warned.add(key)
            if key in _ALIAS_PINS:
                _LOG.warning(
                    "Gemini alias %s is pinned to %s. Set an explicit model id.",
                    raw,
                    pinned,
                )
            else:
                _LOG.warning(
                    "Gemini alias %s is not used. Falling back to %s.",
                    raw,
                    pinned or "(empty)",
                )
        return pinned
    return raw


def _settings() -> Any:
    from app.config import settings

    return settings


def _pick(
    env: Mapping[str, str],
    cfg: Any,
    pairs: list[tuple[str, str, str]],
) -> str | None:
    """First explicit env var or settings override.

    ``pairs`` is ``(ENV_NAME, settings_attr, coded_default)``. An empty string
    means the role was left unset. Process env wins over the Settings object so
    tests and a redeploy see the new value without reconstructing Settings.
    """
    fields_set = set(getattr(cfg, "model_fields_set", ()) or ())
    for env_name, attr, coded_default in pairs:
        raw = env.get(env_name)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if attr in fields_set:
            value = getattr(cfg, attr, "")
            if isinstance(value, str) and value.strip():
                return value.strip()
        value = getattr(cfg, attr, None)
        if isinstance(value, str):
            text = value.strip()
            if text and text != coded_default:
                return text
    return None


def _resolve(
    pairs: list[tuple[str, str, str]],
    *,
    fallback: str,
    environ: Mapping[str, str] | None = None,
    settings_obj: Any | None = None,
) -> str:
    env = os.environ if environ is None else environ
    cfg = _settings() if settings_obj is None else settings_obj
    chosen = _pick(env, cfg, pairs)
    return pin_model_id(chosen, fallback=fallback)


def triage_model(
    *,
    environ: Mapping[str, str] | None = None,
    settings_obj: Any | None = None,
) -> str:
    """Per-photo triage (roles, measurements, label OCR)."""
    return _resolve(
        [
            ("GEMINI_TRIAGE_MODEL", "gemini_triage_model", DEFAULT_TRIAGE_MODEL),
            ("GEMINI_FLASH_MODEL", "gemini_flash_model", DEFAULT_TRIAGE_MODEL),
        ],
        fallback=DEFAULT_TRIAGE_MODEL,
        environ=environ,
        settings_obj=settings_obj,
    )


def grouping_model(
    *,
    environ: Mapping[str, str] | None = None,
    settings_obj: Any | None = None,
) -> str:
    """Listing-gallery sort. Unset follows triage (both used to be Flash)."""
    chosen = _pick(
        os.environ if environ is None else environ,
        _settings() if settings_obj is None else settings_obj,
        [("GEMINI_GROUPING_MODEL", "gemini_grouping_model", "")],
    )
    if chosen:
        return pin_model_id(chosen, fallback=DEFAULT_TRIAGE_MODEL)
    return triage_model(environ=environ, settings_obj=settings_obj)


def pro_model(
    *,
    environ: Mapping[str, str] | None = None,
    settings_obj: Any | None = None,
) -> str:
    """Audience hunt, field appraiser, and the listing fallback."""
    return _resolve(
        [("GEMINI_PRO_MODEL", "gemini_pro_model", DEFAULT_PRO_MODEL)],
        fallback=DEFAULT_PRO_MODEL,
        environ=environ,
        settings_obj=settings_obj,
    )


def listing_model(
    *,
    environ: Mapping[str, str] | None = None,
    settings_obj: Any | None = None,
) -> str:
    """Final auction listing (``run_brand_audit_pro``).

    Unset follows ``pro_model()`` so an existing ``GEMINI_PRO_MODEL`` override
    still writes listings. Set ``GEMINI_LISTING_MODEL`` to move listings (for
    example to ``gemini-3.8-flash``) without moving search-grounded Pro calls.
    """
    chosen = _pick(
        os.environ if environ is None else environ,
        _settings() if settings_obj is None else settings_obj,
        [("GEMINI_LISTING_MODEL", "gemini_listing_model", "")],
    )
    if chosen:
        return pin_model_id(chosen, fallback=DEFAULT_LISTING_MODEL)
    return pro_model(environ=environ, settings_obj=settings_obj)


def listing_flash_model(
    *,
    environ: Mapping[str, str] | None = None,
    settings_obj: Any | None = None,
) -> str:
    """Simple-lot Flash listing seed (``analyze_photo``). Was ``GEMINI_MODEL``."""
    env = os.environ if environ is None else environ
    cfg = _settings() if settings_obj is None else settings_obj
    override = _pick(
        env,
        cfg,
        [("GEMINI_LISTING_FLASH_MODEL", "gemini_listing_flash_model", "")],
    )
    if override:
        return pin_model_id(override, fallback=DEFAULT_LISTING_FLASH_MODEL)
    return _resolve(
        [("GEMINI_MODEL", "gemini_model", DEFAULT_LISTING_FLASH_MODEL)],
        fallback=DEFAULT_LISTING_FLASH_MODEL,
        environ=env,
        settings_obj=cfg,
    )


def ocr_model(
    *,
    environ: Mapping[str, str] | None = None,
    settings_obj: Any | None = None,
) -> str:
    """Estate-tape SKU check. Unset uses the Flash listing-seed model (today's path)."""
    chosen = _pick(
        os.environ if environ is None else environ,
        _settings() if settings_obj is None else settings_obj,
        [("GEMINI_OCR_MODEL", "gemini_ocr_model", "")],
    )
    if chosen:
        return pin_model_id(chosen, fallback=DEFAULT_OCR_MODEL)
    return listing_flash_model(environ=environ, settings_obj=settings_obj)


def caption_model(
    *,
    environ: Mapping[str, str] | None = None,
    settings_obj: Any | None = None,
) -> str:
    """Facebook Page captions. Unset stays on the legacy Flash id, not triage."""
    env = os.environ if environ is None else environ
    cfg = _settings() if settings_obj is None else settings_obj
    override = _pick(env, cfg, [("GEMINI_CAPTION_MODEL", "gemini_caption_model", "")])
    if override:
        return pin_model_id(override, fallback=DEFAULT_CAPTION_MODEL)
    return _resolve(
        [("GEMINI_FLASH_MODEL", "gemini_flash_model", DEFAULT_TRIAGE_MODEL)],
        fallback=DEFAULT_CAPTION_MODEL,
        environ=env,
        settings_obj=cfg,
    )


def hero_background_model(
    *,
    environ: Mapping[str, str] | None = None,
    settings_obj: Any | None = None,
) -> str:
    return _resolve(
        [("HERO_BG_GEMINI_MODEL", "hero_bg_gemini_model", DEFAULT_HERO_BG_MODEL)],
        fallback=DEFAULT_HERO_BG_MODEL,
        environ=environ,
        settings_obj=settings_obj,
    )


def usage_tier_for_model(model: str) -> str:
    """Analytics bucket: ``pro`` or ``flash``. Does not change which model is called."""
    from app.core.gemini_slots import model_family

    return "pro" if model_family(model) == "pro" else "flash"


def resolved_gemini_models(
    *,
    environ: Mapping[str, str] | None = None,
    settings_obj: Any | None = None,
) -> dict[str, str]:
    kwargs = {"environ": environ, "settings_obj": settings_obj}
    return {
        "triage": triage_model(**kwargs),
        "grouping": grouping_model(**kwargs),
        "ocr": ocr_model(**kwargs),
        "listing": listing_model(**kwargs),
        "listing_flash": listing_flash_model(**kwargs),
        "pro": pro_model(**kwargs),
        "caption": caption_model(**kwargs),
        "hero_background": hero_background_model(**kwargs),
    }
