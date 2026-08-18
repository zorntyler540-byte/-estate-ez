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
        with job_tasks._kick_debounce_lock:
            job_tasks._last_kick_mono.clear()

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

            def order_by(self, *args: object, **kwargs: object) -> _FakeQuery:
                return self

            def first(self) -> object:
                row = MagicMock()
                row.id = "job-q"
                row.updated_at = datetime.now(timezone.utc)
                row.created_at = row.updated_at
                return row

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

    def test_start_claims_job_before_thread(self) -> None:
        with (
            patch.object(job_tasks, "_clear_stale_registration"),
            patch.object(job_tasks, "_claim_queued_job", return_value=True) as mock_claim,
            patch.object(job_tasks, "can_start_job_for_account", return_value=True),
            patch.object(job_tasks, "reconcile_memory_threads_for_account"),
            patch.object(job_tasks.threading, "Thread") as mock_thread,
        ):
            mock_thread.return_value.start = lambda: None
            ok = job_tasks.start_job_in_background("job-a", "acct-1")
            self.assertTrue(ok)
            mock_claim.assert_called_once_with("job-a", "acct-1")

    def test_second_start_returns_false_when_at_capacity(self) -> None:
        with (
            patch.object(job_tasks, "reconcile_memory_threads_for_account"),
            patch.object(job_tasks, "can_start_job_for_account", return_value=False),
        ):
            ok = job_tasks.start_job_in_background("job-b", "acct-1")
            self.assertFalse(ok)

    @patch.object(job_tasks, "catalog_jobs_run_in_process", return_value=False)
    @patch.object(job_tasks, "_claim_queued_job")
    def test_start_noop_when_dedicated_worker_mode(
        self, mock_claim: object, _mock_flag: object
    ) -> None:
        ok = job_tasks.start_job_in_background("job-c", "acct-1")
        self.assertFalse(ok)
        mock_claim.assert_not_called()

    def test_force_in_process_starts_despite_worker_mode(self) -> None:
        with (
            patch.object(job_tasks, "catalog_jobs_run_in_process", return_value=False),
            patch.object(job_tasks, "_clear_stale_registration"),
            patch.object(job_tasks, "_claim_queued_job", return_value=True) as mock_claim,
            patch.object(job_tasks, "can_start_job_for_account", return_value=True),
            patch.object(job_tasks, "reconcile_memory_threads_for_account"),
            patch.object(job_tasks.threading, "Thread") as mock_thread,
        ):
            mock_thread.return_value.start = lambda: None
            ok = job_tasks.start_job_in_background(
                "job-force", "acct-1", force_in_process=True
            )
            self.assertTrue(ok)
            mock_claim.assert_called_once_with("job-force", "acct-1")

    @patch.object(job_tasks, "catalog_jobs_run_in_process", return_value=False)
    @patch.object(job_tasks, "try_start_next_queued_job")
    @patch.object(job_tasks, "effective_running_count", return_value=0)
    @patch.object(job_tasks, "reconcile_memory_threads_for_account")
    @patch.object(job_tasks, "SessionLocal")
    def test_kick_worker_mode_waits_for_fresh_queue(
        self,
        mock_session_local: object,
        _mock_reconcile: object,
        _mock_eff: object,
        mock_try: object,
        _mock_flag: object,
    ) -> None:
        row = MagicMock()
        row.id = "job-fresh"
        row.updated_at = datetime.now(timezone.utc)
        row.created_at = row.updated_at

        class _FakeQuery:
            def filter(self, *args: object, **kwargs: object) -> _FakeQuery:
                return self

            def order_by(self, *args: object, **kwargs: object) -> _FakeQuery:
                return self

            def first(self) -> object:
                return row

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

    @patch.object(job_tasks, "catalog_jobs_run_in_process", return_value=False)
    @patch.object(job_tasks, "try_start_next_queued_job")
    @patch.object(job_tasks, "effective_running_count", return_value=0)
    @patch.object(job_tasks, "reconcile_memory_threads_for_account")
    @patch.object(job_tasks, "SessionLocal")
    def test_kick_worker_mode_starts_stale_queue(
        self,
        mock_session_local: object,
        _mock_reconcile: object,
        _mock_eff: object,
        mock_try: object,
        _mock_flag: object,
    ) -> None:
        row = MagicMock()
        row.id = "job-stale-q"
        row.updated_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        row.created_at = row.updated_at

        class _FakeQuery:
            def filter(self, *args: object, **kwargs: object) -> _FakeQuery:
                return self

            def order_by(self, *args: object, **kwargs: object) -> _FakeQuery:
                return self

            def first(self) -> object:
                return row

        class _FakeDb:
            def query(self, *args: object, **kwargs: object) -> _FakeQuery:
                return _FakeQuery()

            def __enter__(self) -> _FakeDb:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        mock_session_local.return_value = _FakeDb()
        job_tasks.kick_queue_if_idle("acct-1")
        mock_try.assert_called_once_with("acct-1", force_in_process=True)

    def test_kick_queue_if_idle_async_debounces(self) -> None:
        with patch.object(job_tasks.threading, "Thread") as mock_thread:
            job_tasks.kick_queue_if_idle_async("acct-debounce")
            job_tasks.kick_queue_if_idle_async("acct-debounce")
            self.assertEqual(mock_thread.call_count, 1)

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

    def test_reconcile_keeps_active_receiving(self) -> None:
        row = MagicMock()
        row.id = "recv-active"
        row.status = JobStatus.receiving
        row.updated_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        row.created_at = row.updated_at
        row.error_message = None
        row.cancel_requested = False

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [row]

        changed = job_tasks.reconcile_stale_receiving_jobs(db, "acct-1")
        self.assertFalse(changed)
        self.assertEqual(row.status, JobStatus.receiving)

    def test_worker_orphan_requeue_skips_live_job(self) -> None:
        row = MagicMock()
        row.id = "live-1"
        row.status = JobStatus.processing
        row.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
        row.created_at = row.updated_at
        row.account_id = "acct-1"
        row.error_message = None
        row.cancel_requested = False

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [row]

        with patch.object(job_tasks, "is_job_running", return_value=True):
            n = job_tasks.reconcile_worker_orphaned_processing_jobs(db, min_age_seconds=0)

        self.assertEqual(n, 0)
        self.assertEqual(row.status, JobStatus.processing)

    def test_worker_orphan_requeue_boot_frees_capacity(self) -> None:
        row = MagicMock()
        row.id = "orphan-1"
        row.status = JobStatus.processing
        row.updated_at = datetime.now(timezone.utc)
        row.created_at = row.updated_at
        row.account_id = "acct-1"
        row.error_message = "partial"
        row.cancel_requested = False

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [row]

        with patch.object(job_tasks, "is_job_running", return_value=False):
            n = job_tasks.reconcile_worker_orphaned_processing_jobs(db, min_age_seconds=0)

        self.assertEqual(n, 1)
        self.assertEqual(row.status, JobStatus.queued)
        self.assertIsNone(row.error_message)

    def test_reconcile_fails_stale_receiving(self) -> None:
        row = MagicMock()
        row.id = "recv-stale"
        row.status = JobStatus.receiving
        row.updated_at = datetime.now(timezone.utc) - timedelta(minutes=45)
        row.created_at = row.updated_at
        row.error_message = None
        row.cancel_requested = False

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [row]

        changed = job_tasks.reconcile_stale_receiving_jobs(db, "acct-1")
        self.assertTrue(changed)
        self.assertEqual(row.status, JobStatus.failed)
        self.assertIn("never finished", (row.error_message or "").lower())


if __name__ == "__main__":
    unittest.main()
