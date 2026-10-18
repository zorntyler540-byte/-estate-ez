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
from app.services.facebook_calendar import (
    PLATFORM_PUBLISH_TARGETS,
    PUBLISH_TARGET_FACEBOOK_PAGE,
    calendar_key_aliases,
    calendar_slot_key,
    current_publish_target,
    is_legacy_calendar_key,
    migrate_calendar_key,
    photo_offset_for,
    slot_angle_for,
    slot_id_from_local_time,
)
from app.services.facebook_caption import FOLLOW_HOOK_LINE, apply_follow_hook, template_caption
from app.services.facebook_crypto import seal_facebook_secret, unseal_facebook_secret
from app.services.facebook_page import (
    encode_oauth_state,
    listing_photos_for_facebook,
    rotate_listing_photos,
    suggest_schedule_slots,
    validate_scheduled_time,
)
from app.services.meta_graph import GRAPH_API_VERSION, PAGE_OAUTH_SCOPES, _parse_graph_error


def test_graph_api_version_is_pinned() -> None:
    assert GRAPH_API_VERSION == "v26.0"
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


def test_token_error_parser_ignores_generic_oauth_copy() -> None:
    err = _parse_graph_error(
        {
            "error": {
                "message": "This session cannot publish because the post is outside the allowed window.",
                "type": "OAuthException",
                "code": 100,
            }
        },
        400,
    )
    assert not err.is_token_error


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
    assert "See the whole sale: https://ctbids.com/estate-sale/1" in text
    lot_at = text.index("https://ctbids.com/estate-sale/1/item/2")
    sale_at = text.index("See the whole sale: https://ctbids.com/estate-sale/1")
    assert lot_at < sale_at
    assert "Bidding closes" in text
    assert FOLLOW_HOOK_LINE in text
    stripped = apply_follow_hook(text, include=False)
    assert FOLLOW_HOOK_LINE not in stripped
    assert FOLLOW_HOOK_LINE in apply_follow_hook(stripped, include=True)


def test_caption_angles_differ_per_slot() -> None:
    kwargs = dict(
        title="Fenton vase",
        description="Hobnail glass.",
        item_url="https://ctbids.com/estate-sale/1/item/2",
        close_at=datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc),
        include_follow_hook=False,
    )
    lines = {angle: template_caption(**kwargs, angle=angle).split("\n", 1)[0] for angle in (
        "collector_find",
        "midday",
        "stories",
        "tiktok",
        "closing",
    )}
    assert lines["collector_find"].startswith("Collector find:")
    assert lines["midday"].startswith("Midday spotlight:")
    assert lines["stories"].startswith("Still on the table tonight:")
    assert lines["tiktok"].startswith("Would you bid on this?")
    assert lines["closing"].startswith("Closing countdown:")
    assert len(set(lines.values())) == 5


def test_calendar_slot_keys_include_slot_and_migrate_legacy() -> None:
    assert slot_id_from_local_time("09:00") == "0900"
    assert slot_id_from_local_time("12:15") == "1215"
    assert slot_id_from_local_time("16:30") == "1630"
    assert slot_id_from_local_time("18:00") == "1800"
    assert slot_id_from_local_time("17:40") == "1740"
    assert calendar_slot_key(12, "lot-1", "0900") == "pushcal:12:lot-1:0900"
    assert is_legacy_calendar_key("pushcal:12:lot-1")
    assert migrate_calendar_key("pushcal:12:lot-1") == "pushcal:12:lot-1:0900"
    assert migrate_calendar_key("pushcal:12:lot-1:1215") == "pushcal:12:lot-1:1215"
    assert "pushcal:12:lot-1" in calendar_key_aliases("pushcal:12:lot-1:0900")
    assert current_publish_target("Instagram") == PUBLISH_TARGET_FACEBOOK_PAGE
    assert current_publish_target("TikTok") == PUBLISH_TARGET_FACEBOOK_PAGE
    assert current_publish_target("FB + IG Stories") == PUBLISH_TARGET_FACEBOOK_PAGE
    assert set(PLATFORM_PUBLISH_TARGETS.values()) == {PUBLISH_TARGET_FACEBOOK_PAGE}
    assert slot_angle_for("09:00") == "collector_find"
    assert slot_angle_for("12:15") == "midday"
    assert slot_angle_for("16:30") == "stories"
    assert slot_angle_for("18:00") == "tiktok"
    assert slot_angle_for("17:40", hook="Closing countdown") == "closing"
    assert photo_offset_for("1740") == 4


