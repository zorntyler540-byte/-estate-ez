"""Auction Watcher signup, plan gate, and admin switches."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import Account, Sale, User
from app.services.plans import watcher_route_feature


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'watcher.db'}")
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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_watcher_route_feature_allowlist() -> None:
    assert watcher_route_feature("GET", "/auth/me") == "always"
    assert watcher_route_feature("GET", "/engagement/sales") == "analytics"
    assert watcher_route_feature("GET", "/engagement/sales/4401") == "analytics"
    assert watcher_route_feature("GET", "/engagement/sales/4401/calendar") == "analytics"
    assert watcher_route_feature("POST", "/integrations/engagement/snapshots") == "analytics"
    assert watcher_route_feature("POST", "/engagement/sales/4401/calendar/facebook") == "facebook"
    assert watcher_route_feature("GET", "/integrations/facebook/status") == "facebook"
    assert watcher_route_feature("GET", "/facebook/posts/batches/abc") == "facebook"
    assert watcher_route_feature("GET", "/sales") is None
    assert watcher_route_feature("GET", "/lots") is None
    assert watcher_route_feature("POST", "/jobs/upload") is None
    assert watcher_route_feature("POST", "/sales/abc/hero-background") is None
    assert watcher_route_feature("GET", "/admin/accounts") is None
    assert watcher_route_feature("GET", "/engagement/sales/nope") is None


def test_watcher_signup_stays_open_when_catalog_registration_is_closed(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "allow_open_registration", None)
    monkeypatch.setattr(settings, "master_admin_emails", "boss@example.com")
    client, Session = _client(tmp_path, monkeypatch)
    try:
        closed = client.post(
            "/auth/register",
            json={"account_name": "Full", "email": "full@example.com", "password": "password1"},
        )
        assert closed.status_code == 403

        reserved = client.post(
            "/auth/register-watcher",
            json={"account_name": "Nope", "email": "boss@example.com", "password": "password1"},
        )
        assert reserved.status_code == 400

        created = client.post(
            "/auth/register-watcher",
            json={"account_name": "North Franchise", "email": "owner@example.com", "password": "password1"},
        )
        assert created.status_code == 200, created.text
        token = created.json()["access_token"]
        headers = _auth(token)

        me = client.get("/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        body = me.json()
        assert body["plan_code"] == "watcher"
        assert body["feature_analytics"] is True
        assert body["feature_facebook"] is True
        assert body["role"] == "owner"

        db = Session()
        account = db.query(Account).one()
        assert account.plan_code == "watcher"
        assert db.query(Sale).count() == 0
        db.close()

        for path in ("/sales", "/lots", "/team/members", "/ops/jobs"):
            blocked = client.get(path, headers=headers)
            assert blocked.status_code == 403, path
            assert "Auction Watcher" in blocked.json()["detail"]

        upload = client.post("/jobs/upload", headers=headers)
        assert upload.status_code == 403

        queue = client.get("/engagement/sales", headers=headers)
        assert queue.status_code == 200, queue.text
        assert queue.json() == []

        facebook = client.get("/integrations/facebook/status", headers=headers)
        assert facebook.status_code == 200, facebook.text

        clicks = client.get("/facebook/clicks?sale_id=missing-sale", headers=headers)
        assert clicks.status_code == 404
    finally:
        _clear()


def test_admin_switches_limit_watcher_and_leave_full_catalog_open(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "master_admin_emails", "")
    monkeypatch.setattr(settings, "allow_open_registration", True)
    client, Session = _client(tmp_path, monkeypatch)
    try:
        admin = client.post(
            "/auth/register",
            json={"account_name": "Estate-EZ", "email": "boss@example.com", "password": "password1"},
        )
        assert admin.status_code == 200, admin.text
        monkeypatch.setattr(settings, "master_admin_emails", "boss@example.com")
        admin_headers = _auth(admin.json()["access_token"])
        assert client.get("/sales", headers=admin_headers).status_code == 200

        watcher = client.post(
            "/auth/register-watcher",
            json={"account_name": "South Franchise", "email": "south@example.com", "password": "password1"},
        )
        assert watcher.status_code == 200, watcher.text
        watcher_headers = _auth(watcher.json()["access_token"])
        watcher_id = client.get("/auth/me", headers=watcher_headers).json()["account_id"]

        listed = client.get("/admin/accounts", headers=admin_headers)
        assert listed.status_code == 200, listed.text
        row = next(item for item in listed.json() if item["id"] == watcher_id)
        assert row["plan_code"] == "watcher"
        assert row["feature_analytics"] is True
        assert row["feature_facebook"] is True
        assert row["sale_count"] == 0

        bad_plan = client.patch(
            f"/admin/accounts/{watcher_id}",
            headers=admin_headers,
            json={"plan_code": "enterprise"},
        )
        assert bad_plan.status_code == 400

        off = client.patch(
            f"/admin/accounts/{watcher_id}",
            headers=admin_headers,
            json={"feature_analytics": False, "feature_facebook": True, "plan_code": "watcher"},
        )
        assert off.status_code == 200, off.text
        assert off.json()["feature_analytics"] is False
        blocked_queue = client.get("/engagement/sales", headers=watcher_headers)
        assert blocked_queue.status_code == 403
        assert "Push queue" in blocked_queue.json()["detail"]
        assert client.get("/integrations/facebook/status", headers=watcher_headers).status_code == 200
        me_off = client.get("/auth/me", headers=watcher_headers).json()
        assert me_off["feature_analytics"] is False
        assert me_off["feature_facebook"] is True

        facebook_off = client.patch(
            f"/admin/accounts/{watcher_id}",
            headers=admin_headers,
            json={"feature_analytics": True, "feature_facebook": False},
        )
        assert facebook_off.status_code == 200, facebook_off.text
        assert client.get("/engagement/sales", headers=watcher_headers).status_code == 200
        hidden_fb = client.get("/integrations/facebook/status", headers=watcher_headers)
        assert hidden_fb.status_code == 403
        assert "Facebook" in hidden_fb.json()["detail"]

        promoted = client.patch(
            f"/admin/accounts/{watcher_id}",
            headers=admin_headers,
            json={"plan_code": "beta_free", "feature_analytics": False, "feature_facebook": False},
        )
        assert promoted.status_code == 200, promoted.text
        full_me = client.get("/auth/me", headers=watcher_headers).json()
        assert full_me["plan_code"] == "beta_free"
        assert full_me["feature_analytics"] is True
        assert full_me["feature_facebook"] is True
        assert client.get("/sales", headers=watcher_headers).status_code == 200

        db = Session()
        stored = db.query(Account).filter(Account.id == watcher_id).one()
        assert stored.plan_code == "beta_free"
        assert stored.feature_analytics is False
        assert stored.feature_facebook is False
        boss = db.query(User).filter(User.email == "boss@example.com").one()
        assert boss.account_id != watcher_id
        db.close()
    finally:
        _clear()
