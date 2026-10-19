"""Registration, master-admin binding, SECRET_KEY, and JWT versioning."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import create_sse_ticket, user_from_access_token
from app.config import Settings, secret_key_is_weak
from app.db import Base, get_db
from app.main import app
from app.models import User


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'authsec.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    import app.auth as auth_mod
    import app.main as main_mod

    monkeypatch.setattr(auth_mod, "SessionLocal", Session)
    monkeypatch.setattr(main_mod, "SessionLocal", Session)
    app.dependency_overrides[get_db] = override_db
    return TestClient(app), Session


def _clear() -> None:
    app.dependency_overrides.pop(get_db, None)


def test_secret_key_is_weak_detects_placeholders() -> None:
    assert secret_key_is_weak("")
    assert secret_key_is_weak("change-me")
    assert secret_key_is_weak("change-me-dev-secret")
    assert secret_key_is_weak("short")
    assert not secret_key_is_weak("a" * 32)


def test_settings_reject_weak_secret_outside_test(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SECRET_KEY", "change-me")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./app.db")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_reject_weak_secret_in_production(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "change-me-dev-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("CORS_ORIGINS", "https://example.com")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_register_closed_in_production_by_default(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "allow_open_registration", None)
    client, _Session = _client(tmp_path, monkeypatch)
    try:
        res = client.post(
            "/auth/register",
            json={"account_name": "CT", "email": "new@example.com", "password": "password1"},
        )
        assert res.status_code == 403
    finally:
        _clear()


def test_register_and_invite_block_unused_master_admin_email(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "master_admin_emails", "boss@example.com")
    monkeypatch.setattr(settings, "allow_open_registration", True)
    client, _Session = _client(tmp_path, monkeypatch)
    try:
        blocked = client.post(
            "/auth/register",
            json={"account_name": "CT", "email": "boss@example.com", "password": "password1"},
        )
        assert blocked.status_code == 400
        assert "reserved" in blocked.json()["detail"].lower()
        ok = client.post(
            "/auth/register",
            json={"account_name": "CT", "email": "owner@example.com", "password": "password1"},
        )
        assert ok.status_code == 200, ok.text
        invite = client.post(
            "/team/members",
            headers={"Authorization": f"Bearer {ok.json()['access_token']}"},
            json={"email": "boss@example.com", "password": "password1", "role": "catalogger"},
        )
        assert invite.status_code == 400
    finally:
        _clear()


def test_master_admin_user_id_binding(tmp_path, monkeypatch) -> None:
    from app.auth import is_master_admin
    from app.config import settings

    monkeypatch.setattr(settings, "master_admin_emails", "")
    monkeypatch.setattr(settings, "master_admin_user_ids", "user-admin")
    user = User(
        id="user-admin",
        account_id="acct",
        email="not-listed@example.com",
        password_hash="x",
        role="owner",
    )
    other = User(
        id="user-other",
        account_id="acct",
        email="other@example.com",
        password_hash="x",
        role="owner",
    )
    assert is_master_admin(user) is True
    assert is_master_admin(other) is False


def test_password_change_and_logout_all_invalidate_jwts(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        reg = client.post(
            "/auth/register",
            json={"account_name": "CT", "email": "owner@example.com", "password": "password1"},
        )
        assert reg.status_code == 200, reg.text
        token = reg.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/auth/me", headers=headers).status_code == 200
        changed = client.patch(
            "/auth/password",
            headers=headers,
            json={"current_password": "password1", "new_password": "password2"},
        )
        assert changed.status_code == 204
        assert client.get("/auth/me", headers=headers).status_code == 401
        login = client.post(
            "/auth/login",
            json={"email": "owner@example.com", "password": "password2"},
        )
        token2 = login.json()["access_token"]
        headers2 = {"Authorization": f"Bearer {token2}"}
        assert client.get("/auth/me", headers=headers2).status_code == 200
        assert client.post("/auth/logout-all", headers=headers2).status_code == 204
        assert client.get("/auth/me", headers=headers2).status_code == 401
    finally:
        _clear()


def test_sse_ticket_is_not_a_staff_jwt(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        reg = client.post(
            "/auth/register",
            json={"account_name": "CT", "email": "owner@example.com", "password": "password1"},
        )
        token = reg.json()["access_token"]
        db = Session()
        user = db.query(User).one()
        ticket = create_sse_ticket(user, "job-1")
        db.close()
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {ticket}"}).status_code == 401
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    finally:
        _clear()


def test_legacy_jwt_without_ver_still_works_until_bump(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        client.post(
            "/auth/register",
            json={"account_name": "CT", "email": "owner@example.com", "password": "password1"},
        )
        db = Session()
        user = db.query(User).one()
        from jose import jwt
        from app.config import settings as app_settings

        payload = {
            "sub": user.id,
            "account_id": user.account_id,
            "role": user.role,
            "iat": 1,
            "exp": 2_000_000_000,
        }
        legacy = jwt.encode(payload, app_settings.secret_key, algorithm="HS256")
        loaded = user_from_access_token(legacy, db)
        assert loaded.id == user.id
        db.close()
    finally:
        _clear()
