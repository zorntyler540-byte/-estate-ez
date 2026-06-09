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

    def test_second_start_returns_false_when_at_capacity(self) -> None:
        with patch.object(job_tasks.settings, "catalog_job_max_concurrent", 1):
            with patch.object(job_tasks.threading, "Thread") as mock_thread:
                mock_thread.return_value.start = lambda: None
                ok1 = job_tasks.start_job_in_background("job-a", "acct-1")
                ok2 = job_tasks.start_job_in_background("job-b", "acct-1")
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertEqual(job_tasks.running_count_for_account("acct-1"), 1)

    def test_different_accounts_can_run_together(self) -> None:
        with patch.object(job_tasks.settings, "catalog_job_max_concurrent", 1):
            with patch.object(job_tasks.threading, "Thread") as mock_thread:
                mock_thread.return_value.start = lambda: None
                ok1 = job_tasks.start_job_in_background("job-a", "acct-1")
                ok2 = job_tasks.start_job_in_background("job-b", "acct-2")
        self.assertTrue(ok1)
        self.assertTrue(ok2)


if __name__ == "__main__":
    unittest.main()
