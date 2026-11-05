"""Push queue performance and timezone: Gemini once, no DB during Graph, no N+1, open sales only."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import event

from app import engagement_tasks, facebook_post_tasks, worker
from app.config import settings
from app.models import EngagementSnapshot, FacebookPagePost, TrackedLink
from app.services import facebook_caption
from app.services.engagement_store import list_engagement_sales
from app.services.facebook_calendar import open_calendar_sale_ids
from tests.test_facebook_page import _add_push_lot, _clear_overrides, _client, _future_push_body

UTC = timezone.utc


def _meta(monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    monkeypatch.setattr(settings, "google_api_key", "")


def _push_body_for(lots) -> dict:
    body = _future_push_body(lots[0])
    body["items"] = [
        {
            **body["items"][0],
            "ctseller_item_id": int(lot.ctseller_item_id),
            "sku": lot.sku,
            "title": lot.auction_title,
            "item_url": f"https://ctbids.com/estate-sale/12/item/{int(lot.ctseller_item_id)}",
        }
        for lot in lots
    ]
    return body


class _QueryCounter:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.count = 0

    def _hook(self, *_args, **_kwargs) -> None:
        self.count += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hook)
        return self

    def __exit__(self, *_exc) -> None:
        event.remove(self.engine, "before_cursor_execute", self._hook)


def _calendar_queries(client, db) -> tuple[int, list[dict]]:
    with _QueryCounter(db.get_bind()) as counter:
        res = client.get("/engagement/sales/12/calendar")
    assert res.status_code == 200, res.text
    return counter.count, res.json()


def test_calendar_get_is_read_only_and_query_count_does_not_grow_with_lots(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    monkeypatch.setattr(settings, "public_api_origin", "https://go.example.com")
    client, db, account, user, lot = _client(tmp_path, connected=True)
    try:
        assert client.post("/integrations/engagement/snapshots", json=_push_body_for([lot])).status_code == 200
        links_before = db.query(TrackedLink).count()
        assert links_before, "post-snapshot sync creates the Groups links"
        small, posts = _calendar_queries(client, db)
        assert any("go.example.com/go/" in post["group_copy"] for post in posts)
        assert db.query(TrackedLink).count() == links_before, "GET never writes tracked links"

        lots = [lot] + [
            _add_push_lot(db, account, user, lot, lot_id=f"lot-n{i}", sku=f"AB{i:03d}", item_id=200 + i)
            for i in range(6)
        ]
        assert client.post("/integrations/engagement/snapshots", json=_push_body_for(lots)).status_code == 200
        links_after_sync = db.query(TrackedLink).count()
        large, posts = _calendar_queries(client, db)
        assert len({post["lot_id"] for post in posts}) >= 5
        assert db.query(TrackedLink).count() == links_after_sync
        assert large <= small + 2, f"calendar GET ran {large} queries for 7 lots vs {small} for 1"
    finally:
        _clear_overrides()


def test_queue_get_loads_photos_in_one_query(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    try:
        assert client.post("/integrations/engagement/snapshots", json=_push_body_for([lot])).status_code == 200
        with _QueryCounter(db.get_bind()) as one:
            assert client.get("/engagement/sales/12").status_code == 200
        lots = [lot] + [
            _add_push_lot(db, account, user, lot, lot_id=f"lot-q{i}", sku=f"AC{i:03d}", item_id=300 + i)
            for i in range(6)
        ]
        assert client.post("/integrations/engagement/snapshots", json=_push_body_for(lots)).status_code == 200
        with _QueryCounter(db.get_bind()) as many:
            res = client.get("/engagement/sales/12")
        assert res.status_code == 200
        assert all(item["photo_ids"] for item in res.json()["items"])
        assert many.count <= one.count + 1
    finally:
        _clear_overrides()


def test_snapshot_ingest_matches_lots_without_a_query_per_item(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    try:
        lots = [lot] + [
            _add_push_lot(db, account, user, lot, lot_id=f"lot-m{i}", sku=f"AD{i:03d}", item_id=400 + i)
            for i in range(8)
        ]
        monkeypatch.setattr(engagement_tasks, "_start_thread", lambda target, name: None)
        with _QueryCounter(db.get_bind()) as two:
            client.post("/integrations/engagement/snapshots", json=_push_body_for(lots[:2]))
        body = _push_body_for(lots)
        body["captured_at"] = (datetime.fromisoformat(body["captured_at"]) + timedelta(minutes=5)).isoformat()
        with _QueryCounter(db.get_bind()) as nine:
            res = client.post("/integrations/engagement/snapshots", json=body)
        assert res.json()["matched_lots"] == 9
        # Inserts grow with items; lot lookups must not add two SELECTs per item.
        assert nine.count - two.count <= 7 + 2
    finally:
        _clear_overrides()


def test_sales_list_counts_match_the_detail(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    try:
        lots = [lot, _add_push_lot(db, account, user, lot, lot_id="lot-s1", sku="AE001", item_id=501)]
        body = _push_body_for(lots)
        body["items"][1].update({"watchers": 0, "bids": 0})
        assert client.post("/integrations/engagement/snapshots", json=body).status_code == 200
        db.add(
            EngagementSnapshot(
                account_id=account.id,
                ctseller_sale_id=12,
                ctseller_item_id=777,
                sku="LEGACY",
                bucket="Rescue",
                watchers=2,
                bids=0,
                captured_at=datetime.fromisoformat(body["captured_at"]) - timedelta(days=1),
            )
        )
        db.commit()
        [summary] = list_engagement_sales(db, account.id)
        detail = client.get("/engagement/sales/12").json()
        assert summary.item_count == 3
        assert summary.sale_title == "Estate"
        for key in ("push", "needs_eyes", "no_interest", "rescue", "protect", "let_ride"):
            assert getattr(summary, key) == detail[key], key
        assert summary.needs_eyes == 1 and summary.no_interest == 1 and summary.rescue == 2
    finally:
        _clear_overrides()


def test_approve_reuses_the_previewed_caption_without_gemini(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, _account, _user, lot = _client(tmp_path, connected=True)
    calls: list[str] = []

    def counting_drafter(**kwargs):
        calls.append(kwargs["title"])
        return "Drafted caption https://ctbids.com/estate-sale/12/item/99"

    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        slot = next(row for row in client.get("/engagement/sales/12/calendar").json() if row["local_time"] == "09:00")
        with patch("app.services.facebook_calendar.draft_facebook_caption", counting_drafter):
            preview = client.post(
                "/engagement/sales/12/calendar/facebook/preview", json={"slot_keys": [slot["slot_key"]]}
            )
            assert preview.status_code == 200, preview.text
            assert len(calls) == 1
            reviewed = "Owner-edited caption https://ctbids.com/estate-sale/12/item/99"
            feeds: list[str] = []
            with (
                patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
                patch("app.services.facebook_page.upload_unpublished_photo", return_value="m"),
                patch(
                    "app.services.facebook_page.create_page_feed_post",
                    lambda **kw: feeds.append(kw["message"]) or "111_once",
                ),
            ):
                res = client.post(
                    "/engagement/sales/12/calendar/facebook",
                    json={"confirm": True, "slot_keys": [slot["slot_key"]], "captions": {slot["slot_key"]: reviewed}},
                )
        assert res.status_code == 200, res.text
        assert len(calls) == 1, "approve must not call Gemini again"
        assert feeds and feeds[0].startswith("Owner-edited caption")
    finally:
        _clear_overrides()


def test_caption_drafts_overlap() -> None:
    barrier = threading.Barrier(3, timeout=5)

    def slow(**kwargs):
        barrier.wait()
        return kwargs["title"]

    out = facebook_caption.draft_captions([{"title": t} for t in ("a", "b", "c")], drafter=slow)
    assert out == ["a", "b", "c"]


def test_graph_calls_run_without_an_open_db_transaction(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, _account, _user, lot = _client(tmp_path, connected=True)
    seen: list[bool] = []
    real_publish = facebook_post_tasks._run_facebook_posts

    def capture(**kwargs):
        from app.services import facebook_page

        real = facebook_page.publish_posts

        def wrapped(session, **inner):
            def upload(**_kw):
                seen.append(session.in_transaction())
                return "m"

            def feed(**_kw):
                seen.append(session.in_transaction())
                return "111_tx"

            with (
                patch("app.services.facebook_page.upload_unpublished_photo", upload),
                patch("app.services.facebook_page.create_page_feed_post", feed),
            ):
                return real(session, **inner)

        with patch("app.services.facebook_page.publish_posts", wrapped):
            return real_publish(**kwargs)

    monkeypatch.setattr(facebook_post_tasks, "_run_facebook_posts", capture)
    try:
        with patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"):
            res = client.post(
                "/facebook/posts",
                json={"confirm": True, "items": [{"lot_id": lot.id, "caption": "Vase https://ctbids.com/x"}]},
            )
        assert res.status_code == 200, res.text
        assert res.json()["queued"] is True
        assert res.json()["results"][0]["ok"] is True
        assert seen == [False, False]
        assert db.query(FacebookPagePost).one().status == "posted"
    finally:
        _clear_overrides()


def test_auto_approve_toggle_queues_open_sales_and_returns(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, account, _user, lot = _client(tmp_path, connected=True)
    queued: list[tuple[str, int, bool]] = []
    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        closed = _future_push_body(lot)
        closed.update(
            {
                "ctseller_sale_id": 13,
                "captured_at": (datetime.now(UTC) - timedelta(days=9)).isoformat(),
                "sale_ends_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
            }
        )
        closed["items"][0]["ctseller_item_id"] = 1300
        assert client.post("/integrations/engagement/snapshots", json=closed).status_code == 200
        monkeypatch.setattr(
            engagement_tasks,
            "schedule_facebook_calendar_sync",
            lambda bind, account_id, sale_id, publish_new_slots=False: queued.append(
                (account_id, sale_id, publish_new_slots)
            ),
        )
        with patch("app.services.facebook_page.create_page_feed_post") as feed:
            res = client.patch("/integrations/facebook/settings", json={"calendar_auto_approve": True})
        assert res.status_code == 200, res.text
        assert feed.call_count == 0, "no Graph call on the settings request"
        assert queued == [(account.id, 12, True)]
    finally:
        _clear_overrides()


def test_open_sales_skip_closed_and_stale(tmp_path) -> None:
    _client_obj, db, account, _user, _lot = _client(tmp_path)
    now = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)
    rows = [
        (1, now + timedelta(days=2), now - timedelta(hours=1)),
        (2, now - timedelta(minutes=1), now - timedelta(hours=1)),
        (3, None, now - timedelta(days=3)),
        (4, None, now - timedelta(days=30)),
    ]
    for sale_id, ends, captured in rows:
        db.add(
            EngagementSnapshot(
                account_id=account.id,
                ctseller_sale_id=sale_id,
                ctseller_item_id=sale_id * 10,
                sku=f"S{sale_id}",
                sale_ends_at=ends,
                captured_at=captured,
            )
        )
    db.commit()
    try:
        assert open_calendar_sale_ids(db, account.id, now=now) == [1, 3]
    finally:
        _clear_overrides()


def test_franchise_timezone_moves_slot_times(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, _db, _account, _user, lot = _client(tmp_path, connected=True)
    try:
        assert client.post("/integrations/engagement/snapshots", json=_future_push_body(lot)).status_code == 200
        bad = client.patch("/integrations/facebook/settings", json={"timezone": "Mars/Olympus"})
        assert bad.status_code == 400
        res = client.patch("/integrations/facebook/settings", json={"timezone": "America/Chicago"})
        assert res.status_code == 200, res.text
        assert res.json()["timezone"] == "America/Chicago"
        assert res.json()["timezone_label"] == "CT"
        assert res.json()["calendar_auto_approve"] is False, "timezone-only PATCH leaves auto-approve alone"
        posts = client.get("/engagement/sales/12/calendar").json()
        morning = next(row for row in posts if row["local_time"] == "09:00")
        assert morning["zone"] == "America/Chicago"
        assert morning["zone_label"] == "CT"
        slot_at = datetime.fromisoformat(morning["facebook_slot_at"].replace("Z", "+00:00"))
        local = slot_at.astimezone(ZoneInfo("America/Chicago"))
        assert (local.hour, local.minute) == (9, 0)
        assert "CT" in morning["group_copy"] or "Bidding closes" not in morning["group_copy"]
    finally:
        _clear_overrides()


def test_social_sweep_runs_on_a_timer_even_when_busy(monkeypatch) -> None:
    spawned: list[int] = []

    class FakeChild:
        def __init__(self) -> None:
            self.done = False

        def poll(self):
            return 0 if self.done else None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.done = True

    children: list[FakeChild] = []

    def fake_spawn():
        spawned.append(1)
        child = FakeChild()
        children.append(child)
        return child

    monkeypatch.setattr(worker, "_spawn_social_sweep", fake_spawn)
    state = worker.SocialSweepState()
    assert worker.maybe_social_sweep(state, isolate=True, now_mono=1000.0) is True
    assert worker.maybe_social_sweep(state, isolate=True, now_mono=1100.0) is False, "one child at a time"
    children[-1].done = True
    assert worker.maybe_social_sweep(state, isolate=True, now_mono=1100.0) is True
    assert worker.maybe_social_sweep(state, isolate=True, now_mono=1120.0) is False
    children[-1].done = True
    assert worker.maybe_social_sweep(state, isolate=True, now_mono=1130.0) is False, "60s timer"
    assert worker.maybe_social_sweep(state, isolate=True, now_mono=1161.0) is True
    stuck = children[-1]
    worker.maybe_social_sweep(state, isolate=True, now_mono=1161.0 + worker.SOCIAL_SWEEP_MAX_SECONDS + 1)
    assert stuck.done, "a sweep child over the time cap is killed"
    assert len(spawned) == 3
