from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Account, Sale, User
from app.services.ctseller import CTSellerSaleRow
from app.services.ctseller_sale_sync import apply_ctseller_catalog_sale_sync, catalog_sale_name_for_remote


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sync-sales.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _seed(db):
    account = Account(id="acct-s", name="CT")
    user = User(
        id="user-s",
        account_id=account.id,
        email="sync@example.com",
        password_hash="x",
        role="owner",
    )
    db.add_all([account, user])
    db.commit()
    return account, user


def test_catalog_sale_name_for_remote() -> None:
    assert catalog_sale_name_for_remote("  Jones Estate  ", 9) == "Jones Estate"
    assert catalog_sale_name_for_remote("", 9) == "CT Seller #9"


def test_apply_creates_missing_and_skips_unmapped_local(tmp_path) -> None:
    Session = _session(tmp_path)
    db = Session()
    account, user = _seed(db)
    local = Sale(
        id="sale-local",
        account_id=account.id,
        user_id=user.id,
        name="Walkthrough only",
    )
    db.add(local)
    db.commit()
    remote = [
        CTSellerSaleRow(id=101, title="Smith Estate"),
        CTSellerSaleRow(id=102, title="Jones Estate"),
    ]
    stats = apply_ctseller_catalog_sale_sync(
        db, account_id=account.id, user_id=user.id, remote_sales=remote
    )
    db.commit()
    assert stats.created == 2
    assert stats.already_mapped == 0
    rows = db.query(Sale).filter(Sale.account_id == account.id).all()
    names = {r.name for r in rows}
    assert names == {"Walkthrough only", "Smith Estate", "Jones Estate"}
    mapped = {int(r.ctseller_sale_id) for r in rows if r.ctseller_sale_id}
    assert mapped == {101, 102}
    db.close()


def test_apply_renames_open_mapped_not_closed(tmp_path) -> None:
    Session = _session(tmp_path)
    db = Session()
    account, user = _seed(db)
    open_sale = Sale(
        id="sale-open",
        account_id=account.id,
        user_id=user.id,
        name="Old title",
        ctseller_sale_id=55,
    )
    closed = Sale(
        id="sale-closed",
        account_id=account.id,
        user_id=user.id,
        name="Keep closed name",
        ctseller_sale_id=66,
        closed_at=datetime.now(timezone.utc),
    )
    db.add_all([open_sale, closed])
    db.commit()
    stats = apply_ctseller_catalog_sale_sync(
        db,
        account_id=account.id,
        user_id=user.id,
        remote_sales=[
            CTSellerSaleRow(id=55, title="New CT title"),
            CTSellerSaleRow(id=66, title="Should not apply"),
        ],
    )
    db.commit()
    db.refresh(open_sale)
    db.refresh(closed)
    assert stats.created == 0
    assert stats.renamed == 1
    assert stats.already_mapped == 2
    assert open_sale.name == "New CT title"
    assert closed.name == "Keep closed name"
    db.close()


def test_sync_endpoint_creates_from_open_auctions(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.auth import get_current_user
    from app.db import get_db
    from app.main import app
    from app.services.ctseller import seal_secret

    Session = _session(tmp_path)
    db = Session()
    account, user = _seed(db)
    account.ctseller_username = "seller"
    account.ctseller_password_enc = seal_secret("secret")
    db.add(account)
    db.commit()

    class FakeClient:
        def list_open_sales(self):
            return [CTSellerSaleRow(id=47921, title="Latina Estate")]

    monkeypatch.setattr("app.main._ctseller_client_for_account", lambda *a, **k: FakeClient())

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
        res = client.post("/sales/sync-from-ctseller")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["connected"] is True
        assert body["created"] == 1
        assert body["remote_count"] == 1
        assert any(s["name"] == "Latina Estate" and s["ctseller_sale_id"] == 47921 for s in body["sales"])

        again = client.post("/sales/sync-from-ctseller")
        assert again.status_code == 200
        assert again.json()["created"] == 0
        assert again.json()["already_mapped"] == 1
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_sync_endpoint_without_credentials_is_noop(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from app.auth import get_current_user
    from app.db import get_db
    from app.main import app

    Session = _session(tmp_path)
    db = Session()
    _account, user = _seed(db)

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
        res = client.post("/sales/sync-from-ctseller")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["connected"] is False
        assert body["created"] == 0
        assert body["sales"] == []
    finally:
        app.dependency_overrides.clear()
        db.close()
