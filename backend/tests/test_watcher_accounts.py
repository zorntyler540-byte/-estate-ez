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


def test_watcher_route_feature_allows_ctseller_and_thumbnails() -> None:
    assert watcher_route_feature("POST", "/integrations/ctseller/session") == "analytics"
    assert watcher_route_feature("PUT", "/integrations/ctseller/credentials") == "analytics"
    assert watcher_route_feature("POST", "/integrations/ctseller/mfa/send") == "analytics"
    assert watcher_route_feature("POST", "/integrations/ctseller/mfa/verify") == "analytics"
    assert watcher_route_feature("GET", "/integrations/ctseller") == "always"
    assert watcher_route_feature("DELETE", "/integrations/ctseller/credentials") == "always"
    assert watcher_route_feature("GET", "/lots/lot-1/thumbnail") == "analytics"
    assert watcher_route_feature("GET", "/lots/lot-1/photos/p-1/thumbnail") == "analytics"
    # Catalog-side CT Seller tools and other lot routes stay closed.
    assert watcher_route_feature("GET", "/integrations/ctseller/sales") is None
    assert watcher_route_feature("GET", "/integrations/ctseller/conditions") is None
    assert watcher_route_feature("POST", "/integrations/ctseller/session/extra") is None
    assert watcher_route_feature("GET", "/lots/lot-1") is None
    assert watcher_route_feature("GET", "/lots/lot-1/photos") is None
    assert watcher_route_feature("GET", "/lots/lot-1/photos/p-1") is None
    assert watcher_route_feature("POST", "/lots/lot-1/thumbnail") is None
    assert watcher_route_feature("GET", "/lots/lot-1/videos/v-1/thumbnail") is None


def _unsigned_jwt(exp: float) -> str:
    import base64
    import json

    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'exp': int(exp)})}.sig"


def _watcher_with_ctseller(client, Session, *, access: str, refresh: str = "refresh-old"):
    from app.services.ctseller import seal_secret

    created = client.post(
        "/auth/register-watcher",
        json={"account_name": "Watch Co", "email": "watch@example.com", "password": "password1"},
    )
    assert created.status_code == 200, created.text
    headers = _auth(created.json()["access_token"])
    db = Session()
    account = db.query(Account).one()
    account.ctseller_username = "seller@example.com"
    account.ctseller_password_enc = seal_secret("seller-pass")
    account.ctseller_access_token_enc = seal_secret(access)
    account.ctseller_refresh_token_enc = seal_secret(refresh) if refresh else None
    db.add(account)
    db.commit()
    account_id = account.id
    db.close()
    return headers, account_id


def test_watcher_can_mint_ctseller_session_without_network_when_token_is_fresh(tmp_path, monkeypatch) -> None:
    import time

    from app.services.ctseller import CTSellerClient

    client, Session = _client(tmp_path, monkeypatch)
    fresh = _unsigned_jwt(time.time() + 3600)
    try:
        headers, _account_id = _watcher_with_ctseller(client, Session, access=fresh)
        for name in ("refresh_tokens", "login", "list_open_sales"):
            monkeypatch.setattr(
                CTSellerClient, name, lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(_n))
            )
        res = client.post("/integrations/ctseller/session", headers=headers)
        assert res.status_code == 200, res.text
        assert res.headers["cache-control"] == "no-store"
        body = res.json()
        assert body == {
            "access_token": fresh,
            "refresh_token": "refresh-old",
            "username": "seller@example.com",
            "connected": True,
        }
    finally:
        _clear()


