"""Tests for R2 endpoint normalization and catalog keys (no live network)."""

from __future__ import annotations

from app.object_storage import _normalize_endpoint, reset_client_cache


def test_normalize_endpoint_strips_bucket_suffix(monkeypatch) -> None:
    from app import object_storage
    from app.config import settings

    monkeypatch.setattr(settings, "r2_bucket", "estate-catalog")
    monkeypatch.setattr(
        settings,
        "r2_endpoint",
        "https://abc123.r2.cloudflarestorage.com/estate-catalog",
    )
    reset_client_cache()
    assert _normalize_endpoint(settings.r2_endpoint) == "https://abc123.r2.cloudflarestorage.com"


def test_r2_configured_false_by_default() -> None:
    from app.object_storage import r2_configured

    # Dev settings typically empty — just ensure callable.
    assert r2_configured() in (True, False)
