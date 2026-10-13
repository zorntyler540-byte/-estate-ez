"""Client logins upload photos only into the sale the owner assigned."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.auth as auth_mod
import app.main as main_mod
from app.db import Base, get_db
from app.main import app


def _jpeg() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (180, 40, 20)).save(buf, format="JPEG")
    return buf.getvalue()


def test_client_login_is_locked_to_one_sale(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'clients.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    # /jobs/upload authenticates with SessionLocal, not the get_db dependency.
    monkeypatch.setattr(auth_mod, "SessionLocal", Session)
    monkeypatch.setattr(main_mod, "SessionLocal", Session)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        reg = client.post(
            "/auth/register",
            json={"account_name": "CT Las Vegas", "email": "owner-client@example.com", "password": "owner-pass-1"},
        )
        assert reg.status_code == 200, reg.text
        owner_token = reg.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}

        sale_a = client.post("/sales", headers=owner_headers, json={"name": "Spring estate"})
        sale_b = client.post("/sales", headers=owner_headers, json={"name": "Other house"})
        assert sale_a.status_code == 201, sale_a.text
        assert sale_b.status_code == 201, sale_b.text
        sale_a_id = sale_a.json()["id"]
        sale_b_id = sale_b.json()["id"]

        missing_sale = client.post(
            "/team/members",
            headers=owner_headers,
            json={"email": "noclient@example.com", "password": "client-pass-1", "role": "client"},
        )
        assert missing_sale.status_code == 400, missing_sale.text

        created = client.post(
            "/team/members",
            headers=owner_headers,
            json={
                "email": "client@example.com",
                "password": "client-pass-1",
                "role": "client",
                "sale_id": sale_a_id,
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["role"] == "client"
        assert body["assigned_sale_id"] == sale_a_id
        assert body["assigned_sale_name"] == "Spring estate"
        member_id = body["id"]

        login = client.post("/auth/login", json={"email": "client@example.com", "password": "client-pass-1"})
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        me = client.get("/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        assert me.json()["role"] == "client"
        assert me.json()["assigned_sale_id"] == sale_a_id
        assert me.json()["assigned_sale_name"] == "Spring estate"

        sales = client.get("/sales", headers=headers)
        assert sales.status_code == 200, sales.text
        assert [row["id"] for row in sales.json()] == [sale_a_id]

        assert client.post("/sales", headers=headers, json={"name": "Nope"}).status_code == 403
        assert client.get("/lots", headers=headers).status_code == 403
        assert client.get("/team/members", headers=headers).status_code == 403
        assert client.post("/sales/sync-from-ctseller", headers=headers).status_code == 403

        other = client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_b_id, "single_lot_mode": "true"},
            files={"files": ("lot.jpg", _jpeg(), "image/jpeg")},
        )
        assert other.status_code == 403, other.text

        uploaded = client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_a_id, "single_lot_mode": "true", "single_lot_manual_sku": "CL1"},
            files={"files": ("lot.jpg", _jpeg(), "image/jpeg")},
        )
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["sale_id"] == sale_a_id
        assert uploaded.json()["status"] == "receiving"

        jobs = client.get("/jobs", headers=headers)
        assert jobs.status_code == 200, jobs.text
        assert len(jobs.json()) == 1
        assert jobs.json()[0]["sale_id"] == sale_a_id
        assert client.get(f"/jobs?sale_id={sale_b_id}", headers=headers).status_code == 403

        moved = client.patch(
            f"/team/members/{member_id}/sale",
            headers=owner_headers,
            json={"sale_id": sale_b_id},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["assigned_sale_id"] == sale_b_id

        # The existing token still carries role=client; the sale lock is read from the user row.
        sales_after = client.get("/sales", headers=headers)
        assert sales_after.status_code == 200, sales_after.text
        assert [row["id"] for row in sales_after.json()] == [sale_b_id]
        blocked = client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_a_id, "single_lot_mode": "true"},
            files={"files": ("lot.jpg", _jpeg(), "image/jpeg")},
        )
        assert blocked.status_code == 403, blocked.text
    finally:
        app.dependency_overrides.clear()
