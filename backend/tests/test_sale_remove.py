from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import get_current_user
from app.db import Base, get_db
from app.main import app
from app.models import Account, JobStatus, Lot, Photo, ProcessingJob, Sale, User


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sale-remove.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _seed(db, *, role: str = "owner"):
    account = Account(id="acct-rm", name="CT")
    user = User(
        id="user-rm",
        account_id=account.id,
        email=f"{role}@example.com",
        password_hash="x",
        role=role,
    )
    sale = Sale(
        id="sale-old",
        account_id=account.id,
        user_id=user.id,
        name="Magelda 3",
    )
    db.add_all([account, user, sale])
    db.commit()
    return account, user, sale


def test_remove_sale_requires_matching_name(tmp_path: Path) -> None:
    Session = _session(tmp_path)
    db = Session()
    account, user, sale = _seed(db)
    db.add(
        Lot(
            id="lot-keep",
            account_id=account.id,
            user_id=user.id,
            sale_id=sale.id,
            sku="101",
            auction_title="Lamp",
        )
    )
    db.commit()

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    try:
        bad = client.post("/sales/sale-old/remove", json={"confirm_name": "Wrong"})
        assert bad.status_code == 400, bad.text
        assert db.query(Lot).count() == 1
        assert db.query(Sale).filter(Sale.id == "sale-old").first() is not None
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_remove_sale_purges_lots_and_drops_tab(tmp_path: Path) -> None:
    Session = _session(tmp_path)
    db = Session()
    account, user, sale = _seed(db)
    keep = Sale(id="sale-keep", account_id=account.id, user_id=user.id, name="Justina Latina")
    db.add(keep)
    db.add(
        Lot(
            id="lot-gone",
            account_id=account.id,
            user_id=user.id,
            sale_id=sale.id,
            sku="202",
            auction_title="Clock",
        )
    )
    db.add(
        Photo(
            id="ph-gone",
            account_id=account.id,
            user_id=user.id,
            lot_id="lot-gone",
            original_name="c.jpg",
            storage_path="/c.jpg",
        )
    )
    db.add(
        Lot(
            id="lot-stay",
            account_id=account.id,
            user_id=user.id,
            sale_id=keep.id,
            sku="303",
            auction_title="Vase",
        )
    )
    db.add(
        ProcessingJob(
            id="job-done",
            account_id=account.id,
            user_id=user.id,
            sale_id=sale.id,
            status=JobStatus.completed,
            input_folder="/tmp/x",
        )
    )
    db.commit()

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    try:
        res = client.post("/sales/sale-old/remove", json={"confirm_name": "Magelda 3"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["deleted_sale_name"] == "Magelda 3"
        assert body["lots_deleted"] == 1
        inner = Session()
        try:
            assert inner.query(Sale).filter(Sale.id == "sale-old").first() is None
            assert inner.query(Lot).filter(Lot.id == "lot-gone").first() is None
            assert inner.query(Photo).filter(Photo.id == "ph-gone").first() is None
            assert inner.query(Lot).filter(Lot.id == "lot-stay").first() is not None
            assert inner.query(Sale).filter(Sale.id == "sale-keep").first() is not None
        finally:
            inner.close()
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_list_sales_includes_lot_and_job_counts(tmp_path: Path) -> None:
    Session = _session(tmp_path)
    db = Session()
    account, user, sale = _seed(db)
    db.add(
        Lot(
            id="lot-count",
            account_id=account.id,
            user_id=user.id,
            sale_id=sale.id,
            sku="12",
            auction_title="Chair",
        )
    )
    db.add(
        ProcessingJob(
            id="job-count",
            account_id=account.id,
            user_id=user.id,
            sale_id=sale.id,
            status=JobStatus.queued,
            input_folder="batch",
        )
    )
    db.commit()

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    try:
        res = client.get("/sales")
        assert res.status_code == 200, res.text
        row = next(item for item in res.json() if item["id"] == "sale-old")
        assert row["lot_count"] == 1
        assert row["job_count"] == 1
        assert row["active_job_count"] == 1
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_remove_sale_owner_only(tmp_path: Path) -> None:
    Session = _session(tmp_path)
    db = Session()
    _account, user, _sale = _seed(db, role="catalogger")

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    try:
        res = client.post("/sales/sale-old/remove", json={"confirm_name": "Magelda 3"})
        assert res.status_code == 403
        assert db.query(Sale).filter(Sale.id == "sale-old").first() is not None
    finally:
        app.dependency_overrides.clear()
        db.close()
