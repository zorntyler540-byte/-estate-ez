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


def test_looks_like_video_bytes_even_when_named_jpg() -> None:
    from app.catalog_limits import (
        looks_like_video_bytes,
        looks_like_video_file,
        should_store_as_catalog_image,
    )

    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8
    jpeg = b"\xff\xd8\xff\xd9"
    assert looks_like_video_bytes(mp4) is True
    assert looks_like_video_bytes(jpeg) is False
    assert looks_like_video_bytes(b"short") is False
    assert should_store_as_catalog_image(mp4, "mobile-draft-01.jpg") is False
    assert should_store_as_catalog_image(jpeg, "mobile-draft-01.jpg") is True
    assert should_store_as_catalog_image(jpeg, "walk.mp4") is False


def test_looks_like_video_file_sniffs_jpg_named_clip(tmp_path) -> None:
    from app.catalog_limits import looks_like_video_file

    fake = tmp_path / "mobile-draft-01.jpg"
    fake.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    assert looks_like_video_file(fake) is True
    still = tmp_path / "mobile-draft-02.jpg"
    still.write_bytes(b"\xff\xd8\xff\xd9")
    assert looks_like_video_file(still) is False


def test_persist_catalog_photo_rejects_video_named_jpg(tmp_path, monkeypatch) -> None:
    from app.catalog_storage import persist_catalog_photo

    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)
    src = tmp_path / "mobile-draft-01.jpg"
    src.write_bytes(b"\x00\x00\x00\x18ftypisom")
    try:
        persist_catalog_photo("acct-1", src)
    except ValueError as exc:
        assert "still images" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_persist_catalog_video_rejects_jpeg_bytes_named_mp4(tmp_path, monkeypatch) -> None:
    from app.catalog_storage import persist_catalog_video

    monkeypatch.setattr("app.catalog_storage._BACKEND_DIR", tmp_path)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    try:
        persist_catalog_video("acct-1", src)
    except ValueError as exc:
        assert "playable" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_lot_photos_out_hides_video_named_rows() -> None:
    from datetime import datetime, timezone

    from app.services.serialization import lot_photos_out

    photos = [
        SimpleNamespace(
            id="p1",
            lot_id="lot-1",
            original_name="mobile-d-01.jpg",
            created_at=datetime.now(timezone.utc),
            sort_order=0,
            storage_path="catalog/a/p1.jpg",
            photo_role="hero",
            distinct_item_id=None,
            is_primary_front=True,
            shows_whole_lot=True,
            shows_measuring_scale=False,
            label_legibility=None,
            plain_bg_saved=False,
        ),
        SimpleNamespace(
            id="v",
            lot_id="lot-1",
            original_name="walk.mp4",
            created_at=datetime.now(timezone.utc),
            sort_order=1,
            storage_path="catalog/a/v.mp4",
            photo_role=None,
            distinct_item_id=None,
            is_primary_front=False,
            shows_whole_lot=False,
            shows_measuring_scale=False,
            label_legibility=None,
            plain_bg_saved=False,
        ),
    ]
    out = lot_photos_out(photos, lot_id="lot-1")
    assert [row.id for row in out] == ["p1"]


def test_ctbids_listing_never_includes_video_rows() -> None:
    photos = [
        SimpleNamespace(id="p", photo_role="hero", original_name="item.jpg"),
        SimpleNamespace(id="v", photo_role=None, original_name="walk.mp4"),
    ]
    kept = photos_for_ctbids_listing(photos)
    assert [p.id for p in kept] == ["p"]


def test_save_uploaded_files_skips_clip_named_as_jpg(tmp_path, monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from app.upload_storage import save_uploaded_files

    monkeypatch.setattr("app.upload_storage.mirror_local_file_to_r2", lambda *a, **k: None)

    dest = tmp_path / "batch"
    dest.mkdir()

    async def read_clip() -> bytes:
        return b"\x00\x00\x00\x18ftypmp42xxxx"

    async def read_still() -> bytes:
        return b"\xff\xd8\xff\xd9"

    clip = SimpleNamespace(filename="mobile-draft-01.jpg", read=read_clip)
    still = SimpleNamespace(filename="mobile-draft-02.jpg", read=read_still)

    async def _run() -> None:
        await save_uploaded_files(dest, [clip, still])

    asyncio.run(_run())
    names = sorted(p.name for p in dest.iterdir() if p.is_file())
    assert names == ["mobile-draft-02.jpg"]