def test_ctseller_session_mint_releases_db_and_refreshes_once_under_concurrency(tmp_path, monkeypatch) -> None:
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from app.services.ctseller import CTSellerClient, CTSellerTokens, unseal_secret

    client, Session = _client(tmp_path, monkeypatch)
    engine = Session.kw["bind"]
    stale = _unsigned_jwt(time.time() - 60)
    new_access = _unsigned_jwt(time.time() + 3600)
    calls: list[str] = []
    checked_out: list[int] = []
    calls_lock = threading.Lock()

    def fake_refresh(self, refresh_token):
        with calls_lock:
            calls.append(refresh_token)
            checked_out.append(engine.pool.checkedout())
        time.sleep(0.3)
        return CTSellerTokens(access_token=new_access, refresh_token="refresh-new")

    try:
        headers, account_id = _watcher_with_ctseller(client, Session, access=stale)
        monkeypatch.setattr(CTSellerClient, "refresh_tokens", fake_refresh)
        monkeypatch.setattr(
            CTSellerClient, "login", lambda *a, **k: (_ for _ in ()).throw(AssertionError("login"))
        )
        monkeypatch.setattr(
            CTSellerClient,
            "list_open_sales",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("probe")),
        )
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(
                pool.map(lambda _i: client.post("/integrations/ctseller/session", headers=headers), range(5))
            )
        assert [r.status_code for r in results] == [200] * 5, [r.text for r in results]
        assert {r.json()["access_token"] for r in results} == {new_access}
        assert {r.json()["refresh_token"] for r in results} == {"refresh-new"}
        assert all(r.headers["cache-control"] == "no-store" for r in results)
        assert calls == ["refresh-old"]
        db = Session()
        account = db.query(Account).filter(Account.id == account_id).one()
        assert unseal_secret(account.ctseller_access_token_enc) == new_access
        assert unseal_secret(account.ctseller_refresh_token_enc) == "refresh-new"
        db.close()

        # Single request: no pooled connection is checked out during CT Seller HTTP.
        db = Session()
        account = db.query(Account).filter(Account.id == account_id).one()
        from app.services.ctseller import seal_secret

        account.ctseller_access_token_enc = seal_secret(stale)
        db.add(account)
        db.commit()
        db.close()
        calls.clear()
        checked_out.clear()
        single = client.post("/integrations/ctseller/session", headers=headers)
        assert single.status_code == 200, single.text
        assert calls == ["refresh-new"]
        assert checked_out == [0]
    finally:
        _clear()


def test_watcher_ctseller_connect_and_mfa_routes_are_open(tmp_path, monkeypatch) -> None:
    from app.services.ctseller import CTSellerClient, CTSellerMfaRequired, CTSellerTokens

    client, Session = _client(tmp_path, monkeypatch)
    try:
        created = client.post(
            "/auth/register-watcher",
            json={"account_name": "Watch Co", "email": "watch@example.com", "password": "password1"},
        )
        assert created.status_code == 200, created.text
        headers = _auth(created.json()["access_token"])

        assert client.get("/integrations/ctseller", headers=headers).json()["connected"] is False

        def need_code(self, username, password, **kwargs):
            raise CTSellerMfaRequired("mfa-token-1", email="seller@example.com")

        monkeypatch.setattr(CTSellerClient, "login", need_code)
        put = client.put(
            "/integrations/ctseller/credentials",
            headers=headers,
            json={"username": "seller@example.com", "password": "seller-pass"},
        )
        assert put.status_code == 200, put.text
        assert put.json()["mfa_required"] is True

        monkeypatch.setattr(CTSellerClient, "send_mfa", lambda self, *a, **k: None)
        sent = client.post(
            "/integrations/ctseller/mfa/send",
            headers=headers,
            json={"mfa_token": "mfa-token-1", "channel": "email"},
        )
        assert sent.status_code == 200, sent.text

        monkeypatch.setattr(
            CTSellerClient,
            "verify_mfa",
            lambda self, token, otp: CTSellerTokens(access_token="aaa.bbb.ccc", refresh_token="r"),
        )
        verified = client.post(
            "/integrations/ctseller/mfa/verify",
            headers=headers,
            json={
                "username": "seller@example.com",
                "password": "seller-pass",
                "mfa_token": "mfa-token-1",
                "otp": "123456",
            },
        )
        assert verified.status_code == 200, verified.text
        assert client.get("/integrations/ctseller", headers=headers).json()["connected"] is True

        # Lot thumbnails reach the route (404: watcher has no lots) instead of the plan gate.
        thumb = client.get("/lots/missing/thumbnail", headers=headers)
        assert thumb.status_code == 404, thumb.text
        photo = client.get("/lots/missing/photos/p-1/thumbnail", headers=headers)
        assert photo.status_code == 404, photo.text

        db = Session()
        account = db.query(Account).one()
        account.feature_analytics = False
        db.add(account)
        db.commit()
        db.close()
        for method, path in (
            ("POST", "/integrations/ctseller/session"),
            ("PUT", "/integrations/ctseller/credentials"),
            ("POST", "/integrations/ctseller/mfa/send"),
            ("GET", "/lots/missing/thumbnail"),
            ("GET", "/lots/missing/photos/p-1/thumbnail"),
        ):
            blocked = client.request(method, path, headers=headers, json={})
            assert blocked.status_code == 403, (method, path, blocked.text)
        assert client.get("/integrations/ctseller", headers=headers).status_code == 200
        gone = client.delete("/integrations/ctseller/credentials", headers=headers)
        assert gone.status_code == 200, gone.text
        assert gone.json()["connected"] is False
    finally:
        _clear()


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