def test_rotate_listing_photos_wraps() -> None:
    photos = [SimpleNamespace(id=f"p{i}") for i in range(3)]
    assert [p.id for p in rotate_listing_photos(photos, 0)] == ["p0", "p1", "p2"]
    assert [p.id for p in rotate_listing_photos(photos, 1)] == ["p1", "p2", "p0"]
    assert [p.id for p in rotate_listing_photos(photos, 4)] == ["p1", "p2", "p0"]
    assert rotate_listing_photos([], 2) == []


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
        assert res.json().get("queued") is False
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


def test_preview_and_publish_are_owner_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, _db, _account, _user, lot = _client(tmp_path, role="catalogger", connected=True)
    try:
        preview = client.post("/facebook/posts/preview", json={"lot_ids": [lot.id]})
        assert preview.status_code == 403
        posted = client.post(
            "/facebook/posts",
            json={"confirm": True, "items": [{"lot_id": lot.id, "caption": "Hi"}]},
        )
        assert posted.status_code == 403
    finally:
        _clear_overrides()


def test_expired_user_token_does_not_force_reconnect_when_page_token_present(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, db, account, _user, _lot = _client(tmp_path, connected=True)
    account.facebook_user_token_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.add(account)
    db.commit()
    try:
        status = client.get("/integrations/facebook/status").json()
        assert status["connected"] is True
        assert status["needs_reconnect"] is False
    finally:
        _clear_overrides()


def test_user_token_without_page_is_pick_not_reconnect(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, db, account, _user, _lot = _client(tmp_path, connected=True)
    account.facebook_page_id = None
    account.facebook_page_name = None
    account.facebook_page_token_enc = None
    db.add(account)
    db.commit()
    try:
        status = client.get("/integrations/facebook/status").json()
        assert status["connected"] is False
        assert status["needs_reconnect"] is False
    finally:
        _clear_overrides()


def test_multi_lot_publish_queues_background_batch(tmp_path, monkeypatch) -> None:
    from app import facebook_post_tasks

    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, db, account, user, lot = _client(tmp_path, connected=True)
    lot2 = Lot(
        id="lot-fb-2",
        account_id=account.id,
        user_id=user.id,
        sale_id=lot.sale_id,
        sku="AA101",
        auction_title="Second vase",
        professional_description="Pair.",
        ctseller_item_id=100,
        ctseller_sale_id=12,
    )
    hero2 = Photo(
        id="photo-hero-2",
        account_id=account.id,
        user_id=user.id,
        lot_id=lot2.id,
        original_name="hero2.jpg",
        storage_path="catalog/hero2.jpg",
        photo_role="hero",
        sort_order=0,
        shows_whole_lot=True,
    )
    db.add_all([lot2, hero2])
    db.commit()

    Session = db.get_bind()
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=Session, autoflush=False, autocommit=False, future=True)
    facebook_post_tasks.reset_facebook_post_runtime_state()
    monkeypatch.setattr(facebook_post_tasks, "SessionLocal", factory)

    class ImmediateThread:
        def __init__(self, target=None, **_kwargs):
            self._target = target

        def start(self):
            assert self._target
            self._target()

    monkeypatch.setattr(facebook_post_tasks.threading, "Thread", ImmediateThread)

    try:
        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"\xff\xd8\xffjpeg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="media-x"),
            patch("app.services.facebook_page.create_page_feed_post", return_value="111_777"),
        ):
            res = client.post(
                "/facebook/posts",
                json={
                    "confirm": True,
                    "source": "push_queue",
                    "items": [
                        {"lot_id": lot.id, "caption": "First lot on CT Bids"},
                        {"lot_id": lot2.id, "caption": "Second lot on CT Bids"},
                    ],
                },
            )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["queued"] is True
        assert body["batch_id"]
        poll = client.get(f"/facebook/posts/batches/{body['batch_id']}")
        assert poll.status_code == 200, poll.text
        done = poll.json()
        assert done["active"] is False
        assert done["total"] == 2
        assert len(done["results"]) == 2
        assert all(row["ok"] for row in done["results"])
        assert db.query(FacebookPagePost).count() == 2
    finally:
        facebook_post_tasks.reset_facebook_post_runtime_state()
        _clear_overrides()


