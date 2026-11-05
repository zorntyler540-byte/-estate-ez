"""P0 safety for the Push calendar -> Facebook Page publisher. Graph HTTP is always mocked."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app import facebook_post_tasks
from app.config import settings
from app.models import FacebookPagePost
from app.services.facebook_calendar import (
    AUTO_APPROVE_MAX_ATTEMPTS,
    INTERRUPTED_NOTE,
    cancel_calendar_slots,
    sweep_calendar_auto_approve,
    sync_facebook_calendar_after_snapshot,
)
from app.services.meta_graph import MetaGraphError
from tests.test_facebook_page import _add_push_lot, _clear_overrides, _client, _future_push_body

UTC = timezone.utc


def _meta(monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    monkeypatch.setattr(settings, "google_api_key", "")


def _morning(client) -> dict:
    return next(row for row in client.get("/engagement/sales/12/calendar").json() if row["local_time"] == "09:00")


def _approve(client, key: str, feed_id: str = "111_live"):
    with (
        patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
        patch("app.services.facebook_page.upload_unpublished_photo", return_value="m"),
        patch("app.services.facebook_page.create_page_feed_post", return_value=feed_id),
    ):
        res = client.post("/engagement/sales/12/calendar/facebook", json={"confirm": True, "slot_keys": [key]})
    assert res.status_code == 200, res.text
    return res.json()["results"]


class _GraphSpy:
    """Fails the test's expectations if any Graph call happens; records them instead."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, path, **_kwargs):
        self.calls.append((method, path))
        return {"id": "unexpected"}


def test_live_scheduled_post_is_marked_posted_not_deleted(tmp_path, monkeypatch) -> None:
    """Repro 1 from the review: after slot time the sync used to DELETE the live post."""
    _meta(monkeypatch)
    client, db, account, _user, lot = _client(tmp_path, connected=True)
    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        slot = _morning(client)
        assert _approve(client, slot["slot_key"])[0]["status"] == "scheduled"
        row = db.query(FacebookPagePost).one()
        after_slot = row.scheduled_publish_time.replace(tzinfo=UTC) + timedelta(hours=1)
        spy = _GraphSpy()
        with patch("app.services.meta_graph.graph_request", spy):
            sync_facebook_calendar_after_snapshot(db, account.id, 12, now=after_slot)
        assert spy.calls == []
        db.refresh(row)
        assert row.status == "posted"
        assert row.facebook_post_id == "111_live"
        assert row.posted_at is not None
    finally:
        _clear_overrides()


def test_next_day_snapshot_never_touches_a_live_post(tmp_path, monkeypatch) -> None:
    """Repro 2: a next-day snapshot matched the live post via the undated alias and moved it."""
    _meta(monkeypatch)
    client, db, account, _user, lot = _client(tmp_path, connected=True)
    try:
        body = _future_push_body(lot)
        assert client.post("/integrations/engagement/snapshots", json=body).status_code == 200
        slot = _morning(client)
        _approve(client, slot["slot_key"])
        row = db.query(FacebookPagePost).one()
        original = (row.calendar_key, row.scheduled_publish_time)
        next_day = dict(body)
        next_day["captured_at"] = (datetime.fromisoformat(body["captured_at"]) + timedelta(days=1)).isoformat()
        spy = _GraphSpy()
        with patch("app.services.meta_graph.graph_request", spy):
            assert client.post("/integrations/engagement/snapshots", json=next_day).status_code == 200
            after_slot = row.scheduled_publish_time.replace(tzinfo=UTC) + timedelta(days=1, hours=2)
            sync_facebook_calendar_after_snapshot(db, account.id, 12, now=after_slot)
        assert spy.calls == []
        db.refresh(row)
        assert (row.calendar_key, row.scheduled_publish_time) == original
        assert row.status == "posted"
    finally:
        _clear_overrides()


