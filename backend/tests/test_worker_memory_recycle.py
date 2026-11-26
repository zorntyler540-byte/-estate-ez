"""Worker nightly memory recycle: 3 AM Pacific, jitter, toggle, and drain."""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from app.services import api_memory_recycle as recycle
from app.services.api_memory_recycle import (
    RECYCLE_EXIT_CODE,
    WORKER_DRAIN_MAX_SECONDS,
    WORKER_JITTER_MAX_SECONDS,
    WorkerRecycleGate,
    memory_recycle_enabled,
    next_recycle_at,
    plan_worker_recycle,
    sleep_until,
    start_worker_memory_recycle,
    worker_recycle_action,
    worker_recycle_exit_reason,
    worker_recycle_jitter,
    worker_recycle_should_start,
)
from app import worker

PT = ZoneInfo("America/Los_Angeles")


class _Roll:
    def __init__(self, value: int) -> None:
        self.value = value
        self.bounds: tuple[int, int] | None = None

    def randint(self, a: int, b: int) -> int:
        self.bounds = (a, b)
        return self.value


def test_jitter_is_inclusive_zero_to_fifteen_minutes() -> None:
    assert WORKER_JITTER_MAX_SECONDS == 15 * 60
    assert WORKER_DRAIN_MAX_SECONDS == 10 * 60
    low = _Roll(0)
    high = _Roll(WORKER_JITTER_MAX_SECONDS)
    assert worker_recycle_jitter(low) == timedelta(0)
    assert worker_recycle_jitter(high) == timedelta(minutes=15)
    assert low.bounds == (0, WORKER_JITTER_MAX_SECONDS)
    assert high.bounds == (0, WORKER_JITTER_MAX_SECONDS)


def test_jitter_samples_stay_inside_the_window() -> None:
    rng = random.Random(12345)
    samples = [worker_recycle_jitter(rng) for _ in range(200)]
    assert len({sample.total_seconds() for sample in samples}) > 1
    for sample in samples:
        assert timedelta(0) <= sample <= timedelta(minutes=15)


def test_worker_schedule_is_3am_plus_jitter_same_morning() -> None:
    now = datetime(2026, 11, 24, 2, 30, tzinfo=PT)
    got = plan_worker_recycle(now, rng=_Roll(7 * 60))
    assert got.astimezone(PT) == datetime(2026, 11, 24, 3, 7, tzinfo=PT)


def test_worker_schedule_rolls_to_next_day_after_3am() -> None:
    now = datetime(2026, 11, 24, 3, 10, tzinfo=PT)
    got = next_recycle_at(now, jitter=timedelta(minutes=5))
    assert got.astimezone(PT) == datetime(2026, 11, 25, 3, 5, tzinfo=PT)


def test_zero_or_negative_jitter_matches_the_api_instant() -> None:
    now = datetime(2026, 11, 24, 2, 0, tzinfo=PT)
    base = next_recycle_at(now)
    assert next_recycle_at(now, jitter=timedelta(0)) == base
    assert next_recycle_at(now, jitter=timedelta(minutes=-30)) == base
    assert base.astimezone(PT) == datetime(2026, 11, 24, 3, 0, tzinfo=PT)


def test_recycle_exit_code_restarts_on_failure() -> None:
    assert RECYCLE_EXIT_CODE == 1


def test_toggle_defaults_on_and_false_disables(monkeypatch) -> None:
    monkeypatch.delenv("WORKER_MEMORY_RECYCLE", raising=False)
    assert memory_recycle_enabled("WORKER_MEMORY_RECYCLE") is True
    assert worker_recycle_should_start(is_test=False) is True
    for value in ("false", "0", "no", "off", "FALSE"):
        monkeypatch.setenv("WORKER_MEMORY_RECYCLE", value)
        assert memory_recycle_enabled("WORKER_MEMORY_RECYCLE") is False
        assert worker_recycle_should_start(is_test=False) is False
    # Same off-set the API uses.
    for value in ("false", "0", "no", "off"):
        monkeypatch.setenv("API_MEMORY_RECYCLE", value)
        assert memory_recycle_enabled("API_MEMORY_RECYCLE") is False
    monkeypatch.setenv("API_MEMORY_RECYCLE", "true")
    assert memory_recycle_enabled("API_MEMORY_RECYCLE") is True


def test_toggle_and_start_are_skipped_in_tests() -> None:
    assert worker_recycle_should_start(is_test=True) is False
    before = {thread.name for thread in threading.enumerate()}
    assert start_worker_memory_recycle() is None
    started = {thread.name for thread in threading.enumerate()} - before
    assert "worker-memory-recycle" not in started