def _future_push_body(lot: Lot) -> dict:
    from zoneinfo import ZoneInfo

    pacific = ZoneInfo("America/Los_Angeles")
    captured = datetime.now(pacific) + timedelta(days=3)
    captured = captured.replace(hour=8, minute=0, second=0, microsecond=0)
    ends = captured + timedelta(days=5)
    return {
        "ctseller_sale_id": int(lot.ctseller_sale_id or 12),
        "sale_title": "Estate",
        "auction_url": "https://ctbids.com/estate-sale/12/",
        "sale_ends_at": ends.isoformat(),
        "captured_at": captured.isoformat(),
        "items": [
            {
                "ctseller_item_id": int(lot.ctseller_item_id or 99),
                "sku": lot.sku or "AA100",
                "title": lot.auction_title or "Vase",
                "views": 20,
                "watchers": 6,
                "bids": 2,
                "price": 8,
                "reserve": 5,
                "item_url": "https://ctbids.com/estate-sale/12/item/99",
                "item_status": "Ready",
            }
        ],
    }


def test_push_calendar_facebook_slot_schedules_after_confirm(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, db, _account, _user, lot = _client(tmp_path, connected=True)
    try:
        snap = client.post("/integrations/engagement/snapshots", json=_future_push_body(lot))
        assert snap.status_code == 200, snap.text
        calendar = client.get("/engagement/sales/12/calendar").json()
        facebook_rows = [row for row in calendar if row["platform"] == "Facebook"]
        assert facebook_rows
        slot = facebook_rows[0]
        assert slot["lot_id"] == lot.id
        assert slot["local_time"] == "09:00"
        assert slot["facebook_status"] == "planned"
        assert slot["facebook_status_label"] == "Planned"
        assert slot["facebook_schedulable"] is True
        assert slot["facebook_can_cancel"] is False
        assert slot["facebook_blocked_reason"] is None
        assert slot["slot_key"] == f"pushcal:12:{lot.id}:0900"
        assert slot["publish_target"] == "facebook_page"
        assert slot["intended_platform"] == "Facebook"
        countdown = next(row for row in calendar if row["local_time"] == "17:40")
        assert countdown["lot_id"] == lot.id
        assert countdown["platform"] == "FB + IG Stories"
        assert countdown["slot_key"] == f"pushcal:12:{lot.id}:1740"
        assert countdown["facebook_schedulable"] is True
        assert countdown["publish_target"] == "facebook_page"
        assert client.get("/facebook/posts").json() == []

        denied = client.post(
            f"/engagement/sales/12/calendar/facebook",
            json={"confirm": False, "slot_keys": [slot["slot_key"]]},
        )
        assert denied.status_code == 400

        captured_unix: list[int] = []

        def fake_feed(*, page_id, page_token, message, media_fbids, scheduled_unix=None):
            assert scheduled_unix
            captured_unix.append(int(scheduled_unix))
            return "111_cal"

        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"\xff\xd8\xffjpeg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="m-cal"),
            patch("app.services.facebook_page.create_page_feed_post", fake_feed),
        ):
            approved = client.post(
                "/engagement/sales/12/calendar/facebook",
                json={"confirm": True, "slot_keys": [slot["slot_key"]]},
            )
        assert approved.status_code == 200, approved.text
        row = approved.json()["results"][0]
        assert row["ok"] is True
        assert row["status"] == "scheduled"
        assert captured_unix
        again = client.get("/engagement/sales/12/calendar").json()
        fb = next(r for r in again if r["platform"] == "Facebook")
        assert fb["facebook_status"] == "scheduled"
        assert fb["facebook_status_label"] == "Scheduled"
        assert fb["facebook_can_cancel"] is True
        assert fb["facebook_schedulable"] is False
        assert "Already scheduled" in (fb["facebook_blocked_reason"] or "")
        assert fb["facebook_post_id"] == "111_cal"
        still_open = next(r for r in again if r["local_time"] == "17:40")
        assert still_open["facebook_status"] == "planned"
        assert still_open["facebook_schedulable"] is True
        dup = client.post(
            "/engagement/sales/12/calendar/facebook",
            json={"confirm": True, "slot_keys": [slot["slot_key"]]},
        )
        assert dup.json()["results"][0]["skipped"] is True
    finally:
        _clear_overrides()


