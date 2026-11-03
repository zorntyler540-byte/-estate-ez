"""Gemini Retry-After parsing, exponential backoff, and generate_content retry loop."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.gemini_retry import (
    GeminiRetryableError,
    backoff_seconds,
    catalog_retry_exhausted_message,
    format_catalog_retry_message,
    inspect_transient,
    parse_catalog_retry_attempt,
    parse_retry_after,
)
from app.core.gemini_slots import generate_content, model_family, reset_gemini_slots_for_tests


class _HTTPError(Exception):
    def __init__(self, status_code: int, retry_after: str | None = None, msg: str = "rate limited"):
        super().__init__(msg)
        self.status_code = status_code
        self.response = SimpleNamespace(status_code=status_code, headers={"Retry-After": retry_after} if retry_after else {})


def test_parse_retry_after_delta_seconds() -> None:
    assert parse_retry_after("2") == 2.0
    assert parse_retry_after("1.5") == 1.5
    assert parse_retry_after("") is None
    assert parse_retry_after(None) is None


def test_parse_retry_after_http_date() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    later = now + timedelta(seconds=8)
    header = later.strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(header, now=now) == pytest.approx(8.0, abs=0.01)


def test_backoff_honors_retry_after_with_jitter() -> None:
    rng = random.Random(0)
    delay = backoff_seconds(0, 4.0, base=1.0, cap=32.0, rng=rng)
    assert 4.0 <= delay <= 5.0


def test_backoff_full_jitter_exponential() -> None:
    rng = random.Random(1)
    delays = [backoff_seconds(attempt, None, base=1.0, cap=32.0, rng=rng) for attempt in range(5)]
    assert delays[0] <= 1.0
    assert delays[1] <= 2.0
    assert delays[2] <= 4.0
    assert delays[3] <= 8.0
    assert delays[4] <= 16.0
    assert any(d > 0 for d in delays)


def test_inspect_transient_429_and_retry_after() -> None:
    exc = _HTTPError(429, retry_after="3")
    transient, retry_after, status = inspect_transient(exc)
    assert transient is True
    assert status == 429
    assert retry_after == 3.0


def test_inspect_transient_genai_code_attr() -> None:
    class _GenAIError(Exception):
        def __init__(self) -> None:
            super().__init__("429 RESOURCE_EXHAUSTED")
            self.code = 429
            self.status = "RESOURCE_EXHAUSTED"

    transient, _retry, status = inspect_transient(_GenAIError())
    assert transient is True
    assert status == 429


def test_inspect_transient_ignores_400() -> None:
    transient, _retry, status = inspect_transient(_HTTPError(400, msg="bad request"))
    assert transient is False
    assert status == 400


def test_catalog_retry_attempt_roundtrip() -> None:
    msg = format_catalog_retry_message(2, 3, RuntimeError("Gemini 429"))
    assert parse_catalog_retry_attempt(msg) == 2
    assert parse_catalog_retry_attempt(None) == 0
    exhausted = catalog_retry_exhausted_message(3, RuntimeError("Gemini 429"))
    assert "Gemini" in exhausted
    assert "Retry failed" in exhausted


def test_model_family_does_not_change_model_choice() -> None:
    assert model_family("gemini-2.5-flash") == "flash"
    assert model_family("gemini-2.5-pro") == "pro"
    assert model_family("gemini-3.8-flash") == "flash38"
    assert model_family("gemini-flash-latest") == "flash38"


def test_generate_content_retries_429_then_succeeds(monkeypatch) -> None:
    reset_gemini_slots_for_tests()
    sleeps: list[float] = []
    monkeypatch.setattr("app.core.gemini_slots.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("app.core.gemini_slots.backoff_seconds", lambda *a, **k: 0.01)
    monkeypatch.setattr("app.core.gemini_slots.wait_for_model_quota", lambda *a, **k: None)

    calls = {"n": 0}

    class _Models:
        def generate_content(self, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _HTTPError(429, retry_after="1")
            return SimpleNamespace(text="ok", usage_metadata=None)

    class _Client:
        models = _Models()

    out = generate_content(_Client(), model="gemini-2.5-flash", contents=["hi"])
    assert out.text == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_generate_content_marks_retryable_after_exhausted_attempts(monkeypatch) -> None:
    reset_gemini_slots_for_tests()
    monkeypatch.setattr("app.core.gemini_slots.time.sleep", lambda s: None)
    monkeypatch.setattr("app.core.gemini_slots.backoff_seconds", lambda *a, **k: 0.0)
    monkeypatch.setattr("app.core.gemini_slots.wait_for_model_quota", lambda *a, **k: None)
    monkeypatch.setattr("app.config.settings.gemini_retry_max_attempts", 3)

    class _Models:
        def generate_content(self, **kwargs):
            raise _HTTPError(429, retry_after="2")

    class _Client:
        models = _Models()

    with pytest.raises(GeminiRetryableError) as caught:
        generate_content(_Client(), model="gemini-2.5-pro", contents=["hi"])
    assert caught.value.attempts == 3
    assert caught.value.status_code == 429
