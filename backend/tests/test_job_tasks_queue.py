"""Catalog job queue: one active run per account by default."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app import job_tasks


class JobTasksQueueTests(unittest.TestCase):
    def tearDown(self) -> None:
        with job_tasks._job_cancels_lock:
            job_tasks._job_cancels.clear()
            job_tasks._job_accounts.clear()

    @patch.object(job_tasks, "try_start_next_queued_job")
    @patch.object(job_tasks, "effective_running_count", return_value=0)
    @patch.object(job_tasks, "SessionLocal")
    def test_kick_queue_if_idle_starts_when_only_queued(
        self, mock_session_local: object, _mock_eff: object, mock_try: object
    ) -> None:
        class _FakeQuery:
            def filter(self, *args: object, **kwargs: object) -> _FakeQuery:
                return self

            def first(self) -> object:
                return object()

        class _FakeDb:
            def query(self, *args: object, **kwargs: object) -> _FakeQuery:
                return _FakeQuery()

            def __enter__(self) -> _FakeDb:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        mock_session_local.return_value = _FakeDb()
        job_tasks.kick_queue_if_idle("acct-1")
        mock_try.assert_called_once_with("acct-1")

    def test_second_start_returns_false_when_at_capacity(self) -> None:
        with patch.object(job_tasks.settings, "catalog_job_max_concurrent", 1):
            with patch.object(job_tasks.threading, "Thread") as mock_thread:
                mock_thread.return_value.start = lambda: None
                ok1 = job_tasks.start_job_in_background("job-a", "acct-1")
                ok2 = job_tasks.start_job_in_background("job-b", "acct-1")
        self.assertTrue(ok1)
        self.assertFalse(ok2)

    def test_different_accounts_can_run_together(self) -> None:
        with patch.object(job_tasks.settings, "catalog_job_max_concurrent", 1):
            with patch.object(job_tasks, "effective_running_count", return_value=0):
                with patch.object(job_tasks.threading, "Thread") as mock_thread:
                    mock_thread.return_value.start = lambda: None
                    ok1 = job_tasks.start_job_in_background("job-a", "acct-1")
                    ok2 = job_tasks.start_job_in_background("job-b", "acct-2")
        self.assertTrue(ok1)
        self.assertTrue(ok2)


if __name__ == "__main__":
    unittest.main()