def test_push_calendar_reschedule_and_removal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, db, account, _user, lot = _client(tmp_path, connected=True)
    updates: list[int] = []
    deletes: list[str] = []

    def fake_feed(*, page_id, page_token, message, media_fbids, scheduled_unix=None):
        return "111_move"

    def fake_update(*, post_id, page_token, scheduled_unix):
        updates.append(int(scheduled_unix))

    def fake_delete(*, post_id, page_token):
        deletes.append(post_id)

    try:
        body = _future_push_body(lot)
        assert client.post("/integrations/engagement/snapshots", json=body).status_code == 200
        slot = next(
            row for row in client.get("/engagement/sales/12/calendar").json() if row["platform"] == "Facebook"
        )
        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="m"),
            patch("app.services.facebook_page.create_page_feed_post", fake_feed),
        ):
            assert (
                client.post(
                    "/engagement/sales/12/calendar/facebook",
                    json={"confirm": True, "slot_keys": [slot["slot_key"]]},
                ).status_code
                == 200
            )
        later = dict(body)
        later["captured_at"] = (
            datetime.fromisoformat(body["captured_at"]) + timedelta(days=1)
        ).isoformat()
        with (
            patch("app.services.facebook_calendar.update_scheduled_page_post", fake_update),
            patch("app.services.facebook_calendar.delete_page_post", fake_delete),
        ):
            moved = client.post("/integrations/engagement/snapshots", json=later)
        assert moved.status_code == 200, moved.text
        assert updates
        rescued = dict(later)
        rescued["items"] = [
            {
                **body["items"][0],
                "watchers": 0,
                "bids": 0,
                "views": 1,
            }
        ]
        with (
            patch("app.services.facebook_calendar.update_scheduled_page_post", fake_update),
            patch("app.services.facebook_calendar.delete_page_post", fake_delete),
        ):
            gone = client.post("/integrations/engagement/snapshots", json=rescued)
        assert gone.status_code == 200, gone.text
        assert "111_move" in deletes
        stored = db.query(FacebookPagePost).one()
        assert stored.status == "cancelled"
    finally:
        _clear_overrides()


def test_push_calendar_auto_approve_off_by_default(tmp_path, monkeypatch) -> None:
    from app import facebook_post_tasks

    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    monkeypatch.setattr(facebook_post_tasks, "SYNC_LOT_LIMIT", 80)
    client, db, account, _user, lot = _client(tmp_path, connected=True)
    feeds: list[str] = []

    def fake_feed(*, page_id, page_token, message, media_fbids, scheduled_unix=None):
        feeds.append("called")
        return "111_auto"

    try:
        status = client.get("/integrations/facebook/status").json()
        assert status["calendar_auto_approve"] is False
        with patch("app.services.facebook_page.create_page_feed_post", fake_feed):
            client.post("/integrations/engagement/snapshots", json=_future_push_body(lot))
        assert feeds == []
        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="m"),
            patch("app.services.facebook_page.create_page_feed_post", fake_feed),
        ):
            patched = client.patch(
                "/integrations/facebook/settings",
                json={"calendar_auto_approve": True},
            )
        assert patched.status_code == 200, patched.text
        assert patched.json()["calendar_auto_approve"] is True
        assert feeds
        scheduled = db.query(FacebookPagePost).filter(FacebookPagePost.status == "scheduled").all()
        assert {row.calendar_key for row in scheduled} == {
            f"pushcal:12:{lot.id}:0900",
            f"pushcal:12:{lot.id}:1740",
        }
    finally:
        _clear_overrides()


def _add_push_lot(db, account, user, sibling: Lot, *, lot_id: str, sku: str, item_id: int) -> Lot:
    lot = Lot(
        id=lot_id,
        account_id=account.id,
        user_id=user.id,
        sale_id=sibling.sale_id,
        sku=sku,
        auction_title=f"{sku} lot",
        professional_description="Cataloged.",
        ctseller_item_id=item_id,
        ctseller_sale_id=12,
    )
    hero = Photo(
        id=f"photo-{lot_id}",
        account_id=account.id,
        user_id=user.id,
        lot_id=lot.id,
        original_name=f"{sku}.jpg",
        storage_path=f"catalog/{sku}.jpg",
        photo_role="hero",
        sort_order=0,
        shows_whole_lot=True,
    )
    db.add_all([lot, hero])
    db.commit()
    return lot


