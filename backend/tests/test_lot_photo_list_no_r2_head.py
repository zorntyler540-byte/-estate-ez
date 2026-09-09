"""Lot photo/video list endpoints must not HEAD R2 (pins the DB pool)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.catalog_storage import catalog_file_listed_without_remote_stat
from app.services.serialization import lot_photo_out, lot_video_out


def test_listed_without_remote_stat_skips_r2_head(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr("app.object_storage.r2_configured", lambda: True)
    monkeypatch.setattr("app.object_storage.object_exists", lambda key: called.append(key) or True)

    assert catalog_file_listed_without_remote_stat("catalog/acct/shot.jpg") is True
    assert catalog_file_listed_without_remote_stat("") is False
    assert catalog_file_listed_without_remote_stat(None) is False
    assert called == []


def test_lot_photo_out_does_not_head_r2(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr("app.object_storage.r2_configured", lambda: True)
    monkeypatch.setattr("app.object_storage.object_exists", lambda key: called.append(key) or True)

    photo = SimpleNamespace(
        id="p1",
        lot_id="lot-1",
        original_name="shot.jpg",
        created_at=datetime.now(timezone.utc),
        sort_order=0,
        storage_path="catalog/acct/shot.jpg",
        photo_role="hero",
        distinct_item_id=None,
        is_primary_front=True,
        shows_whole_lot=True,
        shows_measuring_scale=False,
        label_legibility=None,
    )
    out = lot_photo_out(photo, lot_id="lot-1")
    assert out.file_on_disk is True
    assert called == []


def test_lot_video_out_does_not_head_r2(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr("app.object_storage.r2_configured", lambda: True)
    monkeypatch.setattr("app.object_storage.object_exists", lambda key: called.append(key) or True)

    video = SimpleNamespace(
        id="v1",
        lot_id="lot-1",
        original_name="clip.mp4",
        created_at=datetime.now(timezone.utc),
        sort_order=0,
        storage_path="catalog/acct/videos/clip.mp4",
        content_type="video/mp4",
        byte_size=12,
    )
    out = lot_video_out(video, lot_id="lot-1")
    assert out.file_on_disk is True
    assert called == []
