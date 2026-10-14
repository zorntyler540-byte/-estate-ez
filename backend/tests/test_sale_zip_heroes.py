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
    ZIP_KIND_ALL,
    ZIP_KIND_HEROES,
    _lot_folder_name,
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
    assert "lot-045-Lamp.jpg" in names
    assert "_lots.csv" in names
    photo_names = [n for n in names if n != "_lots.csv"]
    assert photo_names == ["lot-045-Lamp.jpg"]
    # Full-sale ZIP still uses folders — this export must stay flat one-file-per-lot.
    assert not any("/" in n for n in names)
    index = zipfile.ZipFile(row.zip_path).read("_lots.csv").decode("utf-8-sig")
    assert "045" in index
    assert "Lamp" in index
    assert "lot-045-Lamp.jpg" in index


def test_lot_folder_keeps_full_sku_and_title() -> None:
    used: set[str] = set()
    jewelry = SimpleNamespace(id="aaaaaaaa", sku="JZ5137AD", auction_title="Liz & Co Heart Necklace")
    numeric = SimpleNamespace(id="bbbbbbbb", sku="045", auction_title="Table Lamp")
    clone = SimpleNamespace(id="cccccccc", sku="CLONEFH745", auction_title="Clone Wars figure")
    assert _lot_folder_name(jewelry, used) == "JZ5137AD-Liz-Co-Heart-Necklace"
    assert _lot_folder_name(numeric, used) == "lot-045-Table-Lamp"
    assert _lot_folder_name(clone, used) == "CLONEFH745-Clone-Wars-figure"
    # Same SKU+title in one sale gets a numeric suffix, not a digit-stripped lot-5137.
    dup = SimpleNamespace(id="dddddddd", sku="JZ5137AD", auction_title="Liz & Co Heart Necklace")
    assert _lot_folder_name(dup, used) == "JZ5137AD-Liz-Co-Heart-Necklace-2"


