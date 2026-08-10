"""Tests for job-batch R2 staging helpers (local path parsing / restore no-ops)."""

from __future__ import annotations

from pathlib import Path

from app.catalog_storage import upload_root
from app.job_batch_storage import (
    ensure_job_input_folder,
    parse_batch_folder,
    r2_key_for_file,
    restore_job_folder_from_r2,
)


def test_parse_batch_folder_and_key(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr("app.job_batch_storage.upload_root", lambda: root.resolve())
    monkeypatch.setattr("app.catalog_storage.upload_root", lambda: root.resolve())

    folder = root / "acct-1" / "batch-9"
    folder.mkdir(parents=True)
    assert parse_batch_folder(folder) == ("acct-1", "batch-9")
    assert r2_key_for_file("acct-1", "batch-9", "photo_1.jpg") == (
        "job-batches/acct-1/batch-9/photo_1.jpg"
    )


def test_ensure_missing_folder_without_r2_is_noop(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr("app.job_batch_storage.upload_root", lambda: root.resolve())
    monkeypatch.setattr("app.object_storage.r2_configured", lambda: False)

    folder = root / "acct" / "batch"
    out = ensure_job_input_folder(folder)
    assert out == folder.resolve()
    assert restore_job_folder_from_r2(folder) == 0
    assert not folder.exists()
