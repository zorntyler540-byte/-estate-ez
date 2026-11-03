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
        return MagicMock(poll=lambda: None)

    monkeypatch.setattr(worker, "claim_next_fair_job", _claim)
    monkeypatch.setattr(worker, "_spawn_isolated_job_async", _spawn)
    children: dict = {}
    started = worker._fill_worker_slots(isolate=True, children=children, cap=3)
    assert started is True
    assert list(children) == ["j1", "j2", "j3"]
    assert spawned == [("j1", "a1"), ("j2", "a2"), ("j3", "a3")]
    assert queue == [("j4", "a4")]
    assert job_tasks.isolated_child_job_ids() == {"j1", "j2", "j3"}


def test_reap_unregisters_and_requeues_crashed_child(monkeypatch) -> None:
    requeued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        worker, "_requeue_if_still_processing", lambda j, a: requeued.append((j, a)) or True
    )
    job_tasks.register_isolated_child_job("dead", "acct-1")
    proc = SimpleNamespace(poll=lambda: 1)
    children = {"dead": (proc, "acct-1")}
    worker._reap_isolated_children(children)
    assert children == {}
    assert "dead" not in job_tasks.isolated_child_job_ids()
    assert requeued == [("dead", "acct-1")]


def test_worker_slot_capacity_reads_setting(monkeypatch) -> None:
    monkeypatch.setattr(worker.settings, "worker_max_concurrent_jobs", 4)
    assert worker.worker_slot_capacity() == 4


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
