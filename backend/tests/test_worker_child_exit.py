"""Isolated child must exit after a complex/Pro-author job is persisted."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import JobStatus, Photo, ProcessingJob
from app.services import processing
from app.services.worker_child_lifecycle import (
    log_isolated_job_complete,
    note_job_persisted,
    reset_isolated_child_lifecycle_for_tests,
)
from app import worker


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'exit.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def _job(db, *, created_at: datetime) -> ProcessingJob:
    row = ProcessingJob(
        id="job-complex",
        account_id="acct-1",
        user_id="user-1",
        sale_id="sale-1",
        input_folder="/tmp/batch",
        single_lot_mode=True,
        status=JobStatus.completed,
        analyzed_photos=5,
        created_at=created_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_verify_does_not_download_older_account_photos(tmp_path, monkeypatch) -> None:
    """The post-complete check used resolve_catalog_storage_path on every account photo.

    That downloaded the whole franchise catalog from R2 and hung the child after the
    lot was already saved (job db5b42d2, complex Pro-author path).
    """
    db = _session(tmp_path)
    now = datetime.now(timezone.utc)
    job = _job(db, created_at=now)
    for i in range(40):
        db.add(
            Photo(
                id=f"old-{i}",
                account_id="acct-1",
                user_id="user-1",
                original_name=f"old-{i}.jpg",
                storage_path=f"catalog/acct-1/old-{i}.jpg",
                created_at=now - timedelta(days=10),
            )
        )
    db.add(
        Photo(
            id="new-1",
            account_id="acct-1",
            user_id="user-1",
            original_name="mobile-draft-01.jpg",
            storage_path="catalog/acct-1/new-1.jpg",
            created_at=now,
        )
    )
    db.commit()

    resolved: list[str] = []
    checked: list[str] = []

    def _resolve(path):
        resolved.append(str(path))
        return Path("/missing")

    monkeypatch.setattr(processing, "resolve_catalog_storage_path", _resolve)
    monkeypatch.setattr(
        processing, "catalog_photo_file_on_disk", lambda path: checked.append(str(path)) or True
    )

    processing._verify_job_catalog_files_on_disk(db, job)
    assert resolved == []
    assert checked == ["catalog/acct-1/new-1.jpg"]


def test_note_job_persisted_logs_complete_once(caplog, monkeypatch) -> None:
    reset_isolated_child_lifecycle_for_tests()
    monkeypatch.setenv("WORKER_ISOLATED_CHILD", "1")
    monkeypatch.setattr(
        "app.services.worker_child_lifecycle.arm_isolated_child_exit_watchdog",
        lambda code=0: None,
    )
    with caplog.at_level("INFO"):
        note_job_persisted(
            job_id="job-x",
            account_id="acct-x",
            status="completed",
            lots=1,
            photos=5,
            duration_s=12.0,
        )
        log_isolated_job_complete(
            job_id="job-x",
            account_id="acct-x",
            status="completed",
            lots=1,
            photos=5,
        )
    text = " ".join(rec.getMessage() for rec in caplog.records)
    assert text.count("Isolated catalog job complete") == 1
    assert "job-x" in text
    reset_isolated_child_lifecycle_for_tests()


def test_parent_kills_child_idle_after_job_done(monkeypatch) -> None:
    monkeypatch.setattr(worker, "_job_is_terminal", lambda j, a: "completed")
    monkeypatch.setattr(worker.settings, "worker_child_done_kill_seconds", 1.0)
    killed: list[int] = []
    proc = SimpleNamespace(pid=9, kill=lambda: killed.append(9), poll=lambda: None)
    slot = worker.IsolatedChild(proc=proc, account_id="acct-1", started_mono=0.0)
    worker._maybe_kill_idle_done_child("job-done", slot)
    assert killed == []
    assert "done_mono" in slot.extra
    slot.extra["done_mono"] = time.monotonic() - 5
    worker._maybe_kill_idle_done_child("job-done", slot)
    assert killed == [9]


def test_run_isolated_job_stops_waiting_when_db_says_done(monkeypatch) -> None:
    """Complex/Pro path can persist the lot while the job thread is still in post-complete work."""
    monkeypatch.setenv("WORKER_ISOLATED_CHILD", "1")
    monkeypatch.setattr(worker.job_tasks, "start_job_in_background", lambda *a, **k: True)
    monkeypatch.setattr(worker.job_tasks, "is_job_running", lambda job_id: True)
    monkeypatch.setattr(worker, "_job_is_terminal", lambda j, a: "completed")
    monkeypatch.setattr(worker, "_job_finish_stats", lambda j, a: ("completed", 5, 1))
    monkeypatch.setattr(worker, "_apply_child_as_limit", lambda: None)
    reset_isolated_child_lifecycle_for_tests()
    code = worker.run_isolated_job("job-complex", "acct-1")
    assert code == 0
    reset_isolated_child_lifecycle_for_tests()


def _worker_test_env() -> dict[str, str]:
    env = os.environ.copy()
    env["ENVIRONMENT"] = "test"
    env.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use")
    env["WORKER_CHILD_EXIT_SECONDS"] = "2"
    env["WORKER_ISOLATED_CHILD"] = "1"
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def test_complex_pro_author_path_child_exits_when_cleanup_hangs() -> None:
    """After Pro author + pinned Flash, persist complete then hang — watchdog must os._exit."""
    proc = subprocess.run(
        [sys.executable, "-m", "app.worker", "--self-test-complex-hang"],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        env=_worker_test_env(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "Isolated catalog job complete" in out
    assert "Pipeline Pro author succeeded" in out
    assert "watchdog fired" in out
