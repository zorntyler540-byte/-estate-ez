"""Env-var resolution for pinned Gemini model ids."""

from __future__ import annotations

import inspect

import pytest

from app.config import Settings
from app.core.gemini_models import (
    DEFAULT_LISTING_FLASH_MODEL,
    DEFAULT_LISTING_MODEL,
    DEFAULT_PRO_MODEL,
    DEFAULT_TRIAGE_MODEL,
    caption_model,
    grouping_model,
    hero_background_model,
    listing_flash_model,
    listing_model,
    ocr_model,
    pin_model_id,
    pro_model,
    resolved_gemini_models,
    triage_model,
    usage_tier_for_model,
)

_MODEL_ENV = (
    "GEMINI_TRIAGE_MODEL",
    "GEMINI_GROUPING_MODEL",
    "GEMINI_OCR_MODEL",
    "GEMINI_LISTING_MODEL",
    "GEMINI_LISTING_FLASH_MODEL",
    "GEMINI_CAPTION_MODEL",
    "GEMINI_FLASH_MODEL",
    "GEMINI_PRO_MODEL",
    "GEMINI_MODEL",
    "HERO_BG_GEMINI_MODEL",
)


@pytest.fixture(autouse=True)
def _clear_model_env(monkeypatch):
    for name in _MODEL_ENV:
        monkeypatch.delenv(name, raising=False)


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, ENVIRONMENT="test", **kwargs)


class _Cfg:
    def __init__(self, fields: set[str] | None = None, **attrs: str) -> None:
        self.model_fields_set = fields or set()
        for key, value in attrs.items():
            setattr(self, key, value)


def test_defaults_match_current_behavior() -> None:
    resolved = resolved_gemini_models(environ={}, settings_obj=_Cfg())
    assert resolved["triage"] == DEFAULT_TRIAGE_MODEL == "gemini-2.5-flash"
    assert resolved["grouping"] == "gemini-2.5-flash"
    assert resolved["listing"] == DEFAULT_LISTING_MODEL == "gemini-2.5-pro"
    assert resolved["pro"] == DEFAULT_PRO_MODEL == "gemini-2.5-pro"
    assert resolved["listing_flash"] == DEFAULT_LISTING_FLASH_MODEL == "gemini-3.8-flash"
    assert resolved["ocr"] == "gemini-3.8-flash"
    assert resolved["caption"] == "gemini-2.5-flash"
    assert resolved["hero_background"] == "gemini-nano-banana-2.1"
    assert all("latest" not in value for value in resolved.values())


def test_live_defaults_when_env_unset() -> None:
    assert triage_model() == "gemini-2.5-flash"
    assert listing_model() == "gemini-2.5-pro"
    assert pro_model() == "gemini-2.5-pro"
    assert listing_flash_model() == "gemini-3.8-flash"
    assert ocr_model() == "gemini-3.8-flash"
    assert "latest" not in hero_background_model()


