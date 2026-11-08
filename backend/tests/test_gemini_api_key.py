"""GOOGLE_API_KEY is the only Gemini key; GEMINI_API_KEY is a deprecated alias."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app import config as config_mod
from app.config import Settings


@pytest.fixture(autouse=True)
def _clean_key_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, ENVIRONMENT="test", **kwargs)


def test_google_api_key_is_used_directly(caplog):
    with caplog.at_level(logging.WARNING, logger="app.config"):
        s = _settings(GOOGLE_API_KEY="primary-key")
    assert s.google_api_key == "primary-key"
    assert "GEMINI_API_KEY" not in caplog.text


def test_legacy_gemini_key_is_copied_with_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="app.config"):
        s = _settings(GEMINI_API_KEY="legacy-key")
    assert s.google_api_key == "legacy-key"
    assert "GEMINI_API_KEY is deprecated" in caplog.text


def test_legacy_gemini_key_from_env_var(monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_API_KEY", "legacy-env-key")
    with caplog.at_level(logging.WARNING, logger="app.config"):
        s = _settings()
    assert s.google_api_key == "legacy-env-key"
    assert "Rename it to GOOGLE_API_KEY" in caplog.text


def test_same_value_in_both_warns_but_starts(caplog):
    with caplog.at_level(logging.WARNING, logger="app.config"):
        s = _settings(GOOGLE_API_KEY="same", GEMINI_API_KEY="same")
    assert s.google_api_key == "same"
    assert "Remove GEMINI_API_KEY" in caplog.text


def test_different_values_refuse_to_start():
    with pytest.raises(ValidationError, match="both set to different values"):
        _settings(GOOGLE_API_KEY="one", GEMINI_API_KEY="two")


def test_helpers_read_live_settings(monkeypatch):
    monkeypatch.setattr(config_mod.settings, "google_api_key", "  live-key  ")
    assert config_mod.google_api_key() == "live-key"
    assert config_mod.require_google_api_key() == "live-key"
    monkeypatch.setattr(config_mod.settings, "google_api_key", "")
    assert config_mod.google_api_key() == ""
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY is not configured"):
        config_mod.require_google_api_key()


def test_pipeline_client_uses_settings_not_process_env(monkeypatch):
    import pipeline.pipeline as pipe

    captured: dict[str, str] = {}

    class _FakeClient:
        def __init__(self, *, api_key: str, **_kwargs):
            captured["api_key"] = api_key

    monkeypatch.setenv("GEMINI_API_KEY", "stale-env-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "stale-env-key")
    monkeypatch.setattr(config_mod.settings, "google_api_key", "settings-key")
    monkeypatch.setattr(pipe.genai, "Client", _FakeClient)
    monkeypatch.setattr(pipe, "_genai_client", None)
    pipe._get_client()
    assert captured["api_key"] == "settings-key"


def test_pipeline_client_requires_key(monkeypatch):
    import pipeline.pipeline as pipe

    monkeypatch.setattr(config_mod.settings, "google_api_key", "")
    monkeypatch.setattr(pipe, "_genai_client", None)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        pipe._get_client()


def test_callers_do_not_copy_key_into_os_environ(monkeypatch):
    import os

    from app.services import audience_hunt, facebook_caption, pipeline_integration

    assert not hasattr(audience_hunt, "_ensure_gemini_env")
    assert not hasattr(facebook_caption, "_ensure_gemini_env")
    monkeypatch.setattr(config_mod.settings, "google_api_key", "settings-key")
    pipeline_integration._sync_pipeline_env_from_settings()
    assert "GOOGLE_API_KEY" not in os.environ
    assert "GEMINI_API_KEY" not in os.environ