def test_approval_survives_a_rank_reshuffle(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    rival = _add_push_lot(db, account, user, lot, lot_id="lot-rival", sku="AA200", item_id=200)
    body = _future_push_body(lot)
    rival_item = {
        **body["items"][0],
        "ctseller_item_id": 200,
        "sku": rival.sku,
        "title": rival.auction_title,
        "item_url": "https://ctbids.com/estate-sale/12/item/200",
        "watchers": 0,
        "bids": 0,
    }
    try:
        first = {**body, "items": [body["items"][0], rival_item]}
        assert client.post("/integrations/engagement/snapshots", json=first).status_code == 200
        slot = _morning(client)
        assert slot["lot_id"] == lot.id
        _approve(client, slot["slot_key"])

        # The rival now outranks the approved lot; without pinning it would take 09:00.
        hot = {**rival_item, "watchers": 12, "bids": 2}
        second = {
            **body,
            "captured_at": (datetime.fromisoformat(body["captured_at"]) + timedelta(minutes=5)).isoformat(),
            "items": [body["items"][0], hot],
        }
        spy = _GraphSpy()
        with patch("app.services.meta_graph.graph_request", spy):
            assert client.post("/integrations/engagement/snapshots", json=second).status_code == 200
        assert spy.calls == []
        calendar = client.get("/engagement/sales/12/calendar").json()
        same_cell = [r for r in calendar if r["post_date"] == slot["post_date"] and r["local_time"] == "09:00"]
        assert len(same_cell) == 1
        assert same_cell[0]["lot_id"] == lot.id
        assert same_cell[0]["slot_key"] == slot["slot_key"]
        assert same_cell[0]["pinned"] is True
        assert same_cell[0]["facebook_status"] == "scheduled"
        assert any(r["lot_id"] == rival.id for r in calendar)
        assert db.query(FacebookPagePost).one().calendar_key == slot["slot_key"]
    finally:
        _clear_overrides()


def test_empty_slot_keys_post_nothing(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, _account, _user, lot = _client(tmp_path, connected=True)
    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        spy = _GraphSpy()
        with patch("app.services.meta_graph.graph_request", spy):
            for post_now in (False, True):
                res = client.post(
                    "/engagement/sales/12/calendar/facebook",
                    json={"confirm": True, "post_now": post_now, "slot_keys": []},
                )
                assert res.status_code == 400
                blank = client.post(
                    "/engagement/sales/12/calendar/facebook",
                    json={"confirm": True, "post_now": post_now, "slot_keys": ["", "  "]},
                )
                assert blank.status_code == 400
            preview = client.post("/engagement/sales/12/calendar/facebook/preview", json={"slot_keys": []})
            assert preview.status_code == 400
        assert spy.calls == []
        assert db.query(FacebookPagePost).count() == 0
    finally:
        _clear_overrides()


def test_unknown_slot_key_comes_back_as_a_skipped_row(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, _db, _account, _user, lot = _client(tmp_path, connected=True)
    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        gone = f"pushcal:12:{lot.id}:20990101:0900"
        res = client.post("/engagement/sales/12/calendar/facebook", json={"confirm": True, "slot_keys": [gone]})
        assert res.status_code == 200, res.text
        rows = res.json()["results"]
        assert len(rows) == 1 and rows[0]["skipped"] is True
        assert "no longer on the Push calendar" in rows[0]["error"]
    finally:
        _clear_overrides()


def test_claimed_slot_is_never_double_posted(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        slot = _morning(client)
        db.add(
            FacebookPagePost(
                account_id=account.id,
                user_id=user.id,
                lot_id=lot.id,
                source="push_calendar",
                calendar_key=slot["slot_key"],
                calendar_sale_id=12,
                status="publishing",
                claimed_at=datetime.now(UTC),
            )
        )
        db.commit()
        feeds: list[str] = []
        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="m"),
            patch("app.services.facebook_page.create_page_feed_post", side_effect=lambda **_k: feeds.append("x") or "1"),
        ):
            res = client.post(
                "/engagement/sales/12/calendar/facebook",
                json={"confirm": True, "post_now": True, "slot_keys": [slot["slot_key"]]},
            )
            assert res.json()["results"][0]["skipped"] is True

            # Race: the publisher's lookup missed the other claim; the unique index stops it.
            with patch("app.services.facebook_calendar.calendar_row_for_key", return_value=None):
                from app.services.facebook_page import publish_posts

                raced = publish_posts(
                    db,
                    account=account,
                    user=user,
                    items=[
                        {
                            "lot_id": lot.id,
                            "caption": "Second publisher",
                            "calendar_key": slot["slot_key"],
                            "calendar_sale_id": 12,
                        }
                    ],
                    include_follow_hook=False,
                    confirm=True,
                    force=True,
                    source="push_calendar",
                )
        assert raced[0]["skipped"] is True
        assert "already being posted" in raced[0]["error"]
        assert feeds == []
        assert db.query(FacebookPagePost).count() == 1
    finally:
        _clear_overrides()


def test_force_cannot_repost_a_posted_calendar_slot(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, _account, _user, lot = _client(tmp_path, connected=True)
    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        slot = _morning(client)
        _approve(client, slot["slot_key"])
        feeds: list[str] = []
        with patch(
            "app.services.facebook_page.create_page_feed_post", side_effect=lambda **_k: feeds.append("x") or "2"
        ):
            res = client.post(
                "/engagement/sales/12/calendar/facebook",
                json={"confirm": True, "force": True, "post_now": True, "slot_keys": [slot["slot_key"]]},
            )
        assert res.json()["results"][0]["skipped"] is True
        assert feeds == []
    finally:
        _clear_overrides()


def test_auto_approve_backs_off_then_stops_after_the_cap(tmp_path, monkeypatch) -> None:
    from app.services import facebook_calendar

    _meta(monkeypatch)
    # Page feed retries only: reserved Story slots would fill this one-lot calendar's quota.
    monkeypatch.setitem(facebook_calendar.PLATFORM_PUBLISH_TARGETS, "FB + IG Stories", "copy_only")
    client, db, account, _user, lot = _client(tmp_path, connected=True)
    account.facebook_calendar_auto_approve = True
    db.add(account)
    db.commit()
    attempts: list[str] = []

    def failing_feed(**_kwargs):
        attempts.append("x")
        raise MetaGraphError("(#100) Something went wrong", code=100)

    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        t0 = datetime.now(UTC)
        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="m"),
            patch("app.services.facebook_page.create_page_feed_post", failing_feed),
        ):
            sweep_calendar_auto_approve(db, now=t0)
            slots = len(attempts)
            assert slots >= 1
            sweep_calendar_auto_approve(db, now=t0 + timedelta(minutes=1))
            assert len(attempts) == slots, "no retry inside the backoff window"
            sweep_calendar_auto_approve(db, now=t0 + timedelta(minutes=11))
            assert len(attempts) == 2 * slots
            sweep_calendar_auto_approve(db, now=t0 + timedelta(minutes=11 + 21))
            assert len(attempts) == 3 * slots
            for hours in (1, 6, 24):
                sweep_calendar_auto_approve(db, now=t0 + timedelta(hours=hours))
            assert len(attempts) == 3 * slots, "capped"
        rows = db.query(FacebookPagePost).all()
        assert len(rows) == slots
        for row in rows:
            assert row.status == "failed"
            assert row.attempt_count == AUTO_APPROVE_MAX_ATTEMPTS
            assert "Stopped retrying" in (row.review_note or "")
    finally:
        _clear_overrides()


def test_auto_approve_never_reposts_an_owner_cancelled_slot(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, account, _user, lot = _client(tmp_path, connected=True)
    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        slot = _morning(client)
        _approve(client, slot["slot_key"], feed_id="111_cancel_me")
        with patch("app.services.facebook_calendar.delete_page_post"):
            res = client.post("/engagement/sales/12/calendar/facebook/cancel", json={"slot_keys": [slot["slot_key"]]})
        assert res.json()["results"][0]["ok"] is True
        account.facebook_calendar_auto_approve = True
        db.add(account)
        db.commit()
        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="m"),
            patch("app.services.facebook_page.create_page_feed_post", return_value="111_other"),
        ):
            sweep_calendar_auto_approve(db)
        cancelled = db.query(FacebookPagePost).filter(FacebookPagePost.calendar_key == slot["slot_key"]).one()
        assert cancelled.status == "cancelled"
        assert cancelled.facebook_post_id == "111_cancel_me"
    finally:
        _clear_overrides()


