"""Tests for account-scoped upload job folder resolution."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.upload_storage import resolve_account_upload_folder


def test_resolve_account_upload_folder_accepts_own_existing_batch(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)
    batch = tmp_path / "data" / "uploads" / "acct-1" / "batch-1"
    batch.mkdir(parents=True)

    assert resolve_account_upload_folder("acct-1", str(batch)) == batch.resolve()


def test_resolve_account_upload_folder_rejects_other_account_batch(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)
    batch = tmp_path / "data" / "uploads" / "acct-2" / "batch-1"
    batch.mkdir(parents=True)

    with pytest.raises(HTTPException) as exc:
        resolve_account_upload_folder("acct-1", str(batch))

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid job upload path"


def test_resolve_account_upload_folder_rejects_missing_own_batch(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)
    batch = tmp_path / "data" / "uploads" / "acct-1" / "missing"

    with pytest.raises(HTTPException) as exc:
        resolve_account_upload_folder("acct-1", str(batch))

    assert exc.value.status_code == 400
    assert exc.value.detail == "Job upload folder is missing"
