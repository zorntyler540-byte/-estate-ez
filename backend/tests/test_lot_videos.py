"""Lot video storage and listing limits (not Gemini / CT Bids)."""

from __future__ import annotations

from pathlib import Path

from app.catalog_limits import (
    is_catalog_video_filename,
    lot_video_delta_error,
    lot_video_size_error,
    video_content_type,
)
from app.catalog_storage import persist_catalog_video, upload_root
from app.services.photo_roles import photos_for_ctbids_listing
from types import SimpleNamespace


def test_video_filename_and_content_type() -> None:
    assert is_catalog_video_filename("walkaround.mp4") is True
    assert is_catalog_video_filename("clip.MOV") is True
    assert is_catalog_video_filename("note.webm") is True
    assert is_catalog_video_filename("hero.jpg") is False
    assert is_catalog_video_filename("tape.heic") is False
    assert video_content_type("a.mp4") == "video/mp4"
    assert video_content_type("a.mov") == "video/quicktime"


def test_video_size_and_count_caps() -> None:
    assert lot_video_size_error(0)
    assert lot_video_size_error(81 * 1024 * 1024)
    assert lot_video_size_error(1_000_000) is None
    assert lot_video_delta_error(current=8, delta=1)
    assert lot_video_delta_error(current=7, delta=1) is None
    assert lot_video_delta_error(current=7, delta=2)


def test_persist_catalog_video_keeps_mp4_suffix(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)
    src = tmp_path / "batch" / "clip.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    video_id, key = persist_catalog_video("acct-1", src)
    assert video_id
    assert key.startswith("catalog/acct-1/videos/")
    assert key.endswith(".mp4")
    assert (upload_root() / key).is_file()


def test_persist_catalog_video_rejects_jpeg(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)
    src = tmp_path / "shot.jpg"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    try:
        persist_catalog_video("acct-1", src)
    except ValueError as exc:
        assert "MP4" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_lot_video_row_round_trip(tmp_path, monkeypatch) -> None:
    from datetime import datetime, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import Account, Lot, LotVideo, Sale, User
    from app.services.serialization import lot_video_out

    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    video_id, key = persist_catalog_video("acct-1", src)

    engine = create_engine(f"sqlite:///{tmp_path / 'videos.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    db.add(Account(id="acct-1", name="CT"))
    db.add(User(id="u1", account_id="acct-1", email="a@t", password_hash="x", role="owner"))
    db.add(Sale(id="s1", account_id="acct-1", user_id="u1", name="Sale"))
    db.add(Lot(id="lot-1", account_id="acct-1", user_id="u1", sale_id="s1", sku="1", auction_title="Lamp"))
    db.add(
        LotVideo(
            id=video_id,
            account_id="acct-1",
            user_id="u1",
            lot_id="lot-1",
            original_name="clip.mp4",
            storage_path=key,
            content_type="video/mp4",
            byte_size=src.stat().st_size,
            sort_order=0,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    row = db.query(LotVideo).filter(LotVideo.lot_id == "lot-1").one()
    out = lot_video_out(row, lot_id="lot-1")
    assert out.original_name == "clip.mp4"
    assert out.content_type == "video/mp4"
    assert out.file_on_disk is True


def test_ctbids_listing_never_includes_video_rows() -> None:
    photos = [
        SimpleNamespace(id="p", photo_role="hero", original_name="item.jpg"),
        SimpleNamespace(id="v", photo_role=None, original_name="walk.mp4"),
    ]
    kept = photos_for_ctbids_listing(photos)
    assert [p.id for p in kept] == ["p"]
