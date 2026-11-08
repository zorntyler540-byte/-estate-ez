"""Every SDK client must call the Gemini API version our system instructions need.

google-genai 3.x defaults to ``v1``, which rejects system instructions with a 400.
"""

from __future__ import annotations

from app.config import settings


def test_pipeline_client_uses_configured_api_version(monkeypatch) -> None:
    from pipeline import pipeline

    monkeypatch.setattr(settings, "google_api_key", "test-key")
    monkeypatch.setattr(pipeline, "_genai_client", None)
    client = pipeline._get_client()
    try:
        assert client._api_client._http_options.api_version == settings.gemini_api_version == "v1beta"
    finally:
        monkeypatch.setattr(pipeline, "_genai_client", None)


def test_core_client_uses_configured_api_version(monkeypatch) -> None:
    from app.core import gemini

    monkeypatch.setattr(settings, "google_api_key", "test-key")
    monkeypatch.setattr(gemini, "_genai_client", None)
    client = gemini._get_genai_client()
    try:
        assert client._api_client._http_options.api_version == "v1beta"
    finally:
        monkeypatch.setattr(gemini, "_genai_client", None)
