from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Account, JobStatus, Lot, Photo, ProcessingJob, Sale, User
from app.services.sale_merge import SaleMergeError, merge_catalog_sales

UTC = timezone.utc


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sale-merge.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def _account(db) -> Account:
    row = Account(id="acct-a", name="CT LV")
    db.add(row)
    return row


def _user(db, *, user_id: str = "owner-a") -> User:
    row = User(
        id=user_id,
        account_id="acct-a",
        email="owner@caringtransitions.com",
        password_hash="x",
        role="owner",
    )
    db.add(row)
    return row


def _sale(db, *, sale_id: str, name: str, user_id: str = "owner-a") -> Sale:
    row = Sale(id=sale_id, account_id="acct-a", user_id=user_id, name=name)
    db.add(row)
    return row


def test_merge_moves_lots_and_jobs_then_deletes_source(tmp_path: Path) -> None:
    db = _session(tmp_path)
    _account(db)
    owner = _user(db)
    keep = _sale(db, sale_id="sale-keep", name="Keeper")
    drop = _sale(db, sale_id="sale-drop", name="Extra")
    db.flush()
    db.add(
        Lot(
            id="lot-a",
            account_id="acct-a",
            user_id=owner.id,
            sale_id=drop.id,
            sku="101",
            auction_title="Lamp",
        )
    )
    db.add(
        Photo(
            id="p-a",
            account_id="acct-a",
            user_id=owner.id,
            lot_id="lot-a",
            original_name="a.jpg",
            storage_path="/a.jpg",
        )
    )
    db.add(
        ProcessingJob(
            id="job-done",
            account_id="acct-a",
            user_id=owner.id,
            sale_id=drop.id,
            status=JobStatus.completed,
            input_folder="/tmp/in",
        )
    )
    db.commit()

    result = merge_catalog_sales(
        db, account_id="acct-a", target_sale_id=keep.id, source_sale_id=drop.id
    )
    assert result.lots_moved == 1
    assert result.jobs_moved == 1
    assert result.source_sale_name == "Extra"
    assert result.duplicate_sku_count == 0

    db.expire_all()
    assert db.query(Sale).filter(Sale.id == "sale-drop").first() is None
    lot = db.query(Lot).filter(Lot.id == "lot-a").one()
    assert lot.sale_id == "sale-keep"
    job = db.query(ProcessingJob).filter(ProcessingJob.id == "job-done").one()
    assert job.sale_id == "sale-keep"
    photo = db.query(Photo).filter(Photo.id == "p-a").one()
    assert photo.lot_id == "lot-a"


def test_merge_flags_duplicate_skus(tmp_path: Path) -> None:
    db = _session(tmp_path)
    _account(db)
    owner = _user(db)
    keep = _sale(db, sale_id="sale-keep", name="Keeper")
    drop = _sale(db, sale_id="sale-drop", name="Extra")
    db.flush()
    db.add_all(
        [
            Lot(
                id="lot-keep",
                account_id="acct-a",
                user_id=owner.id,
                sale_id=keep.id,
                sku="55",
                auction_title="A",
            ),
            Lot(
                id="lot-drop",
                account_id="acct-a",
                user_id=owner.id,
                sale_id=drop.id,
                sku="55",
                auction_title="B",
            ),
        ]
    )
    db.commit()
    result = merge_catalog_sales(
        db, account_id="acct-a", target_sale_id=keep.id, source_sale_id=drop.id
    )
    assert result.duplicate_sku_count == 2
    db.expire_all()
    flags = {row.id: row.sku_duplicate_in_sale for row in db.query(Lot).all()}
    assert flags["lot-keep"] is True
    assert flags["lot-drop"] is True


def test_merge_keeps_target_ctseller_mapping_and_copies_empty_covers(tmp_path: Path) -> None:
    db = _session(tmp_path)
    _account(db)
    owner = _user(db)
    keep = _sale(db, sale_id="sale-keep", name="Keeper")
    drop = _sale(db, sale_id="sale-drop", name="Extra")
    keep.ctseller_sale_id = 99
    drop.ctseller_sale_id = 77
    drop.ctseller_cover_photo_ids = '["photo-cover"]'
    db.flush()
    db.add(
        Lot(
            id="lot-drop",
            account_id="acct-a",
            user_id=owner.id,
            sale_id=drop.id,
            sku="12",
            auction_title="Chair",
        )
    )
    db.commit()

    merge_catalog_sales(db, account_id="acct-a", target_sale_id=keep.id, source_sale_id=drop.id)
    db.expire_all()
    keeper = db.query(Sale).filter(Sale.id == "sale-keep").one()
    assert keeper.ctseller_sale_id == 99
    assert keeper.ctseller_cover_photo_ids == '["photo-cover"]'
    assert db.query(Sale).filter(Sale.id == "sale-drop").first() is None


def test_merge_rejects_self_and_missing(tmp_path: Path) -> None:
    db = _session(tmp_path)
    _account(db)
    _user(db)
    keep = _sale(db, sale_id="sale-keep", name="Keeper")
    db.commit()
    with pytest.raises(SaleMergeError, match="itself"):
        merge_catalog_sales(
            db, account_id="acct-a", target_sale_id=keep.id, source_sale_id=keep.id
        )
    with pytest.raises(SaleMergeError, match="not found"):
        merge_catalog_sales(
            db, account_id="acct-a", target_sale_id=keep.id, source_sale_id="nope"
        )


def test_merge_rejects_active_catalog_job(tmp_path: Path) -> None:
    db = _session(tmp_path)
    _account(db)
    owner = _user(db)
    keep = _sale(db, sale_id="sale-keep", name="Keeper")
    drop = _sale(db, sale_id="sale-drop", name="Extra")
    db.flush()
    db.add(
        ProcessingJob(
            id="job-busy",
            account_id="acct-a",
            user_id=owner.id,
            sale_id=drop.id,
            status=JobStatus.processing,
            input_folder="/tmp/in",
        )
    )
    db.commit()
    with pytest.raises(SaleMergeError, match="finished"):
        merge_catalog_sales(
            db, account_id="acct-a", target_sale_id=keep.id, source_sale_id=drop.id
        )
    db.expire_all()
    assert db.query(Sale).filter(Sale.id == drop.id).one() is not None


def test_merge_rejects_closed_sale(tmp_path: Path) -> None:
    db = _session(tmp_path)
    _account(db)
    _user(db)
    keep = _sale(db, sale_id="sale-keep", name="Keeper")
    drop = _sale(db, sale_id="sale-drop", name="Extra")
    drop.closed_at = datetime(2026, 9, 1, tzinfo=UTC)
    db.commit()
    with pytest.raises(SaleMergeError, match="open"):
        merge_catalog_sales(
            db, account_id="acct-a", target_sale_id=keep.id, source_sale_id=drop.id
        )


def test_merge_endpoint_rejects_catalogger() -> None:
    from app.main import merge_sales
    from app.schemas import SaleMergeIn

    with pytest.raises(HTTPException) as ei:
        merge_sales(
            target_sale_id="sale-keep",
            payload=SaleMergeIn(source_sale_id="sale-drop"),
            current=SimpleNamespace(role="catalogger", account_id="acct-a", id="c1"),
            db=None,
        )
    assert ei.value.status_code == 403
