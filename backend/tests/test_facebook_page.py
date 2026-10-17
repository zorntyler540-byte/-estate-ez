"""Facebook Page OAuth, preview, and posting — Graph HTTP is always mocked."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import get_current_user
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Account, FacebookPagePost, Lot, Photo, Sale, User
from app.services.facebook_caption import FOLLOW_HOOK_LINE, apply_follow_hook, template_caption
from app.services.facebook_crypto import seal_facebook_secret, unseal_facebook_secret
from app.services.facebook_page import (
    encode_oauth_state,
    listing_photos_for_facebook,
    suggest_schedule_slots,
    validate_scheduled_time,
)
from app.services.meta_graph import GRAPH_API_VERSION, PAGE_OAUTH_SCOPES, _parse_graph_error


def test_graph_api_version_is_pinned() -> None:
    assert GRAPH_API_VERSION == "v23.0"
    assert GRAPH_API_VERSION.startswith("v")
    assert "pages_manage_posts" in PAGE_OAUTH_SCOPES
    assert "pages_show_list" in PAGE_OAUTH_SCOPES
    assert "pages_read_engagement" in PAGE_OAUTH_SCOPES


def test_facebook_secret_roundtrip() -> None:
    sealed = seal_facebook_secret("page-token-secret")
    assert sealed
    assert sealed != "page-token-secret"
    assert unseal_facebook_secret(sealed) == "page-token-secret"


def test_token_error_parser_flags_reconnect() -> None:
    err = _parse_graph_error(
        {"error": {"message": "Invalid OAuth access token.", "type": "OAuthException", "code": 190}},
        400,
    )
    assert err.is_token_error
    assert "Reconnect" in str(err)


def test_listing_photos_drop_tape_and_use_hero_order() -> None:
    tape = SimpleNamespace(id="tape", photo_role="tape", sort_order=0, original_name="tape.jpg")
    detail = SimpleNamespace(id="detail", photo_role="angle_only", sort_order=1, original_name="d.jpg")
    hero = SimpleNamespace(id="hero", photo_role="hero", sort_order=2, original_name="h.jpg")
    kept = listing_photos_for_facebook([tape, detail, hero])
    assert [p.id for p in kept] == ["hero", "detail"]


def test_caption_template_includes_link_close_and_follow_hook() -> None:
    close = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)
    text = template_caption(
        title="Fenton vase",
        description="Hobnail glass.",
        item_url="https://ctbids.com/estate-sale/1/item/2",
        close_at=close,
        include_follow_hook=True,
        current_bid=12,
        watchers=4,
    )
    assert "Fenton vase" in text
    assert "https://ctbids.com/estate-sale/1/item/2" in text
    assert "Bidding closes" in text
    assert FOLLOW_HOOK_LINE in text
    stripped = apply_follow_hook(text, include=False)
    assert FOLLOW_HOOK_LINE not in stripped
    assert FOLLOW_HOOK_LINE in apply_follow_hook(stripped, include=True)


def test_schedule_suggestions_keep_meta_window() -> None:
    now = datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc)
    close = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)
    slots = suggest_schedule_slots(now=now, close_at=close)
    labels = [row["label"] for row in slots]
    assert any("2 days before close" in label for label in labels)
    assert any("Morning of close" in label for label in labels)
    too_soon = now + timedelta(minutes=2)
    try:
        validate_scheduled_time(too_soon, now=now)
        raise AssertionError("expected 10-minute lead")
    except Exception as exc:
        assert "10 minutes" in str(exc)


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'facebook.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _seed(db, *, role: str = "owner"):
    account = Account(id="acct-fb", name="CT")
    user = User(
        id="user-fb",
        account_id=account.id,
        email="fb@example.com",
        password_hash="x",
        role=role,
    )
    sale = Sale(id="sale-fb", account_id=account.id, user_id=user.id, name="Estate")
    lot = Lot(
        id="lot-fb",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="AA100",
        auction_title="Fenton hobnail vase",
        professional_description="Opalescent glass.",
        ctseller_item_id=99,
        ctseller_sale_id=12,
    )
    hero = Photo(
        id="photo-hero",
        account_id=account.id,
        user_id=user.id,
        lot_id=lot.id,
        original_name="hero.jpg",
        storage_path="catalog/hero.jpg",
        photo_role="hero",
        sort_order=0,
        shows_whole_lot=True,
    )
    tape = Photo(
        id="photo-tape",
        account_id=account.id,
        user_id=user.id,
        lot_id=lot.id,
        original_name="tape.jpg",
        storage_path="catalog/tape.jpg",
        photo_role="tape",
        sort_order=1,
    )
    db.add_all([account, user, sale, lot, hero, tape])
    db.commit()
    return account, user, lot


def _client(tmp_path, *, role: str = "owner", connected: bool = False):
    Session = _session(tmp_path)
    db = Session()
    account, user, lot = _seed(db, role=role)
    if connected:
        account.facebook_user_id = "user-1"
        account.facebook_user_token_enc = seal_facebook_secret("USER_TOKEN")
        account.facebook_page_id = "111"
        account.facebook_page_name = "Caring Transitions Page"
        account.facebook_page_token_enc = seal_facebook_secret("PAGE_TOKEN")
        db.add(account)
        db.commit()

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app), db, account, user, lot


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def test_status_hidden_when_meta_app_id_unset(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "")
    monkeypatch.setattr(settings, "meta_app_secret", "")
    client, _db, _account, _user, _lot = _client(tmp_path)
    try:
        res = client.get("/integrations/facebook/status")
        assert res.status_code == 200
        body = res.json()
        assert body["configured"] is False
        assert body["connected"] is False
        assert body["graph_api_version"] == GRAPH_API_VERSION
        start = client.get("/integrations/facebook/oauth/start")
        assert start.status_code == 503
    finally:
        _clear_overrides()


def test_oauth_start_is_owner_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, _db, _account, _user, _lot = _client(tmp_path, role="catalogger")
    try:
        res = client.get("/integrations/facebook/oauth/start")
        assert res.status_code == 403
    finally:
        _clear_overrides()


def test_oauth_callback_stores_encrypted_page_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    monkeypatch.setattr(settings, "public_web_origin", "http://localhost:3000")
    client, db, _account, user, _lot = _client(tmp_path)
    state = encode_oauth_state(account_id=user.account_id, user_id=user.id)

    def fake_exchange_code(**_kwargs):
        return {"access_token": "SHORT", "expires_in": 3600}

    def fake_long(**_kwargs):
        return {"access_token": "LONG_USER", "expires_in": 5184000}

    def fake_me(_token):
        return {"id": "fb-user-9", "name": "Tyler"}

    def fake_pages(_token):
        return [{"id": "page-22", "name": "Aunt Page", "access_token": "PAGE_LIVE", "can_post": True}]

    try:
        with (
            patch("app.services.facebook_page.exchange_code_for_user_token", fake_exchange_code),
            patch("app.services.facebook_page.exchange_long_lived_user_token", fake_long),
            patch("app.services.facebook_page.get_me", fake_me),
            patch("app.services.facebook_page.list_user_pages", fake_pages),
        ):
            res = client.get(
                "/integrations/facebook/oauth/callback",
                params={"code": "abc", "state": state},
                follow_redirects=False,
            )
        assert res.status_code == 302
        assert "facebook=connected" in res.headers["location"]
        account = db.query(Account).filter(Account.id == user.account_id).first()
        assert account.facebook_page_id == "page-22"
        assert account.facebook_page_name == "Aunt Page"
        assert account.facebook_page_token_enc
        assert unseal_facebook_secret(account.facebook_page_token_enc) == "PAGE_LIVE"
        assert "PAGE_LIVE" not in (account.facebook_page_token_enc or "")
        status = client.get("/integrations/facebook/status").json()
        assert status["configured"] is True
        assert status["connected"] is True
        assert status["page_name"] == "Aunt Page"
    finally:
        _clear_overrides()


def test_disconnect_clears_tokens(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, db, account, _user, _lot = _client(tmp_path, connected=True)
    try:
        res = client.delete("/integrations/facebook/connection")
        assert res.status_code == 200
        assert res.json()["connected"] is False
        db.refresh(account)
        assert account.facebook_page_token_enc is None
        assert account.facebook_page_id is None
    finally:
        _clear_overrides()


def test_preview_excludes_tape_and_does_not_post(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, _db, _account, _user, lot = _client(tmp_path, connected=True)
    try:
        with patch(
            "app.services.facebook_page.draft_facebook_caption",
            return_value="Bid here\nhttps://ctbids.com/estate-sale/12/item/99",
        ):
            res = client.post(
                "/facebook/posts/preview",
                json={"lot_ids": [lot.id], "include_follow_hook": True},
            )
        assert res.status_code == 200
        body = res.json()
        row = body["lots"][0]
        assert row["photo_ids"] == ["photo-hero"]
        assert "tape" not in row["photo_ids"]
        assert "ctbids.com" in row["caption"]
        assert client.get("/facebook/posts").json() == []
    finally:
        _clear_overrides()


def test_publish_requires_explicit_confirm(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, _db, _account, _user, lot = _client(tmp_path, connected=True)
    try:
        res = client.post(
            "/facebook/posts",
            json={
                "confirm": False,
                "items": [{"lot_id": lot.id, "caption": "Hello"}],
            },
        )
        assert res.status_code == 400
        assert "Preview" in res.json()["detail"]
    finally:
        _clear_overrides()


def test_publish_uploads_unpublished_then_feed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, db, _account, _user, lot = _client(tmp_path, connected=True)
    uploads: list[str] = []

    def fake_upload(*, page_id, page_token, image_bytes, filename):
        uploads.append(filename)
        assert page_id == "111"
        assert page_token == "PAGE_TOKEN"
        assert image_bytes
        return "media-1"

    def fake_feed(*, page_id, page_token, message, media_fbids, scheduled_unix=None):
        assert media_fbids == ["media-1"]
        assert "Fenton" in message
        assert scheduled_unix is None
        return "111_555"

    try:
        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"\xff\xd8\xffjpeg"),
            patch("app.services.facebook_page.upload_unpublished_photo", fake_upload),
            patch("app.services.facebook_page.create_page_feed_post", fake_feed),
        ):
            res = client.post(
                "/facebook/posts",
                json={
                    "confirm": True,
                    "source": "lot",
                    "items": [{"lot_id": lot.id, "caption": "Fenton vase on CT Bids"}],
                },
            )
        assert res.status_code == 200, res.text
        row = res.json()["results"][0]
        assert row["ok"] is True
        assert row["facebook_post_id"] == "111_555"
        assert row["status"] == "posted"
        assert uploads
        stored = db.query(FacebookPagePost).one()
        assert stored.facebook_post_id == "111_555"
        assert stored.status == "posted"
        assert stored.lot_id == lot.id
        history = client.get("/facebook/posts", params={"lot_id": lot.id}).json()
        assert history[0]["facebook_post_id"] == "111_555"
    finally:
        _clear_overrides()


def test_publish_skips_duplicate_unless_forced(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, db, account, user, lot = _client(tmp_path, connected=True)
    db.add(
        FacebookPagePost(
            account_id=account.id,
            user_id=user.id,
            lot_id=lot.id,
            lot_ids_json=f'["{lot.id}"]',
            facebook_page_id="111",
            caption="old",
            status="posted",
        )
    )
    db.commit()
    try:
        res = client.post(
            "/facebook/posts",
            json={"confirm": True, "items": [{"lot_id": lot.id, "caption": "Again"}]},
        )
        assert res.status_code == 200
        row = res.json()["results"][0]
        assert row["ok"] is False
        assert row["skipped"] is True
        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="m2"),
            patch("app.services.facebook_page.create_page_feed_post", return_value="111_999"),
        ):
            forced = client.post(
                "/facebook/posts",
                json={
                    "confirm": True,
                    "force": True,
                    "items": [{"lot_id": lot.id, "caption": "Again"}],
                },
            )
        assert forced.json()["results"][0]["ok"] is True
    finally:
        _clear_overrides()


def test_expired_token_prompts_reconnect(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, _db, _account, _user, lot = _client(tmp_path, connected=True)
    from app.services.meta_graph import MetaGraphError

    try:
        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
            patch(
                "app.services.facebook_page.upload_unpublished_photo",
                side_effect=MetaGraphError("Invalid OAuth access token.", code=190, is_token_error=True),
            ),
        ):
            res = client.post(
                "/facebook/posts",
                json={"confirm": True, "items": [{"lot_id": lot.id, "caption": "Hi"}]},
            )
        row = res.json()["results"][0]
        assert row["ok"] is False
        assert row["needs_reconnect"] is True
        status = client.get("/integrations/facebook/status").json()
        assert status["needs_reconnect"] is True
    finally:
        _clear_overrides()


def test_oauth_start_url_uses_pinned_graph_version(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "999")
    monkeypatch.setattr(settings, "meta_app_secret", "secret")
    monkeypatch.setattr(settings, "meta_oauth_redirect_uri", "http://localhost:8000/integrations/facebook/oauth/callback")
    client, _db, _account, _user, _lot = _client(tmp_path)
    try:
        res = client.get("/integrations/facebook/oauth/start")
        assert res.status_code == 200
        url = res.json()["url"]
        assert f"facebook.com/{GRAPH_API_VERSION}/dialog/oauth" in url
        assert "pages_manage_posts" in url
        assert "999" in url
    finally:
        _clear_overrides()