def test_photos_zip_uses_sku_title_folders(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'zip.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr("app.services.sale_zip_export.export_dir_for_account", lambda _aid: export_dir)

    hero = tmp_path / "hero.jpg"
    extra = tmp_path / "extra.jpg"
    _tiny_jpeg(hero)
    _tiny_jpeg(extra)

    db.add(
        Lot(
            id="lot-1",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            sku="JZ5137AD",
            auction_title="Liz & Co Heart Necklace",
        )
    )
    db.add_all(
        [
            Photo(
                id="p-hero",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-1",
                original_name="front.jpg",
                storage_path=str(hero),
                sort_order=0,
                photo_role="hero",
            ),
            Photo(
                id="p-extra",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-1",
                original_name="back.jpg",
                storage_path=str(extra),
                sort_order=1,
                photo_role="item_front",
            ),
        ]
    )
    row = SalePhotoZipExport(
        id="ex-all",
        account_id="acct-1",
        user_id="user-1",
        sale_id="sale-1",
        status=JobStatus.queued,
        kind=ZIP_KIND_ALL,
        total_photos_expected=2,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()

    run_sale_photo_zip_export(db, "ex-all")
    db.refresh(row)
    assert row.status == JobStatus.completed
    assert row.photo_files_written == 2
    names = zipfile.ZipFile(row.zip_path).namelist()
    folder = "JZ5137AD-Liz-Co-Heart-Necklace"
    assert f"{folder}/001_front.jpg" in names
    assert f"{folder}/002_back.jpg" in names
    assert "_lots.csv" in names
    assert not any(n.startswith("lot-5137") for n in names)
    index = zipfile.ZipFile(row.zip_path).read("_lots.csv").decode("utf-8-sig")
    assert "JZ5137AD" in index
    assert folder in index


def test_heroes_zip_can_filter_to_one_ctbids_sale(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'zip.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr("app.services.sale_zip_export.export_dir_for_account", lambda _aid: export_dir)

    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    _tiny_jpeg(a)
    _tiny_jpeg(b)

    db.add_all(
        [
            Lot(
                id="lot-a",
                account_id="acct-1",
                user_id="user-1",
                sale_id="sale-1",
                sku="TG415SK",
                auction_title="Gold necklace",
                ctseller_item_id=11,
                ctseller_sale_id=100,
            ),
            Lot(
                id="lot-b",
                account_id="acct-1",
                user_id="user-1",
                sale_id="sale-1",
                sku="MS322AD",
                auction_title="Hallmark ornaments",
                ctseller_item_id=22,
                ctseller_sale_id=200,
            ),
            Photo(
                id="p-a",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-a",
                original_name="a.jpg",
                storage_path=str(a),
                sort_order=0,
                photo_role="hero",
            ),
            Photo(
                id="p-b",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-b",
                original_name="b.jpg",
                storage_path=str(b),
                sort_order=0,
                photo_role="hero",
            ),
        ]
    )
    row = SalePhotoZipExport(
        id="ex-ctbids",
        account_id="acct-1",
        user_id="user-1",
        sale_id="sale-1",
        status=JobStatus.queued,
        kind=ZIP_KIND_HEROES,
        filter_ctseller_sale_id=100,
        total_photos_expected=1,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()

    run_sale_photo_zip_export(db, "ex-ctbids")
    db.refresh(row)
    assert row.status == JobStatus.completed
    names = [n for n in zipfile.ZipFile(row.zip_path).namelist() if n != "_lots.csv"]
    assert names == ["TG415SK-Gold-necklace.jpg"]
    assert not any("MS322AD" in n for n in names)


def test_all_photos_zip_can_filter_to_one_ctbids_sale(tmp_path: Path, monkeypatch) -> None:
    """Full photo ZIP for one CT Bids auction keeps every frame of those lots only."""
    engine = create_engine(f"sqlite:///{tmp_path / 'zip.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr("app.services.sale_zip_export.export_dir_for_account", lambda _aid: export_dir)

    front = tmp_path / "front.jpg"
    detail = tmp_path / "detail.jpg"
    other = tmp_path / "other.jpg"
    _tiny_jpeg(front)
    _tiny_jpeg(detail)
    _tiny_jpeg(other)

    db.add_all(
        [
            Lot(
                id="lot-a",
                account_id="acct-1",
                user_id="user-1",
                sale_id="sale-1",
                sku="TG415SK",
                auction_title="Gold necklace",
                ctseller_item_id=11,
                ctseller_sale_id=100,
            ),
            Lot(
                id="lot-b",
                account_id="acct-1",
                user_id="user-1",
                sale_id="sale-1",
                sku="MS322AD",
                auction_title="Hallmark ornaments",
                ctseller_item_id=22,
                ctseller_sale_id=200,
            ),
            Photo(
                id="p-front",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-a",
                original_name="front.jpg",
                storage_path=str(front),
                sort_order=0,
                photo_role="hero",
            ),
            Photo(
                id="p-detail",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-a",
                original_name="detail.jpg",
                storage_path=str(detail),
                sort_order=1,
                photo_role="item_front",
            ),
            Photo(
                id="p-other",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-b",
                original_name="other.jpg",
                storage_path=str(other),
                sort_order=0,
                photo_role="hero",
            ),
        ]
    )
    row = SalePhotoZipExport(
        id="ex-all-ctbids",
        account_id="acct-1",
        user_id="user-1",
        sale_id="sale-1",
        status=JobStatus.queued,
        kind=ZIP_KIND_ALL,
        filter_ctseller_sale_id=100,
        total_photos_expected=2,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()

    run_sale_photo_zip_export(db, "ex-all-ctbids")
    db.refresh(row)
    assert row.status == JobStatus.completed
    assert row.photo_files_written == 2
    names = [n for n in zipfile.ZipFile(row.zip_path).namelist() if n != "_lots.csv"]
    folder = "TG415SK-Gold-necklace"
    assert names == [f"{folder}/001_front.jpg", f"{folder}/002_detail.jpg"]
    assert not any("MS322AD" in n for n in names)
    index = zipfile.ZipFile(row.zip_path).read("_lots.csv").decode("utf-8-sig")
    assert "TG415SK" in index
    assert "MS322AD" not in index


def test_heroes_zip_across_website_sales_for_one_ctbids_auction(tmp_path: Path, monkeypatch) -> None:
    """One CT Seller auction can hold lots that live in different Estate-EZ catalogs."""
    engine = create_engine(f"sqlite:///{tmp_path / 'zip.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr("app.services.sale_zip_export.export_dir_for_account", lambda _aid: export_dir)

    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    other_auction = tmp_path / "c.jpg"
    _tiny_jpeg(a)
    _tiny_jpeg(b)
    _tiny_jpeg(other_auction)

    db.add_all(
        [
            Lot(
                id="lot-a",
                account_id="acct-1",
                user_id="user-1",
                sale_id="sale-theresa",
                sku="TG415SK",
                auction_title="Gold necklace",
                ctseller_item_id=11,
                ctseller_sale_id=100,
            ),
            Lot(
                id="lot-b",
                account_id="acct-1",
                user_id="user-1",
                sale_id="sale-primitive",
                sku="MS322AD",
                auction_title="Hallmark ornaments",
                ctseller_item_id=22,
                ctseller_sale_id=100,
            ),
            Lot(
                id="lot-c",
                account_id="acct-1",
                user_id="user-1",
                sale_id="sale-primitive",
                sku="ZZ999ZZ",
                auction_title="Other auction",
                ctseller_item_id=33,
                ctseller_sale_id=200,
            ),
            Photo(
                id="p-a",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-a",
                original_name="a.jpg",
                storage_path=str(a),
                sort_order=0,
                photo_role="hero",
            ),
            Photo(
                id="p-b",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-b",
                original_name="b.jpg",
                storage_path=str(b),
                sort_order=0,
                photo_role="hero",
            ),
            Photo(
                id="p-c",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-c",
                original_name="c.jpg",
                storage_path=str(other_auction),
                sort_order=0,
                photo_role="hero",
            ),
        ]
    )
    row = SalePhotoZipExport(
        id="ex-across",
        account_id="acct-1",
        user_id="user-1",
        sale_id="sale-theresa",
        status=JobStatus.queued,
        kind=ZIP_KIND_HEROES,
        filter_ctseller_sale_id=100,
        across_sales=True,
        total_photos_expected=2,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()

    run_sale_photo_zip_export(db, "ex-across")
    db.refresh(row)
    assert row.status == JobStatus.completed
    assert row.photo_files_written == 2
    names = sorted(n for n in zipfile.ZipFile(row.zip_path).namelist() if n != "_lots.csv")
    assert names == ["MS322AD-Hallmark-ornaments.jpg", "TG415SK-Gold-necklace.jpg"]

