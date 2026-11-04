from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import JobStatus, Lot, LotVideo, Photo, ProcessingJob
from app.services.mobile_drafts import (
    cataloged_mobile_draft_ids,
    cataloged_mobile_draft_lots,
    mobile_draft_id_from_original_name,
)


def test_extracts_uuid_draft_id() -> None:
    name = "mobile-1c86c230-a1c1-4433-b64e-5c2957e79027-03.jpg"
    assert mobile_draft_id_from_original_name(name) == "1c86c230-a1c1-4433-b64e-5c2957e79027"


def test_extracts_uppercase_and_png() -> None:
    name = "MOBILE-1C86C230-A1C1-4433-B64E-5C2957E79027-11.PNG"
    assert mobile_draft_id_from_original_name(name) == "1c86c230-a1c1-4433-b64e-5c2957e79027"


def test_rejects_non_mobile_names() -> None:
    assert mobile_draft_id_from_original_name("1C86C230-A1C1-4433-B64E-5C2957E79027.jpg") is None
    assert mobile_draft_id_from_original_name("hero.jpg") is None
    assert mobile_draft_id_from_original_name(None) is None


def test_extracts_video_upload_name() -> None:
    name = "mobile-1c86c230-a1c1-4433-b64e-5c2957e79027-vid-01.mp4"
    assert mobile_draft_id_from_original_name(name) == "1c86c230-a1c1-4433-b64e-5c2957e79027"


def test_cataloged_mobile_draft_ids_dedupes_photos(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'drafts.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    draft_a = "1c86c230-a1c1-4433-b64e-5c2957e79027"
    draft_b = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    db.add(
        Lot(
            id="lot-1",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            auction_title="Lamp",
            sku="LV101",
            photo_count=3,
        )
    )
    db.add(
        Lot(
            id="lot-other",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-other",
            auction_title="Other sale",
        )
    )
    db.add_all(
        [
            Photo(
                id="p1",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-1",
                original_name=f"mobile-{draft_a}-01.jpg",
                storage_path="/catalog/a1.jpg",
                sort_order=0,
            ),
            Photo(
                id="p2",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-1",
                original_name=f"mobile-{draft_a}-02.jpg",
                storage_path="/catalog/a2.jpg",
                sort_order=1,
            ),
            Photo(
                id="p3",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-1",
                original_name="hero.jpg",
                storage_path="/catalog/hero.jpg",
                sort_order=2,
            ),
            Photo(
                id="p4",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-other",
                original_name=f"mobile-{draft_b}-01.jpg",
                storage_path="/catalog/b1.jpg",
                sort_order=0,
            ),
        ]
    )
    db.commit()

    assert cataloged_mobile_draft_ids(db, "acct-1", "sale-1") == [draft_a]
    assert cataloged_mobile_draft_ids(db, "acct-1", "sale-other") == [draft_b]
    assert cataloged_mobile_draft_ids(db, "acct-other", "sale-1") == []

    lots = cataloged_mobile_draft_lots(db, "acct-1", "sale-1")
    assert len(lots) == 1
    assert lots[0].draft_id == draft_a
    assert lots[0].lot_id == "lot-1"
    assert lots[0].sku == "LV101"
    assert lots[0].photo_count == 3
    assert lots[0].auction_title == "Lamp"


def test_cataloged_drafts_map_video_name_without_sku(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'video-map.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    draft = "1c86c230-a1c1-4433-b64e-5c2957e79027"
    db.add(
        Lot(
            id="lot-vid",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            auction_title="Mixer",
            sku=None,
            photo_count=4,
        )
    )
    db.add(
        LotVideo(
            id="v1",
            account_id="acct-1",
            user_id="user-1",
            lot_id="lot-vid",
            original_name=f"mobile-{draft}-vid-01.mp4",
            storage_path="/videos/v1.mp4",
            content_type="video/mp4",
            byte_size=12,
            sort_order=0,
        )
    )
    when = datetime.now(timezone.utc) - timedelta(minutes=3)
    db.add(
        ProcessingJob(
            id="job-vid",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            status=JobStatus.completed,
            input_folder="/tmp/batch",
            single_lot_mode=True,
            client_draft_id=draft,
            created_at=when,
            updated_at=when,
        )
    )
    db.commit()
    lots = cataloged_mobile_draft_lots(db, "acct-1", "sale-1")
    assert len(lots) == 1
    assert lots[0].draft_id == draft
    assert lots[0].lot_id == "lot-vid"


def test_cataloged_drafts_single_lot_without_sku_or_mobile_name(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'nosku.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    draft = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    job_at = datetime.now(timezone.utc) - timedelta(minutes=4)
    lot_at = job_at + timedelta(minutes=1)
    db.add(
        Lot(
            id="lot-old",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            auction_title="Older lot",
            sku="OLD1",
            created_at=job_at - timedelta(days=2),
        )
    )
    db.add(
        Lot(
            id="lot-new",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            auction_title="No SKU mixer",
            sku=None,
            photo_count=4,
            created_at=lot_at,
        )
    )
    db.add(
        Photo(
            id="p-new",
            account_id="acct-1",
            user_id="user-1",
            lot_id="lot-new",
            original_name="ImagePicker_123.jpg",
            storage_path="/catalog/x.jpg",
            sort_order=0,
        )
    )
    db.add(
        ProcessingJob(
            id="job-new",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            status=JobStatus.completed,
            input_folder="/tmp/batch",
            single_lot_mode=True,
            client_draft_id=draft,
            created_at=job_at,
            updated_at=lot_at,
        )
    )
    db.commit()
    lots = cataloged_mobile_draft_lots(db, "acct-1", "sale-1")
    assert [(row.draft_id, row.lot_id) for row in lots] == [(draft, "lot-new")]