def test_triage_env_overrides_legacy_flash(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_FLASH_MODEL", "gemini-2.0-flash")
    monkeypatch.setenv("GEMINI_TRIAGE_MODEL", "gemini-3.5-flash-lite")
    assert triage_model() == "gemini-3.5-flash-lite"
    assert grouping_model() == "gemini-3.5-flash-lite"


def test_legacy_flash_model_still_selects_triage(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_FLASH_MODEL", "gemini-2.0-flash")
    assert triage_model() == "gemini-2.0-flash"
    assert grouping_model() == "gemini-2.0-flash"
    # Captions stay on the same legacy Flash id, independent of a later triage override.
    assert caption_model() == "gemini-2.0-flash"


def test_grouping_and_caption_can_diverge_from_triage(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_TRIAGE_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("GEMINI_GROUPING_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GEMINI_CAPTION_MODEL", "gemini-3.8-flash")
    assert triage_model() == "gemini-3.5-flash-lite"
    assert grouping_model() == "gemini-2.5-flash"
    assert caption_model() == "gemini-3.8-flash"


def test_listing_follows_pro_until_listing_model_is_set(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_PRO_MODEL", "gemini-2.5-pro")
    assert listing_model() == "gemini-2.5-pro"
    assert pro_model() == "gemini-2.5-pro"
    monkeypatch.setenv("GEMINI_LISTING_MODEL", "gemini-3.8-flash")
    assert listing_model() == "gemini-3.8-flash"
    assert pro_model() == "gemini-2.5-pro"


def test_flash_latest_alias_is_pinned(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-flash-latest")
    monkeypatch.setenv("GEMINI_TRIAGE_MODEL", "gemini-flash-latest")
    assert listing_flash_model() == "gemini-3.8-flash"
    assert ocr_model() == "gemini-3.8-flash"
    assert triage_model() == "gemini-3.8-flash"
    assert "latest" not in listing_flash_model()
    assert "latest" not in triage_model()


def test_unknown_latest_alias_falls_back() -> None:
    assert pin_model_id("gemini-pro-latest", fallback="gemini-2.5-pro") == "gemini-2.5-pro"
    assert not pin_model_id("gemini-foo-latest", fallback="gemini-2.5-flash").endswith("-latest")


def test_ocr_and_listing_flash_overrides(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    assert listing_flash_model() == "gemini-2.5-flash"
    assert ocr_model() == "gemini-2.5-flash"
    monkeypatch.setenv("GEMINI_OCR_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("GEMINI_LISTING_FLASH_MODEL", "gemini-3.8-flash")
    assert ocr_model() == "gemini-3.5-flash-lite"
    assert listing_flash_model() == "gemini-3.8-flash"


def test_settings_defaults_and_alias_pin() -> None:
    bare = _settings()
    assert bare.gemini_model == "gemini-3.8-flash"
    assert bare.gemini_triage_model == "gemini-2.5-flash"
    assert bare.gemini_pro_model == "gemini-2.5-pro"
    assert bare.gemini_listing_model == ""
    assert bare.gemini_ocr_model == ""
    pinned = _settings(GEMINI_MODEL="gemini-flash-latest")
    assert pinned.gemini_model == "gemini-3.8-flash"
    refused = _settings(GEMINI_PRO_MODEL="gemini-pro-latest")
    assert refused.gemini_pro_model == "gemini-2.5-pro"
    assert "latest" not in refused.gemini_pro_model


def test_settings_field_is_used_when_env_is_unset() -> None:
    cfg = _Cfg({"gemini_triage_model"}, gemini_triage_model="gemini-3.5-flash-lite")
    assert triage_model(environ={}, settings_obj=cfg) == "gemini-3.5-flash-lite"


def test_attribute_override_moves_listing_with_pro() -> None:
    cfg = _Cfg(gemini_pro_model="gemini-2.5-pro-custom")
    assert pro_model(environ={}, settings_obj=cfg) == "gemini-2.5-pro-custom"
    assert listing_model(environ={}, settings_obj=cfg) == "gemini-2.5-pro-custom"


def test_usage_tier_follows_the_model_family() -> None:
    assert usage_tier_for_model("gemini-2.5-pro") == "pro"
    assert usage_tier_for_model("gemini-3.8-flash") == "flash"
    assert usage_tier_for_model("gemini-2.5-flash") == "flash"


def test_listing_author_is_generate_content_not_batch() -> None:
    from pipeline.pipeline import build_listing_request, run_brand_audit_pro

    src = inspect.getsource(run_brand_audit_pro) + inspect.getsource(build_listing_request)
    assert "batches" not in src
    assert "_gemini_generate" in inspect.getsource(run_brand_audit_pro)


def test_run_brand_audit_uses_listing_model(monkeypatch) -> None:
    import pipeline.pipeline as pipe

    monkeypatch.setenv("GEMINI_LISTING_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("GEMINI_PRO_MODEL", "gemini-2.5-pro")
    captured: dict[str, str] = {}

    class _Resp:
        text = '{"final_auction_title": "Pinned", "final_professional_description": "ok"}'
        parsed = None
        usage_metadata = None

    def _fake(model, contents, config):
        captured["model"] = model
        assert contents
        assert config is not None
        return _Resp()

    monkeypatch.setattr(pipe, "_gemini_generate", _fake)
    out = pipe.run_brand_audit_pro({}, {"items": []})
    assert captured["model"] == "gemini-3.8-flash"
    assert "Pinned" in out


def test_triage_call_uses_triage_model(monkeypatch) -> None:
    import pipeline.pipeline as pipe
    from pipeline.schemas import PhotoTriageResult

    monkeypatch.setenv("GEMINI_TRIAGE_MODEL", "gemini-3.5-flash-lite")
    captured: dict[str, str] = {}
    row = PhotoTriageResult(
        filename="a.jpg",
        photo_role="angle_only",
        label_legibility="none",
        quality="ok",
        confidence="low",
    )

    class _Resp:
        parsed = row
        text = None
        usage_metadata = None

    def _fake(model, contents, config):
        captured["model"] = model
        return _Resp()

    monkeypatch.setattr(pipe, "_gemini_generate", _fake)
    pipe.triage_single_photo("a.jpg", b"\xff\xd8\xff\xd9")
    assert captured["model"] == "gemini-3.5-flash-lite"