def test_auto_approve_skips_a_dead_page_token(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, account, _user, lot = _client(tmp_path, connected=True)
    account.facebook_calendar_auto_approve = True
    account.facebook_token_error = "Error validating access token. Reconnect the Facebook Page."
    db.add(account)
    db.commit()
    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        spy = _GraphSpy()
        with patch("app.services.meta_graph.graph_request", spy):
            assert sweep_calendar_auto_approve(db) == 0
            patched = client.patch("/integrations/facebook/settings", json={"calendar_auto_approve": True})
            assert patched.status_code == 200
            assert patched.json()["needs_reconnect"] is True
        assert spy.calls == []
        assert db.query(FacebookPagePost).count() == 0
    finally:
        _clear_overrides()


def test_token_error_mid_batch_reports_every_item(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    second = _add_push_lot(db, account, user, lot, lot_id="lot-second", sku="AA300", item_id=300)

    def dead(**_kwargs):
        raise MetaGraphError("Error validating access token", code=190, is_token_error=True)

    try:
        from app.services.facebook_page import publish_posts

        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
            patch("app.services.facebook_page.upload_unpublished_photo", side_effect=dead),
        ):
            results = publish_posts(
                db,
                account=account,
                user=user,
                items=[{"lot_id": lot.id, "caption": "one"}, {"lot_id": second.id, "caption": "two"}],
                include_follow_hook=False,
                confirm=True,
                force=False,
                source="push_queue",
            )
        assert [r["lot_id"] for r in results] == [lot.id, second.id]
        assert results[0]["needs_reconnect"] is True
        assert results[1]["skipped"] is True and results[1]["needs_reconnect"] is True
        db.refresh(account)
        assert account.facebook_token_error
    finally:
        _clear_overrides()


def test_too_soon_schedule_is_a_row_not_a_batch_crash(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    try:
        from app.services.facebook_page import publish_posts

        feeds: list[str] = []
        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="m"),
            patch("app.services.facebook_page.create_page_feed_post", side_effect=lambda **_k: feeds.append("x") or "9"),
        ):
            results = publish_posts(
                db,
                account=account,
                user=user,
                items=[
                    {
                        "lot_id": lot.id,
                        "caption": "too soon",
                        "scheduled_publish_time": datetime.now(UTC) + timedelta(minutes=3),
                    }
                ],
                include_follow_hook=False,
                confirm=True,
                force=False,
                source="push_queue",
            )
        assert results[0]["skipped"] is True
        assert "10 minutes" in results[0]["error"]
        assert feeds == []
    finally:
        _clear_overrides()


