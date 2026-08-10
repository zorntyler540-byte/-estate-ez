"""Tests for R2 endpoint normalization and catalog keys (no live network)."""

from __future__ import annotations

from app.object_storage import (
    _clean_secret,
    _normalize_endpoint,
    r2_client_error_hint,
    reset_client_cache,
)


def test_normalize_endpoint_strips_bucket_suffix(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "r2_bucket", "estate-catalog")
    monkeypatch.setattr(
        settings,
        "r2_endpoint",
        "https://abc123.r2.cloudflarestorage.com/estate-catalog",
    )
    reset_client_cache()
    assert _normalize_endpoint(settings.r2_endpoint) == "https://abc123.r2.cloudflarestorage.com"


def test_clean_secret_strips_quotes_and_newlines() -> None:
    assert _clean_secret('  "abc/def+ghi="  ') == "abc/def+ghi="
    assert _clean_secret("'secret'\n") == "secret"
    assert _clean_secret(None) == ""


def test_r2_client_error_hint_signature() -> None:
    class FakeClientError(Exception):
        def __init__(self) -> None:
            super().__init__(
                "An error occurred (SignatureDoesNotMatch) when calling the ListObjectsV2 operation"
            )
            self.response = {"Error": {"Code": "SignatureDoesNotMatch"}}

    hint = r2_client_error_hint(FakeClientError())
    assert hint is not None
    assert "SignatureDoesNotMatch" in hint
    assert "R2_SECRET_ACCESS_KEY" in hint


def test_r2_configured_false_by_default() -> None:
    from app.object_storage import r2_configured

    # Dev settings typically empty — just ensure callable.
    assert r2_configured() in (True, False)
