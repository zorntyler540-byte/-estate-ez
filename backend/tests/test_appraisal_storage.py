"""Tests for appraisal photo storage."""

from __future__ import annotations

from app.appraisal_storage import (
    appraisal_photo_file_on_disk,
    persist_appraisal_photo_bytes,
    upload_root,
)


def test_persist_appraisal_photo_under_appraisals_subdir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.appraisal_storage._BACKEND_DIR", tmp_path)
    key = persist_appraisal_photo_bytes("acct-1", "check-1", index=0, data=b"\xff\xd8\xff\xd9", suffix=".jpg")
    assert key.startswith("appraisals/acct-1/check-1/")
    assert appraisal_photo_file_on_disk(key)
    assert (upload_root() / key).is_file()