def test_push_calendar_every_platform_slot_is_a_page_target(tmp_path, monkeypatch) -> None:
    from app import facebook_post_tasks

    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    monkeypatch.setattr(settings, "google_api_key", "")
    monkeypatch.setattr(facebook_post_tasks, "SYNC_LOT_LIMIT", 80)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    extras = [
        _add_push_lot(db, account, user, lot, lot_id="lot-ig", sku="AA101", item_id=101),
        _add_push_lot(db, account, user, lot, lot_id="lot-st", sku="AA102", item_id=102),
        _add_push_lot(db, account, user, lot, lot_id="lot-tt", sku="AA103", item_id=103),
    ]
    body = _future_push_body(lot)
    body["items"] = [
        body["items"][0],
        *[
            {
                "ctseller_item_id": extra.ctseller_item_id,
                "sku": extra.sku,
                "title": extra.auction_title,
                "views": 20,
                "watchers": 6,
                "bids": 2,
                "price": 8,
                "reserve": 5,
                "item_url": f"https://ctbids.com/estate-sale/12/item/{extra.ctseller_item_id}",
                "item_status": "Ready",
            }
            for extra in extras
        ],
    ]
    try:
        assert client.post("/integrations/engagement/snapshots", json=body).status_code == 200
        calendar = client.get("/engagement/sales/12/calendar").json()
        by_time = {row["local_time"]: row for row in calendar if row["local_time"] != "17:40"}
        assert by_time["09:00"]["platform"] == "Facebook"
        assert by_time["12:15"]["platform"] == "Instagram"
        assert by_time["16:30"]["platform"] == "FB + IG Stories"
        assert by_time["18:00"]["platform"] == "TikTok"
        for row in calendar:
            assert row["publish_target"] == "facebook_page"
            assert row["facebook_schedulable"] is True
            assert row["slot_key"].endswith(f":{slot_id_from_local_time(row['local_time'])}")
            assert row["facebook_status"] == "planned"
        preview = client.post(
            "/engagement/sales/12/calendar/facebook/preview",
            json={"slot_keys": [by_time["12:15"]["slot_key"], by_time["18:00"]["slot_key"]]},
        )
        assert preview.status_code == 200, preview.text
        captions = [row["caption"] for row in preview.json()["lots"]]
        assert len(captions) == 2
        assert "Midday spotlight" in captions[0]
        assert "Would you bid on this?" in captions[1]
        messages: list[str] = []

        def fake_feed(*, page_id, page_token, message, media_fbids, scheduled_unix=None):
            messages.append(message)
            return f"111_{len(messages)}"

        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="m"),
            patch("app.services.facebook_page.create_page_feed_post", fake_feed),
        ):
            approved = client.post(
                "/engagement/sales/12/calendar/facebook",
                json={
                    "confirm": True,
                    "slot_keys": [by_time["12:15"]["slot_key"], by_time["18:00"]["slot_key"]],
                },
            )
        assert approved.status_code == 200, approved.text
        assert all(row["ok"] for row in approved.json()["results"])
        assert len(messages) == 2
        assert messages[0] != messages[1]
    finally:
        _clear_overrides()


