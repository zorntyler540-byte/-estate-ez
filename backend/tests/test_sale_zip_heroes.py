from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import JobStatus, Lot, Photo, SalePhotoZipExport
from app.services.sale_zip_export import (
    ZIP_KIND_HEROES,
    pick_lot_hero_photo,
    run_sale_photo_zip_export,
)


def test_pick_hero_prefers_hero_role_over_tape_cover() -> None:
    tape = SimpleNamespace(photo_role="tape", sort_order=0, original_name="tape.jpg")
    front = SimpleNamespace(photo_role="item_front", sort_order=1, original_name="item.jpg")
    hero = SimpleNamespace(photo_role="hero", sort_order=2, original_name="group.jpg")
    assert pick_lot_hero_photo([tape, front, hero]) is hero


def test_pick_hero_skips_sku_tape_when_no_hero_role() -> None:
    tape = SimpleNamespace(photo_role="tape", sort_order=0, original_name="tape.jpg")
    front = SimpleNamespace(photo_role="item_front", sort_order=1, original_name="item.jpg")
    assert pick_lot_hero_photo([tape, front]) is front


def test_pick_hero_skips_unusable_when_better_frame_exists() -> None:
    unusable = SimpleNamespace(photo_role="unusable", sort_order=0, original_name="blur.jpg")
    front = SimpleNamespace(photo_role="item_front", sort_order=1, original_name="item.jpg")
    assert pick_lot_hero_photo([unusable, front]) is front


def test_pick_hero_empty() -> None:
    assert pick_lot_hero_photo([]) is None


def _tiny_jpeg(path: Path) -> None:
    # Minimal JPEG SOI/EOI so the zip writer has a real file.
    path.write_bytes(b"\xff\xd8\xff\xd9")


def test_heroes_zip_is_flat_sku_named_not_tape(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'zip.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr("app.services.sale_zip_export.export_dir_for_account", lambda _aid: export_dir)

    tape = tmp_path / "tape.jpg"
    hero = tmp_path / "hero.jpg"
    extra = tmp_path / "extra.jpg"
    _tiny_jpeg(tape)
    _tiny_jpeg(hero)
    _tiny_jpeg(extra)

    db.add(
        Lot(
            id="lot-1",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            sku="045",
            auction_title="Lamp",
        )
    )
    db.add_all(
        [
            Photo(
                id="p-tape",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-1",
                original_name="mobile-draft-01.jpg",
                storage_path=str(tape),
                sort_order=0,
                photo_role="tape",
            ),
            Photo(
                id="p-hero",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-1",
                original_name="mobile-draft-02.jpg",
                storage_path=str(hero),
                sort_order=1,
                photo_role="hero",
            ),
            Photo(
                id="p-extra",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-1",
                original_name="mobile-draft-03.jpg",
                storage_path=str(extra),
                sort_order=2,
                photo_role="item_front",
            ),
        ]
    )
    row = SalePhotoZipExport(
        id="ex-1",
        account_id="acct-1",
        user_id="user-1",
        sale_id="sale-1",
        status=JobStatus.queued,
        kind=ZIP_KIND_HEROES,
        total_photos_expected=1,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()

    run_sale_photo_zip_export(db, "ex-1")
    db.refresh(row)
    assert row.status == JobStatus.completed
    assert row.photo_files_written == 1
    names = zipfile.ZipFile(row.zip_path).namelist()
    assert names == ["lot-045.jpg"]
    # Full-sale ZIP still uses folders — this export must stay flat one-file-per-lot.
    assert not any("/" in n for n in names)
