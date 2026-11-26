"""Unit tests for the API's 3 AM Pacific memory recycle schedule."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services import api_memory_recycle as recycle
from app.services.api_memory_recycle import RECYCLE_ZONE, next_recycle_at, start_api_memory_recycle

PT = ZoneInfo("America/Los_Angeles")


def test_next_recycle_is_today_when_before_3am() -> None:
    now = datetime(2026, 11, 24, 2, 30, tzinfo=PT)
    got = next_recycle_at(now)
    assert got.astimezone(PT) == datetime(2026, 11, 24, 3, 0, tzinfo=PT)


def test_next_recycle_is_tomorrow_at_or_after_3am() -> None:
    now = datetime(2026, 11, 24, 3, 0, tzinfo=PT)
    got = next_recycle_at(now)
    assert got.astimezone(PT) == datetime(2026, 11, 25, 3, 0, tzinfo=PT)
    later = datetime(2026, 11, 24, 15, 0, tzinfo=PT)
    assert next_recycle_at(later).astimezone(PT) == datetime(2026, 11, 25, 3, 0, tzinfo=PT)


def test_next_recycle_uses_pacific_across_utc() -> None:
    # 3 AM PT on Nov 24 2026 is 11:00 UTC (PST, UTC-8).
    now = datetime(2026, 11, 24, 10, 0, tzinfo=ZoneInfo("UTC"))
    got = next_recycle_at(now)
    assert got.astimezone(PT) == datetime(2026, 11, 24, 3, 0, tzinfo=PT)
    assert got.astimezone(ZoneInfo("UTC")) == datetime(2026, 11, 24, 11, 0, tzinfo=ZoneInfo("UTC"))


def test_next_recycle_after_spring_forward() -> None:
    # 2026-03-08 2 AM PT does not exist; 3 AM PDT is 10:00 UTC.
    now = datetime(2026, 3, 8, 1, 30, tzinfo=PT)
    got = next_recycle_at(now)
    local = got.astimezone(PT)
    assert local.year == 2026 and local.month == 3 and local.day == 8
    assert local.hour == 3
    assert local.tzname() == "PDT"


def test_start_is_noop_in_tests() -> None:
    # ENVIRONMENT=test in pytest; must not spawn the exit thread.
    start_api_memory_recycle()


def test_recycle_zone_is_pacific() -> None:
    assert RECYCLE_ZONE.key == "America/Los_Angeles"


def test_api_recycle_loop_stays_on_exact_3am_without_jitter(monkeypatch) -> None:
    """Shared scheduling must not add worker jitter to the API exit."""
    seen: dict[str, object] = {}

    def capture(target: datetime) -> None:
        seen["target"] = target

    def stop(reason: str) -> None:
        seen["reason"] = reason
        raise RuntimeError("stop")

    monkeypatch.setattr(recycle, "sleep_until", capture)
    monkeypatch.setattr(recycle, "exit_for_memory_recycle", stop)
    with pytest.raises(RuntimeError, match="stop"):
        recycle._recycle_loop()
    target = seen["target"]
    assert isinstance(target, datetime)
    local = target.astimezone(PT)
    assert (local.hour, local.minute, local.second, local.microsecond) == (3, 0, 0, 0)
    assert seen["reason"] == (
        "Scheduled 3 AM Pacific memory recycle; exiting so the host starts a fresh API process"
    )