def test_push_calendar_same_lot_second_slot_is_not_a_duplicate(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    monkeypatch.setattr(settings, "google_api_key", "")
    client, db, account, user, lot = _client(tmp_path, connected=True)
    extra = Photo(
        id="photo-detail",
        account_id=account.id,
        user_id=user.id,
        lot_id=lot.id,
        original_name="detail.jpg",
        storage_path="catalog/detail.jpg",
        photo_role="angle_only",
        sort_order=2,
    )
    third = Photo(
        id="photo-three",
        account_id=account.id,
        user_id=user.id,
        lot_id=lot.id,
        original_name="three.jpg",
        storage_path="catalog/three.jpg",
        photo_role="angle_only",
        sort_order=3,
    )
    db.add_all([extra, third])
    db.commit()
    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        calendar = client.get("/engagement/sales/12/calendar").json()
        morning = next(row for row in calendar if row["local_time"] == "09:00")
        closing = next(row for row in calendar if row["local_time"] == "17:40")
        preview = client.post(
            "/engagement/sales/12/calendar/facebook/preview",
            json={"slot_keys": [morning["slot_key"], closing["slot_key"]]},
        ).json()
        by_key = {row["calendar_key"]: row for row in preview["lots"]}
        assert by_key[morning["slot_key"]]["photo_ids"][0] == "photo-hero"
        assert by_key[closing["slot_key"]]["photo_ids"][0] == "photo-detail"
        assert by_key[morning["slot_key"]]["caption"] != by_key[closing["slot_key"]]["caption"]
        def fake_feed(*, page_id, page_token, message, media_fbids, scheduled_unix=None):
            return f"111_{message[:8]}"

        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
            patch("app.services.facebook_page.upload_unpublished_photo", side_effect=["m0", "m1", "m2", "m3", "m4", "m5"]),
            patch("app.services.facebook_page.create_page_feed_post", fake_feed),
        ):
            first = client.post(
                "/engagement/sales/12/calendar/facebook",
                json={"confirm": True, "slot_keys": [morning["slot_key"]]},
            )
            second = client.post(
                "/engagement/sales/12/calendar/facebook",
                json={"confirm": True, "slot_keys": [closing["slot_key"]]},
            )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["results"][0]["ok"] is True
        assert second.json()["results"][0]["ok"] is True
        assert second.json()["results"][0]["skipped"] is False
        keys = {row.calendar_key for row in db.query(FacebookPagePost).all()}
        assert keys == {morning["slot_key"], closing["slot_key"]}
        stored = {row.calendar_key: row.photo_ids_json for row in db.query(FacebookPagePost).all()}
        assert stored[morning["slot_key"]].startswith('["photo-hero"')
        assert stored[closing["slot_key"]].startswith('["photo-detail"')
    finally:
        _clear_overrides()


def test_legacy_calendar_key_still_matches_0900_slot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, db, account, user, lot = _client(tmp_path, connected=True)
    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        db.add(
            FacebookPagePost(
                account_id=account.id,
                user_id=user.id,
                lot_id=lot.id,
                lot_ids_json=f'["{lot.id}"]',
                photo_ids_json='["photo-hero"]',
                facebook_page_id="111",
                facebook_post_id="111_legacy",
                caption="Collector find",
                source="push_calendar",
                calendar_key=f"pushcal:12:{lot.id}",
                calendar_sale_id=12,
                status="scheduled",
                scheduled_publish_time=datetime.now(timezone.utc) + timedelta(days=4),
            )
        )
        db.commit()
        calendar = client.get("/engagement/sales/12/calendar").json()
        morning = next(row for row in calendar if row["local_time"] == "09:00")
        assert morning["slot_key"] == f"pushcal:12:{lot.id}:0900"
        assert morning["facebook_status"] == "scheduled"
        assert morning["facebook_post_id"] == "111_legacy"
        assert morning["facebook_schedulable"] is False
        dup = client.post(
            "/engagement/sales/12/calendar/facebook",
            json={"confirm": True, "slot_keys": [morning["slot_key"]]},
        )
        assert dup.json()["results"][0]["skipped"] is True
    finally:
        _clear_overrides()


