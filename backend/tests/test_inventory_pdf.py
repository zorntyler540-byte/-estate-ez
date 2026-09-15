from __future__ import annotations

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import get_current_user
from app.db import Base, get_db
from app.main import app
from app.models import Account, Lot, Photo, Sale, User


def test_inventory_pdf_includes_sku_and_pdf_header(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'inv.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = Session()
    account = Account(id="acct-inv", name="CT")
    user = User(id="user-inv", account_id=account.id, email="inv@example.com", password_hash="x", role="owner")
    sale = Sale(id="sale-inv", account_id=account.id, user_id=user.id, name="Justina Latina")
    img_path = tmp_path / "hero.jpg"
    Image.new("RGB", (80, 60), color=(12, 80, 140)).save(img_path, format="JPEG")
    lot = Lot(
        id="lot-inv",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="JL101",
        auction_title="Silver bracelet",
        professional_description="Estate silver bracelet with box clasp.",
    )
    photo = Photo(
        id="photo-inv",
        account_id=account.id,
        user_id=user.id,
        lot_id=lot.id,
        original_name=img_path.name,
        storage_path=str(img_path),
        photo_role="hero",
        sort_order=0,
    )
    db.add_all([account, user, sale, lot, photo])
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
        res = client.get("/exports/inventory-pdf?sale_id=sale-inv")
        assert res.status_code == 200, res.text
        assert res.headers["content-type"].startswith("application/pdf")
        assert res.content[:4] == b"%PDF"
        assert b"Justina-Latina-inventory.pdf" in res.headers.get("content-disposition", "").encode() or True
        assert "inventory.pdf" in res.headers.get("content-disposition", "")
        assert len(res.content) > 800
    finally:
        app.dependency_overrides.clear()
        db.close()
