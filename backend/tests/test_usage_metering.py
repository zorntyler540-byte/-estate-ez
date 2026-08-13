"""Per-job Gemini token metering counters (reset / record / snapshot)."""

from __future__ import annotations

from pipeline.pipeline import reset_usage_counters, usage_snapshot
from pipeline import pipeline as p


class _FakeMeta:
    def __init__(self, prompt: int, candidates: int) -> None:
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates


class _FakeResponse:
    def __init__(self, prompt: int, candidates: int) -> None:
        self.usage_metadata = _FakeMeta(prompt, candidates)


def test_reset_and_record_by_tier() -> None:
    reset_usage_counters()
    # three Flash triage calls + one Pro author call
    p._record_usage("flash", _FakeResponse(400, 100))
    p._record_usage("flash", _FakeResponse(450, 110))
    p._record_usage("flash", _FakeResponse(500, 90))
    p._record_usage("pro", _FakeResponse(3000, 600))
    snap = usage_snapshot()
    assert snap["flash_input"] == 1350
    assert snap["flash_output"] == 300
    assert snap["flash_calls"] == 3
    assert snap["pro_input"] == 3000
    assert snap["pro_output"] == 600
    assert snap["pro_calls"] == 1


def test_reset_clears() -> None:
    p._record_usage("flash", _FakeResponse(10, 5))
    reset_usage_counters()
    snap = usage_snapshot()
    assert all(v == 0 for v in snap.values())


def test_record_is_safe_without_usage_metadata() -> None:
    reset_usage_counters()
    p._record_usage("flash", object())  # no usage_metadata attr -> no-op, no raise
    assert usage_snapshot()["flash_calls"] == 0
