"""Catalog job queue: one active run per account by default."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app import job_tasks
from app.models import JobStatus


class JobTasksQueueTests(unittest.TestCase):
    def tearDown(self) -> None:
        with job_tasks._job_cancels_lock:
            job_tasks._job_cancels.clear()
            job_tasks._job_accounts.clear()

    @patch.object(job_tasks, "reconcile_memory_threads_for_account")
    @patch.object(job_tasks, "try_start_next_queued_job")
    @patch.object(job_tasks, "effective_running_count", return_value=0)
    @patch.object(job_tasks, "SessionLocal")
    def test_kick_queue_if_idle_starts_when_only_queued(
        self,
        mock_session_local: object,
        _mock_eff: object,
        mock_try: object,
        _mock_reconcile: object,
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

    @patch.object(job_tasks, "reconcile_memory_threads_for_account")
    @patch.object(job_tasks, "try_start_next_queued_job")
    @patch.object(job_tasks, "effective_running_count", return_value=0)
    @patch.object(job_tasks, "SessionLocal")
    def test_kick_queue_if_idle_does_not_start_without_queued_rows(
        self,
        mock_session_local: object,
        _mock_eff: object,
        mock_try: object,
        _mock_reconcile: object,
    ) -> None:
        class _FakeQuery:
            def filter(self, *args: object, **kwargs: object) -> _FakeQuery:
                return self

            def first(self) -> None:
                return None

        class _FakeDb:
            def query(self, *args: object, **kwargs: object) -> _FakeQuery:
                return _FakeQuery()

            def __enter__(self) -> _FakeDb:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        mock_session_local.return_value = _FakeDb()
        job_tasks.kick_queue_if_idle("acct-1")
        mock_try.assert_not_called()

    @patch.object(job_tasks, "_claim_queued_job", return_value=True)
    @patch.object(job_tasks, "can_start_job_for_account", return_value=True)
    @patch.object(job_tasks, "reconcile_memory_threads_for_account")
    @patch.object(job_tasks.threading, "Thread")
    def test_start_claims_job_before_thread(
        self,
        mock_thread: object,
        _mock_reconcile: object,
        _mock_can: object,
        mock_claim: object,
    ) -> None:
        mock_thread.return_value.start = lambda: None
        with patch.object(job_tasks, "_processing_job_ids_in_db", return_value=set()):
            ok = job_tasks.start_job_in_background("job-a", "acct-1")
        self.assertTrue(ok)
        mock_claim.assert_called_once_with("job-a", "acct-1")

    @patch.object(job_tasks, "_claim_queued_job", return_value=False)
    @patch.object(job_tasks, "can_start_job_for_account", return_value=True)
    @patch.object(job_tasks, "reconcile_memory_threads_for_account")
    def test_start_clears_registration_when_claim_fails(
        self,
        _mock_reconcile: object,
        _mock_can: object,
        mock_claim: object,
    ) -> None:
        with patch.object(job_tasks, "_processing_job_ids_in_db", return_value=set()):
            ok = job_tasks.start_job_in_background("job-uploading", "acct-1")
        self.assertFalse(ok)
        mock_claim.assert_called_once_with("job-uploading", "acct-1")
        self.assertFalse(job_tasks.is_job_running("job-uploading"))

    @patch.object(job_tasks, "can_start_job_for_account", return_value=False)
    def test_second_start_returns_false_when_at_capacity(self, _mock_can: object) -> None:
        with patch.object(job_tasks, "_processing_job_ids_in_db", return_value=set()):
            ok = job_tasks.start_job_in_background("job-b", "acct-1")
        self.assertFalse(ok)

    def test_reconcile_does_not_fail_recent_processing_on_other_replica(self) -> None:
        row = MagicMock()
        row.id = "job-recent"
        row.status = JobStatus.processing
        row.updated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        row.error_message = None
        row.cancel_requested = False

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [row]

        with patch.object(job_tasks, "is_job_running", return_value=False):
            changed = job_tasks.reconcile_stale_processing_jobs(db, "acct-1")

        self.assertFalse(changed)
        self.assertEqual(row.status, JobStatus.processing)

    def test_reconcile_fails_stale_processing(self) -> None:
        row = MagicMock()
        row.id = "job-stale"
        row.status = JobStatus.processing
        row.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
        row.error_message = None
        row.cancel_requested = False

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [row]

        with patch.object(job_tasks, "is_job_running", return_value=False):
            changed = job_tasks.reconcile_stale_processing_jobs(db, "acct-1")

        self.assertTrue(changed)
        self.assertEqual(row.status, JobStatus.failed)
        self.assertIn("restart", (row.error_message or "").lower())


if __name__ == "__main__":
    unittest.main()