def test_disabled_toggle_does_not_start_a_thread(monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setenv("WORKER_MEMORY_RECYCLE", "false")
    before = {thread.name for thread in threading.enumerate()}
    assert start_worker_memory_recycle() is None
    started = {thread.name for thread in threading.enumerate()} - before
    assert "worker-memory-recycle" not in started


def test_enabled_toggle_starts_the_daemon(monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.delenv("WORKER_MEMORY_RECYCLE", raising=False)
    seen: dict[str, WorkerRecycleGate] = {}

    def _capture(gate: WorkerRecycleGate) -> None:
        seen["gate"] = gate

    monkeypatch.setattr(recycle, "_worker_recycle_loop", _capture)
    gate = start_worker_memory_recycle()
    assert gate is not None
    deadline = time.monotonic() + 2
    while "gate" not in seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen["gate"] is gate
    assert gate.draining is False


def test_worker_loop_arms_gate_then_backs_out_after_drain(monkeypatch) -> None:
    sleeps: list[object] = []
    monkeypatch.setattr(recycle, "sleep_until", lambda target: sleeps.append(target))
    monkeypatch.setattr(recycle.time, "sleep", lambda seconds: sleeps.append(("sleep", seconds)))
    reasons: list[str] = []
    monkeypatch.setattr(recycle, "exit_for_memory_recycle", lambda reason: reasons.append(reason))
    monkeypatch.setattr(recycle, "worker_recycle_jitter", lambda rng=None: timedelta(minutes=5))
    gate = WorkerRecycleGate()
    recycle._worker_recycle_loop(gate)
    assert gate.draining is True
    target = sleeps[0]
    assert isinstance(target, datetime)
    local = target.astimezone(PT)
    assert (local.hour, local.minute, local.second) == (3, 5, 0)
    assert ("sleep", WORKER_DRAIN_MAX_SECONDS) in sleeps
    assert reasons == [worker_recycle_exit_reason(drain_expired=True)]


def test_drain_deadline_is_ten_minutes_and_does_not_move() -> None:
    gate = WorkerRecycleGate()
    assert gate.draining is False
    assert gate.drain_expired(now_mono=10_000.0) is False
    gate.arm(now_mono=1_000.0, drain_seconds=WORKER_DRAIN_MAX_SECONDS)
    gate.arm(now_mono=1_500.0, drain_seconds=WORKER_DRAIN_MAX_SECONDS)
    assert gate.draining is True
    assert gate.drain_expired(now_mono=1_000.0 + WORKER_DRAIN_MAX_SECONDS - 0.1) is False
    assert gate.drain_expired(now_mono=1_000.0 + WORKER_DRAIN_MAX_SECONDS) is True


def test_action_claims_until_recycle_then_waits_and_exits() -> None:
    assert worker_recycle_action(draining=False, busy=True, drain_expired=False) == "claim"
    assert worker_recycle_action(draining=False, busy=False, drain_expired=True) == "claim"
    assert worker_recycle_action(draining=True, busy=True, drain_expired=False) == "wait"
    assert worker_recycle_action(draining=True, busy=False, drain_expired=False) == "exit"
    assert worker_recycle_action(draining=True, busy=True, drain_expired=True) == "exit"
    assert "in-flight work finished" in worker_recycle_exit_reason(drain_expired=False)
    assert "drain wait elapsed" in worker_recycle_exit_reason(drain_expired=True)


def test_live_work_covers_catalog_slots_and_social_children() -> None:
    social = worker.SocialSweepState()
    publish = worker.SocialPublishState()
    assert (
        worker._worker_has_live_work(
            isolate=True, children={}, social=social, publish=publish
        )
        is False
    )

    class Alive:
        def poll(self):
            return None

    class Finished:
        def poll(self):
            return 0

    publish.child = Alive()  # type: ignore[assignment]
    assert (
        worker._worker_has_live_work(
            isolate=True, children={}, social=social, publish=publish
        )
        is True
    )
    publish.child = Finished()  # type: ignore[assignment]
    assert (
        worker._worker_has_live_work(
            isolate=True, children={}, social=social, publish=publish
        )
        is False
    )
    assert (
        worker._worker_has_live_work(
            isolate=True, children={"job": object()}, social=social, publish=publish  # type: ignore[dict-item]
        )
        is True
    )


def test_social_children_are_not_restarted_while_draining(monkeypatch) -> None:
    def boom_sweep():
        raise AssertionError("sweep spawned")

    monkeypatch.setattr(worker, "_spawn_social_sweep", boom_sweep)
    sweep = worker.SocialSweepState()
    assert worker.maybe_social_sweep(sweep, isolate=True, now_mono=1000.0, allow_start=False) is False
    assert sweep.child is None

    class Finished:
        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    sweep.child = Finished()  # type: ignore[assignment]
    sweep.child_started_mono = 1.0
    assert worker.maybe_social_sweep(sweep, isolate=True, now_mono=50.0, allow_start=False) is False
    assert sweep.child is None

    def boom_due():
        raise AssertionError("publish due query")

    publish = worker.SocialPublishState()
    assert (
        worker.maybe_social_publish(
            publish, isolate=True, now_mono=1000.0, allow_start=False, due=boom_due
        )
        is False
    )


def test_sleep_until_returns_immediately_when_target_has_passed(monkeypatch) -> None:
    calls: list[float] = []
    monkeypatch.setattr(recycle.time, "sleep", lambda seconds: calls.append(seconds))
    sleep_until(datetime.now(ZoneInfo("UTC")) - timedelta(seconds=5))
    assert calls == []


def test_worker_image_sets_malloc_arena_max() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "infra" / "docker" / "Dockerfile.worker").read_text()
    assert "ENV MALLOC_ARENA_MAX=2" in dockerfile
    assert "app.worker" in dockerfile
    source = (root / "backend" / "app" / "worker.py").read_text()
    assert 'os.environ.setdefault("MALLOC_ARENA_MAX", "2")' in source
