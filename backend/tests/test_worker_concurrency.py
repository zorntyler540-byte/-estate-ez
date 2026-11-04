"""Worker child-process pool fills multiple slots without waiting serially."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app import job_tasks
from app import worker


def setup_function() -> None:
    with job_tasks._job_cancels_lock:
        job_tasks._job_cancels.clear()
        job_tasks._job_accounts.clear()
        job_tasks._isolated_child_jobs.clear()


def teardown_function() -> None:
    setup_function()


def test_fill_worker_slots_starts_up_to_cap(monkeypatch) -> None:
    queue = [("j1", "a1"), ("j2", "a2"), ("j3", "a3"), ("j4", "a4")]

    def _claim():
        return queue.pop(0) if queue else None

    spawned: list[tuple[str, str]] = []

    def _spawn(job_id: str, account_id: str) -> MagicMock:
        spawned.append((job_id, account_id))
        return MagicMock(poll=lambda: None, pid=1000 + len(spawned))

    monkeypatch.setattr(worker, "claim_next_fair_job", _claim)
    monkeypatch.setattr(worker, "_spawn_isolated_job_async", _spawn)
    children: dict = {}
    started = worker._fill_worker_slots(isolate=True, children=children, cap=3)
    assert started is True
    assert list(children) == ["j1", "j2", "j3"]
    assert spawned == [("j1", "a1"), ("j2", "a2"), ("j3", "a3")]
    assert queue == [("j4", "a4")]
    assert job_tasks.isolated_child_job_ids() == {"j1", "j2", "j3"}
    assert all(isinstance(slot, worker.IsolatedChild) for slot in children.values())


def test_reap_unregisters_and_requeues_crashed_child(monkeypatch) -> None:
    requeued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        worker, "_requeue_if_still_processing", lambda j, a: requeued.append((j, a)) or True
    )
    monkeypatch.setattr(worker, "_job_finish_stats", lambda j, a: ("failed", 0, 0))
    job_tasks.register_isolated_child_job("dead", "acct-1")
    proc = SimpleNamespace(poll=lambda: 1, wait=lambda timeout=None: 1, pid=4242)
    children = {
        "dead": worker.IsolatedChild(proc=proc, account_id="acct-1", started_mono=0.0, peak_rss_mb=12)
    }
    worker._reap_isolated_children(children)
    assert children == {}
    assert "dead" not in job_tasks.isolated_child_job_ids()
    assert requeued == [("dead", "acct-1")]


def test_reap_logs_success_without_requeue(monkeypatch, caplog) -> None:
    requeued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        worker, "_requeue_if_still_processing", lambda j, a: requeued.append((j, a)) or False
    )
    monkeypatch.setattr(worker, "_job_finish_stats", lambda j, a: ("completed", 3, 1))
    job_tasks.register_isolated_child_job("ok", "acct-1")
    proc = SimpleNamespace(poll=lambda: 0, wait=lambda timeout=None: 0, pid=7)
    children = {
        "ok": worker.IsolatedChild(
            proc=proc, account_id="acct-1", started_mono=0.0, peak_rss_mb=1280.0
        )
    }
    with caplog.at_level("INFO", logger="app.worker"):
        worker._reap_isolated_children(children)
    assert children == {}
    assert requeued == [("ok", "acct-1")]
    text = " ".join(rec.getMessage() for rec in caplog.records)
    assert "Isolated catalog child exited" in text
    assert "peak_child_rss_mb=1280" in text
    assert "lots=1" in text


def test_oversize_child_is_killed(monkeypatch) -> None:
    monkeypatch.setattr(worker.settings, "worker_child_rss_mb", 3000)
    monkeypatch.setattr(worker, "process_rss_mb", lambda pid=None: 3100.0)
    killed: list[int] = []
    proc = SimpleNamespace(pid=99, kill=lambda: killed.append(99), poll=lambda: None)
    slot = worker.IsolatedChild(proc=proc, account_id="a", started_mono=0.0)
    worker._sample_child_rss("big", slot)
    assert killed == [99]
    assert slot.peak_rss_mb == 3100.0


def test_worker_slot_capacity_reads_setting(monkeypatch) -> None:
    monkeypatch.setattr(worker.settings, "worker_max_concurrent_jobs", 4)
    assert worker.worker_slot_capacity() == 4


def test_parse_self_test_complex_hang() -> None:
    assert worker.parse_self_test_complex_hang(["app.worker", "--self-test-complex-hang"]) is True
    assert worker.parse_self_test_complex_hang(["app.worker"]) is False


def test_child_rss_cap_defaults_above_measured_peak() -> None:
    # Open PR #204 proposed 1200; today's 3-photo job went over 1.2 GB.
    assert worker.child_rss_cap_mb() >= 3000


def test_start_already_claimed_skips_claim(monkeypatch) -> None:
    claimed = []
    monkeypatch.setattr(job_tasks, "catalog_jobs_run_in_process", lambda: True)
    monkeypatch.setattr(job_tasks, "reconcile_memory_threads_for_account", lambda *_a, **_k: None)
    monkeypatch.setattr(job_tasks, "can_start_job_for_account", lambda *_a, **_k: False)
    monkeypatch.setattr(job_tasks, "_clear_stale_registration", lambda *_a, **_k: None)
    monkeypatch.setattr(job_tasks, "_claim_queued_job", lambda j, a: claimed.append((j, a)) or True)
    monkeypatch.setattr(job_tasks.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    ok = job_tasks.start_job_in_background("job-x", "acct-x", already_claimed=True)
    assert ok is True
    assert claimed == []
