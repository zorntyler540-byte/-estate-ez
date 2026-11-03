"""Sequential isolated children must exit; parent RSS must return to baseline.

Reproduces the 2026-11-03 Railway leak: after Gemini finished, the child stayed
alive (non-daemon SDK / ThreadPoolExecutor threads survive ``SystemExit``), so
cgroup memory climbed for ~15 minutes with no log line. The parent now
``os._exit``s the child after each job.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from app import worker


def _worker_test_env() -> dict[str, str]:
    env = os.environ.copy()
    env["ENVIRONMENT"] = "test"
    env.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use")
    env.setdefault("MALLOC_ARENA_MAX", "2")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def test_parse_self_test_alloc_mb() -> None:
    assert worker.parse_self_test_alloc_mb(["app.worker", "--self-test-alloc-mb", "40"]) == 40
    assert worker.parse_self_test_alloc_mb(["app.worker"]) is None
    assert worker.parse_self_test_parent_jobs(["app.worker", "--self-test-parent-jobs", "4", "48"]) == (
        4,
        48,
    )
    assert worker.parse_worker_argv(["app.worker", "--self-test-alloc-mb", "40"]) is None


@pytest.mark.skipif(not os.path.exists("/proc/self/statm"), reason="needs Linux /proc RSS")
def test_parent_rss_returns_after_sequential_isolated_jobs() -> None:
    """Several jobs in a row: each child allocates RAM, then must fully exit.

    A leftover child (the production bug) would leave ~N × alloc_mb in tree RSS.
    """
    jobs = 4
    alloc_mb = 48
    proc = subprocess.run(
        [sys.executable, "-m", "app.worker", "--self-test-parent-jobs", str(jobs), str(alloc_mb)],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        env=_worker_test_env(),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, f"no JSON RSS line in stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = json.loads(lines[-1])
    before_parent = float(payload["before_parent_rss_mb"])
    after_parent = float(payload["after_parent_rss_mb"])
    before_tree = float(payload["before_tree_rss_mb"])
    after_tree = float(payload["after_tree_rss_mb"])

    # Four 48 MiB children would leave ~192 MiB if they leaked into the parent tree.
    slack_mb = 40
    assert after_tree <= before_tree + slack_mb, (
        f"worker tree RSS did not return to baseline: {payload}"
    )
    assert after_parent <= before_parent + slack_mb, (
        f"parent RSS grew after sequential jobs: {payload}"
    )
    assert after_parent < 300, f"idle parent RSS exceeds 300 MB target: {payload}"


@pytest.mark.skipif(not os.path.exists("/proc/self/statm"), reason="needs Linux /proc RSS")
def test_self_test_child_exits_despite_thread_pool() -> None:
    """``SystemExit`` would wait forever on the leftover ThreadPoolExecutor."""
    proc = subprocess.run(
        [sys.executable, "-m", "app.worker", "--self-test-alloc-mb", "8"],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        env=_worker_test_env(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