def test_push_calendar_missed_slot_is_skipped(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, db, _account, _user, lot = _client(tmp_path, connected=True)
    from zoneinfo import ZoneInfo

    pacific = ZoneInfo("America/Los_Angeles")
    captured = (datetime.now(pacific) - timedelta(days=1)).replace(
        hour=15, minute=0, second=0, microsecond=0
    )
    ends = captured + timedelta(days=5)
    body = {
        "ctseller_sale_id": 12,
        "sale_title": "Estate",
        "auction_url": "https://ctbids.com/estate-sale/12/",
        "sale_ends_at": ends.isoformat(),
        "captured_at": captured.isoformat(),
        "items": _future_push_body(lot)["items"],
    }
    try:
        assert client.post("/integrations/engagement/snapshots", json=body).status_code == 200
        calendar = client.get("/engagement/sales/12/calendar").json()
        morning = next(row for row in calendar if row["local_time"] == "09:00")
        assert morning["facebook_status"] == "missed"
        assert morning["facebook_status_label"] == "Missed"
        assert morning["facebook_schedulable"] is False
        assert morning["facebook_can_cancel"] is False
        assert "already passed" in (morning["facebook_blocked_reason"] or "").lower()
        skipped = client.post(
            "/engagement/sales/12/calendar/facebook",
            json={"confirm": True, "slot_keys": [morning["slot_key"]]},
        )
        assert skipped.status_code == 200, skipped.text
        assert skipped.json()["results"][0]["skipped"] is True
        assert db.query(FacebookPagePost).count() == 0
    finally:
        _clear_overrides()


def test_ctbids_sale_url_strips_item_and_slash() -> None:
    from app.services.ctbids_urls import ctbids_sale_url, sale_link_line

    assert ctbids_sale_url(item_url="https://ctbids.com/estate-sale/48212/item/5667527") == (
        "https://ctbids.com/estate-sale/48212"
    )
    assert ctbids_sale_url(auction_url="https://ctbids.com/estate-sale/48212/") == (
        "https://ctbids.com/estate-sale/48212"
    )
    assert ctbids_sale_url(sale_id=12) == "https://ctbids.com/estate-sale/12"
    assert sale_link_line("https://ctbids.com/estate-sale/12/") == (
        "See the whole sale: https://ctbids.com/estate-sale/12"
    )


def test_calendar_status_too_soon_label() -> None:
    from app.services.facebook_calendar import facebook_blocked_reason, facebook_status_label

    assert facebook_status_label("too_soon") == "Too soon (under 10 min)"
    assert "10 min" in (facebook_blocked_reason("too_soon", schedulable=False) or "")
    assert facebook_blocked_reason("planned", schedulable=True) is None


def test_owner_can_unapprove_scheduled_calendar_slot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    monkeypatch.setattr(settings, "google_api_key", "")
    client, db, _account, _user, lot = _client(tmp_path, connected=True)
    deletes: list[str] = []

    def fake_feed(*, page_id, page_token, message, media_fbids, scheduled_unix=None):
        assert "https://ctbids.com/estate-sale/12/item/99" in message
        assert "See the whole sale: https://ctbids.com/estate-sale/12" in message
        assert message.index("https://ctbids.com/estate-sale/12/item/99") < message.index(
            "See the whole sale:"
        )
        return "111_unapprove"

    def fake_delete(*, post_id, page_token):
        deletes.append(post_id)

    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        slot = next(
            row for row in client.get("/engagement/sales/12/calendar").json() if row["local_time"] == "09:00"
        )
        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="m"),
            patch("app.services.facebook_page.create_page_feed_post", fake_feed),
        ):
            approved = client.post(
                "/engagement/sales/12/calendar/facebook",
                json={"confirm": True, "slot_keys": [slot["slot_key"]]},
            )
        assert approved.status_code == 200, approved.text
        with patch("app.services.facebook_calendar.delete_page_post", fake_delete):
            cancelled = client.post(
                "/engagement/sales/12/calendar/facebook/cancel",
                json={"slot_keys": [slot["slot_key"]]},
            )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["results"][0]["ok"] is True
        assert cancelled.json()["results"][0]["status"] == "cancelled"
        assert "111_unapprove" in deletes
        again = next(
            row for row in client.get("/engagement/sales/12/calendar").json() if row["local_time"] == "09:00"
        )
        assert again["facebook_status"] == "cancelled"
        assert again["facebook_status_label"] == "Cancelled"
        assert again["facebook_can_cancel"] is False
        assert again["facebook_schedulable"] is True
        stored = db.query(FacebookPagePost).one()
        assert stored.status == "cancelled"
    finally:
        _clear_overrides()


def test_catalogger_cannot_unapprove_calendar_slot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    client, _db, _account, _user, _lot = _client(tmp_path, role="catalogger", connected=True)
    try:
        res = client.post(
            "/engagement/sales/12/calendar/facebook/cancel",
            json={"slot_keys": ["pushcal:12:lot-fb:0900"]},
        )
        assert res.status_code == 403
    finally:
        _clear_overrides()