def test_stale_publishing_claim_fails_safe(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        slot = _morning(client)
        row = FacebookPagePost(
            account_id=account.id,
            user_id=user.id,
            lot_id=lot.id,
            source="push_calendar",
            calendar_key=slot["slot_key"],
            calendar_sale_id=12,
            status="publishing",
            claimed_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db.add(row)
        account.facebook_calendar_auto_approve = True
        db.add(account)
        db.commit()
        feeds: list[str] = []
        with (
            patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
            patch("app.services.facebook_page.upload_unpublished_photo", return_value="m"),
            patch("app.services.facebook_page.create_page_feed_post", side_effect=lambda **_k: feeds.append("x") or "3"),
        ):
            sweep_calendar_auto_approve(db)
        db.refresh(row)
        assert row.status == "failed"
        assert row.review_note == INTERRUPTED_NOTE
        assert row.attempt_count >= AUTO_APPROVE_MAX_ATTEMPTS
        assert db.query(FacebookPagePost).filter(FacebookPagePost.calendar_key == slot["slot_key"]).count() == 1
        # Other slots may auto-post; this one must not.
        assert db.query(FacebookPagePost).filter(FacebookPagePost.id == row.id).one().facebook_post_id is None
    finally:
        _clear_overrides()


def test_owner_cannot_unapprove_a_live_post(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, account, _user, lot = _client(tmp_path, connected=True)
    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        slot = _morning(client)
        _approve(client, slot["slot_key"])
        row = db.query(FacebookPagePost).one()
        live = row.scheduled_publish_time.replace(tzinfo=UTC) + timedelta(minutes=5)
        with patch("app.services.facebook_calendar.delete_page_post") as delete:
            results = cancel_calendar_slots(
                db, account=account, ctseller_sale_id=12, slot_keys=[slot["slot_key"]], now=live
            )
        delete.assert_not_called()
        assert results[0]["skipped"] is True
        assert "already live" in results[0]["error"]
        db.refresh(row)
        assert row.status == "posted"
    finally:
        _clear_overrides()


def test_lot_leaving_the_sale_flags_instead_of_deleting(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    other = _add_push_lot(db, account, user, lot, lot_id="lot-stays", sku="AA400", item_id=400)
    body = _future_push_body(lot)
    other_item = {
        **body["items"][0],
        "ctseller_item_id": 400,
        "sku": other.sku,
        "item_url": "https://ctbids.com/estate-sale/12/item/400",
    }
    try:
        full = {**body, "items": [body["items"][0], other_item], "complete": True}
        assert client.post("/integrations/engagement/snapshots", json=full).status_code == 200
        slot = next(
            r for r in client.get("/engagement/sales/12/calendar").json() if r["lot_id"] == lot.id and r["local_time"] == "09:00"
        )
        _approve(client, slot["slot_key"])
        pruned = {
            **body,
            "captured_at": (datetime.fromisoformat(body["captured_at"]) + timedelta(minutes=5)).isoformat(),
            "items": [other_item],
            "complete": True,
        }
        spy = _GraphSpy()
        with patch("app.services.meta_graph.graph_request", spy):
            assert client.post("/integrations/engagement/snapshots", json=pruned).status_code == 200
        assert spy.calls == []
        row = db.query(FacebookPagePost).one()
        assert row.status == "scheduled"
        assert row.review_note
        shown = next(r for r in client.get("/engagement/sales/12/calendar").json() if r["slot_key"] == slot["slot_key"])
        assert shown["facebook_review_note"] == row.review_note
        assert shown["facebook_can_cancel"] is True
    finally:
        _clear_overrides()


def _alembic(engine, fn, rev: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "alembic"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        fn(cfg, rev)


def test_0051_dedupes_slot_rows_before_the_unique_index(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    _alembic(engine, command.upgrade, "0050_startup_ddl_to_alembic")
    key = "pushcal:12:lot-1:20261020:0900"
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO accounts (id, name, plan_code, is_active) VALUES ('a', 'CT', 'x', 1)"))
        conn.execute(
            text("INSERT INTO users (id, account_id, email, password_hash, role) VALUES ('u', 'a', 'e@x', 'x', 'owner')")
        )
        for row_id, status, stamp in (
            ("old-failed", "failed", "2026-10-01 10:00:00"),
            ("posted", "posted", "2026-10-02 10:00:00"),
            ("new-cancelled", "cancelled", "2026-10-03 10:00:00"),
        ):
            conn.execute(
                text(
                    "INSERT INTO facebook_page_posts (id, account_id, user_id, calendar_key, status, source, "
                    "facebook_page_id, caption, lot_ids_json, photo_ids_json, include_follow_hook, "
                    "created_at, updated_at) VALUES (:id, 'a', 'u', :key, :status, 'push_calendar', '', '', "
                    "'[]', '[]', 0, :ts, :ts)"
                ),
                {"id": row_id, "key": key, "status": status, "ts": stamp},
            )
    _alembic(engine, command.upgrade, "head")
    with engine.connect() as conn:
        keyed = conn.execute(
            text("SELECT id FROM facebook_page_posts WHERE calendar_key = :key"), {"key": key}
        ).fetchall()
        assert [r[0] for r in keyed] == ["posted"]
        notes = conn.execute(
            text("SELECT COUNT(*) FROM facebook_page_posts WHERE calendar_key IS NULL AND review_note IS NOT NULL")
        ).scalar()
        assert notes == 2
