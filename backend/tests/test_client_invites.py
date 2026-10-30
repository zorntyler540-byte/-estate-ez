"""Client invite links: one sale, hashed token, expiry, revocation, and 403s."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from app.models import ClientInvite, User
from app.services.client_invites import hash_invite_token


def _jpeg() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (20, 80, 140)).save(buf, format="JPEG")
    return buf.getvalue()


def test_client_invite_is_sale_scoped_and_blocks_other_routes(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'invites.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
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
            json={"account_name": "CT Reno", "email": "owner-invite@example.com", "password": "owner-pass-1"},
        )
        assert reg.status_code == 200, reg.text
        owner_headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
        sale_a = client.post("/sales", headers=owner_headers, json={"name": "Oak Street"})
        sale_b = client.post("/sales", headers=owner_headers, json={"name": "Other house"})
        assert sale_a.status_code == 201 and sale_b.status_code == 201
        sale_a_id = sale_a.json()["id"]
        sale_b_id = sale_b.json()["id"]

        created = client.post(
            f"/sales/{sale_a_id}/client-invites",
            headers=owner_headers,
            json={"email": "family@example.com"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        raw = body["token"]
        app_password = body["app_password"]
        assert body["invite_path"] == f"/client-invite?token={raw}"
        signed_in = client.post(
            "/auth/login",
            json={"email": "family@example.com", "password": app_password},
        )
        assert signed_in.status_code == 200, signed_in.text
        phone = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {signed_in.json()['access_token']}"},
        )
        assert phone.status_code == 200, phone.text
        assert phone.json()["assigned_sale_id"] == sale_a_id
        assert phone.json()["role"] == "client"
        assert raw not in created.text.replace(body["invite_path"], "").replace(f'"token":"{raw}"', "")

        db = Session()
        try:
            stored = db.query(ClientInvite).filter(ClientInvite.id == body["id"]).one()
            assert stored.token_hash == hash_invite_token(raw)
            assert raw not in stored.token_hash
        finally:
            db.close()

        redeemed = client.post("/auth/client-invite/redeem", json={"token": raw})
        assert redeemed.status_code == 200, redeemed.text
        headers = {"Authorization": f"Bearer {redeemed.json()['access_token']}"}

        me = client.get("/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        assert me.json()["assigned_sale_id"] == sale_a_id
        assert me.json()["role"] == "client"

        assert client.get("/lots", headers=headers).status_code == 403
        assert client.get("/team/members", headers=headers).status_code == 403
        assert client.post("/sales", headers=headers, json={"name": "Nope"}).status_code == 403
        assert client.get(f"/sales/{sale_a_id}/client-uploads", headers=headers).status_code == 403

        other = client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_b_id, "single_lot_mode": "true"},
            files={"files": ("lot.jpg", _jpeg(), "image/jpeg")},
        )
        assert other.status_code == 403, other.text

        draft = "11111111-2222-4333-8444-555555555555"
        uploaded = client.post(
            "/jobs/upload",
            headers=headers,
            data={
                "sale_id": sale_a_id,
                "single_lot_mode": "true",
                "single_lot_manual_sku": "C1",
                "client_draft_id": draft,
            },
            files={"files": ("lot.jpg", _jpeg(), "image/jpeg")},
        )
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["client_draft_id"] == draft
        assert uploaded.json()["status"] == "receiving"

        jobs = client.get("/jobs", headers=headers)
        assert jobs.status_code == 200, jobs.text
        assert len(jobs.json()) == 1
        assert jobs.json()[0]["client_draft_id"] == draft

        uploads = client.get(f"/sales/{sale_a_id}/client-uploads", headers=owner_headers)
        assert uploads.status_code == 200, uploads.text
        assert uploads.json()[0]["email"] == "family@example.com"
        assert uploads.json()[0]["client_draft_id"] == draft
        assert uploads.json()[0]["sku"] == "C1"
    finally:
        app.dependency_overrides.clear()


def test_client_invite_expiry_and_revocation(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'invite-expiry.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
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
            json={"account_name": "CT Reno", "email": "owner-exp@example.com", "password": "owner-pass-1"},
        )
        assert reg.status_code == 200, reg.text
        owner_headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
        sale = client.post("/sales", headers=owner_headers, json={"name": "Pine Street"})
        assert sale.status_code == 201, sale.text
        sale_id = sale.json()["id"]
        soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        created = client.post(
            f"/sales/{sale_id}/client-invites",
            headers=owner_headers,
            json={"email": "guest@example.com", "access_expires_at": soon},
        )
        assert created.status_code == 201, created.text
        raw = created.json()["token"]
        first_password = created.json()["app_password"]
        user_id = created.json()["user_id"]
        redeemed = client.post("/auth/client-invite/redeem", json={"token": raw})
        assert redeemed.status_code == 200, redeemed.text
        headers = {"Authorization": f"Bearer {redeemed.json()['access_token']}"}

        db = Session()
        try:
            row = db.query(User).filter(User.id == user_id).one()
            row.access_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()

        expired = client.get("/auth/me", headers=headers)
        assert expired.status_code == 403, expired.text
        assert (
            client.post(
                "/auth/login",
                json={"email": "guest@example.com", "password": first_password},
            ).status_code
            == 403
        )
        assert client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_id, "single_lot_mode": "true"},
            files={"files": ("lot.jpg", _jpeg(), "image/jpeg")},
        ).status_code == 403

        # A new invite clears expiry so the same person can be let back in.
        again = client.post(
            f"/sales/{sale_id}/client-invites",
            headers=owner_headers,
            json={"email": "guest@example.com"},
        )
        assert again.status_code == 201, again.text
        assert (
            client.post(
                "/auth/login",
                json={"email": "guest@example.com", "password": first_password},
            ).status_code
            == 401
        )
        replacement = client.post(
            "/auth/login",
            json={"email": "guest@example.com", "password": again.json()["app_password"]},
        )
        assert replacement.status_code == 200, replacement.text
        fresh = client.post("/auth/client-invite/redeem", json={"token": again.json()["token"]})
        assert fresh.status_code == 200, fresh.text
        fresh_headers = {"Authorization": f"Bearer {fresh.json()['access_token']}"}
        assert client.get("/auth/me", headers=fresh_headers).status_code == 200

        revoked = client.delete(f"/sales/{sale_id}/client-invites/{user_id}", headers=owner_headers)
        assert revoked.status_code == 204, revoked.text
        assert client.get("/auth/me", headers=fresh_headers).status_code == 403
        assert client.post("/auth/client-invite/redeem", json={"token": again.json()["token"]}).status_code == 403
        assert client.get("/lots", headers=fresh_headers).status_code == 403
    finally:
        app.dependency_overrides.clear()
