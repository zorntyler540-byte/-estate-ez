"""Pytest defaults: allow Settings() to load without a production SECRET_KEY."""

from __future__ import annotations

import os

import pytest

os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use")


@pytest.fixture(autouse=True)
def _inline_snapshot_calendar_sync(monkeypatch):
    """Run the post-snapshot Facebook calendar sync before the ingest call returns.

    Production queues it on a daemon thread; tests assert on Graph mocks right after
    the POST. Tests that cover the thread itself re-patch ``_start_thread``.
    """
    from app import engagement_tasks

    engagement_tasks.reset_engagement_task_state()
    monkeypatch.setattr(engagement_tasks, "_start_thread", lambda target, name: target())
    yield
    engagement_tasks.reset_engagement_task_state()