def test_master_admin_can_create_full_and_watcher_accounts(tmp_path, monkeypatch) -> None:
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

        stranger = client.post(
            "/auth/register-watcher",
            json={"account_name": "Other", "email": "other@example.com", "password": "password1"},
        )
        assert stranger.status_code == 200, stranger.text
        denied = client.post(
            "/admin/accounts",
            headers=_auth(stranger.json()["access_token"]),
            json={
                "name": "Nope",
                "email": "nope@example.com",
                "password": "password1",
                "plan_code": "beta_free",
            },
        )
        assert denied.status_code == 403

        reserved = client.post(
            "/admin/accounts",
            headers=admin_headers,
            json={
                "name": "Reserved",
                "email": "boss@example.com",
                "password": "password1",
                "plan_code": "beta_free",
            },
        )
        assert reserved.status_code == 400

        bad_plan = client.post(
            "/admin/accounts",
            headers=admin_headers,
            json={
                "name": "Bad Plan",
                "email": "bad-plan@example.com",
                "password": "password1",
                "plan_code": "enterprise",
            },
        )
        assert bad_plan.status_code == 400

        full = client.post(
            "/admin/accounts",
            headers=admin_headers,
            json={
                "name": "Oak Street",
                "email": "oak@example.com",
                "password": "franchise1",
                "plan_code": "beta_free",
                "feature_analytics": False,
                "feature_facebook": False,
            },
        )
        assert full.status_code == 201, full.text
        full_body = full.json()
        assert full_body["name"] == "Oak Street"
        assert full_body["owner_email"] == "oak@example.com"
        assert full_body["plan_code"] == "beta_free"
        assert full_body["sale_count"] == 1
        assert "password" not in full_body
        assert "access_token" not in full_body

        signed_in = client.post(
            "/auth/login",
            json={"email": "oak@example.com", "password": "franchise1"},
        )
        assert signed_in.status_code == 200, signed_in.text
        oak_headers = _auth(signed_in.json()["access_token"])
        oak_me = client.get("/auth/me", headers=oak_headers).json()
        assert oak_me["role"] == "owner"
        assert oak_me["plan_code"] == "beta_free"
        assert oak_me["feature_analytics"] is True
        assert oak_me["feature_facebook"] is True
        sales = client.get("/sales", headers=oak_headers)
        assert sales.status_code == 200, sales.text
        assert [row["name"] for row in sales.json()] == ["General"]

        duplicate = client.post(
            "/admin/accounts",
            headers=admin_headers,
            json={
                "name": "Oak Again",
                "email": "oak@example.com",
                "password": "franchise1",
                "plan_code": "beta_free",
            },
        )
        assert duplicate.status_code == 400

        watcher = client.post(
            "/admin/accounts",
            headers=admin_headers,
            json={
                "name": "Push Only",
                "email": "push@example.com",
                "password": "watcherpass",
                "plan_code": "watcher",
                "feature_analytics": True,
                "feature_facebook": False,
            },
        )
        assert watcher.status_code == 201, watcher.text
        assert watcher.json()["plan_code"] == "watcher"
        assert watcher.json()["sale_count"] == 0
        assert watcher.json()["feature_facebook"] is False

        watcher_login = client.post(
            "/auth/login",
            json={"email": "push@example.com", "password": "watcherpass"},
        )
        assert watcher_login.status_code == 200, watcher_login.text
        watcher_headers = _auth(watcher_login.json()["access_token"])
        assert client.get("/engagement/sales", headers=watcher_headers).status_code == 200
        hidden_fb = client.get("/integrations/facebook/status", headers=watcher_headers)
        assert hidden_fb.status_code == 403
        assert client.get("/sales", headers=watcher_headers).status_code == 403

        db = Session()
        oak = db.query(Account).filter(Account.name == "Oak Street").one()
        assert oak.feature_analytics is False
        assert oak.feature_facebook is False
        assert db.query(User).filter(User.email == "boss@example.com").one().account_id != oak.id
        db.close()

        listed = client.get("/admin/accounts", headers=admin_headers)
        emails = {row["owner_email"] for row in listed.json()}
        assert "oak@example.com" in emails
        assert "push@example.com" in emails
    finally:
        _clear()
