"""Public /go/<code> redirects and Facebook caption short-link generation."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import get_current_user
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Account, Lot, Photo, Sale, TrackedLink, TrackedLinkClick, User
from app.services.click_tracking import (
    CHANNEL_PAGE_POST,
    KIND_LOT,
    click_tracking_in_captions,
    get_or_create_tracked_link,
    is_allowed_destination,
    is_preview_bot,
    reset_rate_limits,
    restore_ctbids_urls,
)
from app.services.facebook_crypto import seal_facebook_secret


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'clicks.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _seed(db):
    account = Account(id="acct-clk", name="CT")
    user = User(
        id="user-clk",
        account_id=account.id,
        email="clk@example.com",
        password_hash="x",
        role="owner",
    )
    sale = Sale(id="sale-clk", account_id=account.id, user_id=user.id, name="Estate")
    lot = Lot(
        id="lot-clk",
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
    db.add_all([account, user, sale, lot, hero])
    db.commit()
    return account, user, sale, lot


def _client(tmp_path, *, connected: bool = True):
    Session = _session(tmp_path)
    db = Session()
    account, user, sale, lot = _seed(db)
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
    return TestClient(app), db, account, user, sale, lot


def _clear() -> None:
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)
    reset_rate_limits()


def test_allowed_destination_rejects_open_redirect_hosts() -> None:
    assert is_allowed_destination("https://ctbids.com/estate-sale/12/item/99")
    assert is_allowed_destination("https://www.ctbids.com/estate-sale/12")
    assert not is_allowed_destination("https://evil.example/phish")
    assert not is_allowed_destination("javascript:alert(1)")
    assert not is_allowed_destination("https://ctbids.com.evil.example/estate-sale/1")


def test_preview_bots_are_filtered() -> None:
    assert is_preview_bot(user_agent="facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)")
    assert is_preview_bot(user_agent="Facebot")
    assert is_preview_bot(user_agent="Twitterbot/1.0")
    assert is_preview_bot(purpose="preview")
    assert not is_preview_bot(user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")


def test_redirect_logs_click_and_ignores_query_destination(tmp_path) -> None:
    client, db, account, _user, _sale, lot = _client(tmp_path)
    try:
        link = get_or_create_tracked_link(
            db,
            account_id=account.id,
            sale_id=lot.sale_id,
            lot_id=lot.id,
            destination_url="https://ctbids.com/estate-sale/12/item/99",
            destination_kind=KIND_LOT,
            channel=CHANNEL_PAGE_POST,
        )
        db.commit()
        assert link is not None
        res = client.get(
            f"/go/{link.code}",
            params={"url": "https://evil.example/phish", "to": "https://evil.example/phish"},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.facebook.com/story.php?id=1&token=secret"},
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert res.headers["location"] == "https://ctbids.com/estate-sale/12/item/99"
        assert "evil.example" not in (res.headers["location"] or "")
        clicks = db.query(TrackedLinkClick).all()
        assert len(clicks) == 1
        row = clicks[0]
        assert row.code == link.code
        assert row.lot_id == lot.id
        assert row.sale_id == lot.sale_id
        assert row.channel == CHANNEL_PAGE_POST
        assert row.destination_url == "https://ctbids.com/estate-sale/12/item/99"
        assert row.referrer == "https://www.facebook.com/story.php"
        assert "secret" not in row.referrer
        assert "token=" not in row.referrer
        assert not hasattr(row, "ip") or getattr(row, "ip", None) in (None, "")
    finally:
        _clear()


def test_unknown_code_is_not_an_open_redirect(tmp_path) -> None:
    client, _db, _account, _user, _sale, _lot = _client(tmp_path)
    try:
        res = client.get(
            "/go/notarealcode1",
            params={"url": "https://evil.example/phish"},
            follow_redirects=False,
        )
        assert res.status_code == 404
        assert "evil.example" not in (res.headers.get("location") or "")
    finally:
        _clear()


def test_facebook_preview_crawler_is_not_counted(tmp_path) -> None:
    client, db, account, _user, _sale, lot = _client(tmp_path)
    try:
        link = get_or_create_tracked_link(
            db,
            account_id=account.id,
            sale_id=lot.sale_id,
            lot_id=lot.id,
            destination_url="https://ctbids.com/estate-sale/12/item/99",
            destination_kind=KIND_LOT,
            channel=CHANNEL_PAGE_POST,
        )
        db.commit()
        res = client.get(
            f"/go/{link.code}",
            headers={"User-Agent": "facebookexternalhit/1.1"},
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert res.headers["location"] == "https://ctbids.com/estate-sale/12/item/99"
        assert db.query(TrackedLinkClick).count() == 0
    finally:
        _clear()


def test_local_and_railway_origins_are_not_printed_in_captions(monkeypatch) -> None:
    monkeypatch.setattr(settings, "public_api_origin", "")
    assert click_tracking_in_captions() is False
    monkeypatch.setattr(settings, "public_api_origin", "http://localhost:8000")
    assert click_tracking_in_captions() is False
    monkeypatch.setattr(
        settings,
        "public_api_origin",
        "https://caringtransitions-ai-production.up.railway.app",
    )
    assert click_tracking_in_captions() is False
    monkeypatch.setattr(settings, "public_api_origin", "https://go.example.com")
    assert click_tracking_in_captions() is True


def test_restore_go_urls_back_to_ctbids(tmp_path) -> None:
    _client_tuple = _client(tmp_path)
    _client_http, db, account, _user, _sale, lot = _client_tuple
    try:
        link = get_or_create_tracked_link(
            db,
            account_id=account.id,
            sale_id=lot.sale_id,
            lot_id=lot.id,
            destination_url="https://ctbids.com/estate-sale/12/item/99",
            destination_kind=KIND_LOT,
            channel=CHANNEL_PAGE_POST,
        )
        db.commit()
        assert link is not None
        leftover = (
            f"Bid here\nhttps://caringtransitions-ai-production.up.railway.app/go/{link.code}\n"
            "See the whole sale: https://ctbids.com/estate-sale/12"
        )
        restored = restore_ctbids_urls(db, leftover)
        assert "/go/" not in restored
        assert "https://ctbids.com/estate-sale/12/item/99" in restored
        assert "railway.app" not in restored
    finally:
        _clear()


def test_publish_restores_leftover_go_urls_to_ctbids(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    monkeypatch.setattr(settings, "google_api_key", "")
    monkeypatch.setattr(settings, "public_api_origin", "")
    client, db, account, _user, _sale, lot = _client(tmp_path)
    published: list[str] = []

    def fake_feed(*, page_id, page_token, message, media_fbids, scheduled_unix=None):
        published.append(message)
        return "111_restore"

    try:
        link = get_or_create_tracked_link(
            db,
            account_id=account.id,
            sale_id=lot.sale_id,
            lot_id=lot.id,
            destination_url="https://ctbids.com/estate-sale/12/item/99",
            destination_kind=KIND_LOT,
            channel=CHANNEL_PAGE_POST,
        )
        db.commit()
        assert link is not None
        leftover = (
            f"Bid here\nhttps://caringtransitions-ai-production.up.railway.app/go/{link.code}\n"
            "See the whole sale: https://ctbids.com/estate-sale/12"
        )
        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"\xff\xd8\xffjpeg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="media-1"),
            patch("app.services.facebook_page.create_page_feed_post", fake_feed),
        ):
            posted = client.post(
                "/facebook/posts",
                json={"confirm": True, "items": [{"lot_id": lot.id, "caption": leftover}]},
            )
        assert posted.status_code == 200, posted.text
        assert posted.json()["results"][0]["ok"] is True
        assert published
        assert "/go/" not in published[0]
        assert "railway.app" not in published[0]
        assert "https://ctbids.com/estate-sale/12/item/99" in published[0]
        assert "See the whole sale: https://ctbids.com/estate-sale/12" in published[0]
    finally:
        _clear()


def test_preview_and_publish_use_ctbids_urls_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    monkeypatch.setattr(settings, "google_api_key", "")
    monkeypatch.setattr(settings, "public_api_origin", "")
    client, db, _account, _user, sale, lot = _client(tmp_path)
    published: list[str] = []

    def fake_feed(*, page_id, page_token, message, media_fbids, scheduled_unix=None):
        published.append(message)
        return "111_clk"

    try:
        preview = client.post("/facebook/posts/preview", json={"lot_ids": [lot.id]})
        assert preview.status_code == 200, preview.text
        row = preview.json()["lots"][0]
        assert "/go/" not in row["caption"]
        assert "https://ctbids.com/estate-sale/" in row["caption"]
        assert "/go/" not in row["group_caption"]
        assert "https://ctbids.com/estate-sale/" in row["group_caption"]
        assert "ctbids.com" in (row["item_url"] or "")
        assert "/go/" not in (row["item_url"] or "")
        db.expire_all()
        assert db.query(TrackedLink).count() == 0

        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"\xff\xd8\xffjpeg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="media-1"),
            patch("app.services.facebook_page.create_page_feed_post", fake_feed),
        ):
            posted = client.post(
                "/facebook/posts",
                json={
                    "confirm": True,
                    "items": [{"lot_id": lot.id, "caption": row["caption"]}],
                },
            )
        assert posted.status_code == 200, posted.text
        assert posted.json()["results"][0]["ok"] is True
        assert published
        assert "/go/" not in published[0]
        assert "https://ctbids.com/estate-sale/" in published[0]

        report = client.get(f"/facebook/clicks?sale_id={sale.id}")
        assert report.status_code == 200
        body = report.json()
        assert body["total"] == 0
        assert body["sale_id"] == sale.id
    finally:
        _clear()


def test_preview_and_publish_use_short_links_when_tracking_host_set(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    monkeypatch.setattr(settings, "google_api_key", "")
    monkeypatch.setattr(settings, "public_api_origin", "https://go.example.com")
    client, db, _account, _user, sale, lot = _client(tmp_path)
    published: list[str] = []

    def fake_feed(*, page_id, page_token, message, media_fbids, scheduled_unix=None):
        published.append(message)
        return "111_clk"

    try:
        preview = client.post("/facebook/posts/preview", json={"lot_ids": [lot.id]})
        assert preview.status_code == 200, preview.text
        row = preview.json()["lots"][0]
        assert "https://go.example.com/go/" in row["caption"]
        assert "ctbids.com" not in row["caption"]
        assert "https://go.example.com/go/" in row["group_caption"]
        assert "ctbids.com" not in row["group_caption"]
        assert "https://go.example.com/go/" in (row["item_url"] or "")
        db.expire_all()
        links = db.query(TrackedLink).all()
        assert len(links) >= 2
        codes = {link.code for link in links}
        assert any(code in row["caption"] for code in codes)

        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"\xff\xd8\xffjpeg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="media-1"),
            patch("app.services.facebook_page.create_page_feed_post", fake_feed),
        ):
            posted = client.post(
                "/facebook/posts",
                json={
                    "confirm": True,
                    "items": [{"lot_id": lot.id, "caption": row["caption"]}],
                },
            )
        assert posted.status_code == 200, posted.text
        assert posted.json()["results"][0]["ok"] is True
        assert published
        assert "https://go.example.com/go/" in published[0]
        assert "ctbids.com" not in published[0]

        report = client.get(f"/facebook/clicks?sale_id={sale.id}")
        assert report.status_code == 200
        body = report.json()
        assert body["total"] == 0
        assert body["sale_id"] == sale.id
    finally:
        _clear()


def test_clicks_report_counts_by_lot_and_day(tmp_path) -> None:
    client, db, account, _user, sale, lot = _client(tmp_path)
    try:
        link = get_or_create_tracked_link(
            db,
            account_id=account.id,
            sale_id=sale.id,
            lot_id=lot.id,
            destination_url="https://ctbids.com/estate-sale/12/item/99",
            destination_kind=KIND_LOT,
            channel=CHANNEL_PAGE_POST,
        )
        db.commit()
        hit = client.get(f"/go/{link.code}", headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=False)
        assert hit.status_code == 302
        report = client.get(f"/facebook/clicks?sale_id={sale.id}").json()
        assert report["total"] == 1
        assert report["by_lot"][0]["sku"] == "AA100"
        assert report["by_lot"][0]["clicks"] == 1
        assert report["by_day"][0]["clicks"] == 1
        assert report["by_post"][0]["clicks"] == 1
    finally:
        _clear()
