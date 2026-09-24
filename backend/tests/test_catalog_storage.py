"""Tests for permanent catalog photo storage."""

from __future__ import annotations

from pathlib import Path

from app.catalog_storage import catalog_photo_file_on_disk, persist_catalog_photo, upload_root
from app.services.photo_captured_at import captured_at_from_sidecar, sidecar_path, write_captured_at_sidecar


def test_persist_catalog_photo_copies_under_catalog_subdir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)
    src = tmp_path / "batch" / "shot.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\xff\xd8\xff\xd9")

    photo_id, key = persist_catalog_photo("acct-1", src)
    assert photo_id
    assert key.startswith("catalog/acct-1/")
    assert catalog_photo_file_on_disk(key)
    assert (upload_root() / key).is_file()


def test_persist_catalog_photo_copies_captured_at_sidecar(tmp_path, monkeypatch) -> None:
    from datetime import datetime, timezone

    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)
    src = tmp_path / "batch" / "shot.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\xff\xd8\xff\xd9")
    stamp = datetime(2026, 9, 23, 14, 5, tzinfo=timezone.utc)
    write_captured_at_sidecar(src, stamp)

    photo_id, key = persist_catalog_photo("acct-1", src)
    dest = upload_root() / key
    assert captured_at_from_sidecar(dest) == stamp
    assert sidecar_path(dest).is_file()
    assert photo_id
