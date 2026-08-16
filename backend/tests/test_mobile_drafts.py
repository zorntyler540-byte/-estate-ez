from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Lot, Photo
from app.services.mobile_drafts import cataloged_mobile_draft_ids, mobile_draft_id_from_original_name


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
