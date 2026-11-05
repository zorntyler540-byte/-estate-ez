"""Durable publish queue, Instagram feed, and Page / IG stories. Graph HTTP is always faked."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from app import facebook_post_tasks
from app.config import settings
from app.models import FacebookPagePost, SocialPublishBatch, SocialPublishJob
from app.services.facebook_page import DEFERRED_MISSED_NOTE, UNKNOWN_OUTCOME_NOTE
from app.services.meta_graph import MetaGraphError
from tests.test_facebook_page import _add_push_lot, _clear_overrides, _client, _future_push_body

UTC = timezone.utc


class FakeGraph:
    """Answers the Graph endpoints the publishers use and records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.container_status: dict[str, str] = {}
        self.publish_errors: list[MetaGraphError] = []
        self.feed_errors: list[MetaGraphError] = []
        self.ig_story_error: MetaGraphError | None = None

    def paths(self, suffix: str) -> list[tuple[str, str, dict]]:
        return [call for call in self.calls if call[1].endswith(suffix)]

    def __call__(self, method, path, *, params=None, data=None, files=None, timeout=60.0):
        body = dict(data or params or {})
        self.calls.append((method, path, body))
        if method == "GET" and (params or {}).get("fields") == "status_code":
            return {"status_code": self.container_status.get(path.strip("/"), "FINISHED")}
        if path.endswith("/media") and method == "POST":
            if body.get("media_type") == "STORIES" and self.ig_story_error is not None:
                raise self.ig_story_error
            container = f"c{len(self.calls)}"
            self.container_status[container] = "FINISHED"
            return {"id": container}
        if path.endswith("/media_publish"):
            if self.publish_errors:
                raise self.publish_errors.pop(0)
            self.container_status[body["creation_id"]] = "PUBLISHED"
            return {"id": f"ig-media-{body['creation_id']}"}
        if path.endswith("/photos"):
            return {"id": f"photo-{len(self.calls)}"}
        if path.endswith("/photo_stories"):
            return {"success": True, "post_id": "111_story"}
        if path.endswith("/feed"):
            if self.feed_errors:
                raise self.feed_errors.pop(0)
            return {"id": "111_feed"}
        raise AssertionError(f"unexpected Graph call {method} {path}")


def _meta(monkeypatch, *, instagram: bool = False) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "app-secret")
    monkeypatch.setattr(settings, "google_api_key", "")
    monkeypatch.setattr(settings, "meta_instagram_publishing", instagram)


def _worker_mode(monkeypatch) -> None:
    """Production API: enqueue only; the worker drains."""
    monkeypatch.setattr(settings, "run_catalog_jobs_in_process", False)


def _graph(fake: FakeGraph):
    return (
        patch("app.services.meta_graph.graph_request", fake),
        patch("app.services.facebook_page._photo_jpeg", return_value=b"jpg"),
        patch(
            "app.services.social_targets.stage_public_jpeg",
            lambda **kw: f"https://r2.example/{kw['photo_id']}-{kw['kind']}.jpg",
        ),
    )


def _drain(db, *, at: datetime | None = None) -> int:
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, future=True)
    now_fn = (lambda: at) if at is not None else facebook_post_tasks._utcnow
    return facebook_post_tasks.drain_publish_queue(factory, now_fn=now_fn, concurrency=1)


def _post(client, lot, *, headers=None):
    return client.post(
        "/facebook/posts",
        json={"confirm": True, "items": [{"lot_id": lot.id, "caption": "Vase https://ctbids.com/x"}]},
        headers=headers or {},
    )


def _four_lot_sale(client, db, account, user, lot) -> dict[str, dict]:
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
    assert client.post("/integrations/engagement/snapshots", json=body).status_code == 200
    by_time: dict[str, dict] = {}
    for row in client.get("/engagement/sales/12/calendar").json():
        by_time.setdefault(row["local_time"], row)
    return by_time


def _approve(client, key: str):
    res = client.post("/engagement/sales/12/calendar/facebook", json={"confirm": True, "slot_keys": [key]})
    assert res.status_code == 200, res.text
    return res.json()


def _slot_time(row: FacebookPagePost) -> datetime:
    return row.scheduled_publish_time.replace(tzinfo=UTC)


# --- durable queue -----------------------------------------------------------------------


def test_batch_survives_an_api_restart_and_the_worker_posts_it(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    _worker_mode(monkeypatch)
    client, db, _account, _user, lot = _client(tmp_path, connected=True)
    fake = FakeGraph()
    try:
        with _graph(fake)[0], _graph(fake)[1], _graph(fake)[2]:
            res = _post(client, lot)
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["queued"] is True and body["active"] is True
            assert body["results"] == []
            assert fake.calls == [], "the API never calls Graph"
            batch_id = body["batch_id"]

            facebook_post_tasks.reset_facebook_post_runtime_state()  # API process restarted
            still = client.get(f"/facebook/posts/batches/{batch_id}").json()
            assert still["active"] is True and still["total"] == 1

            assert _drain(db) == 1
        done = client.get(f"/facebook/posts/batches/{batch_id}").json()
        assert done["active"] is False
        assert done["posted"] == 1
        assert done["results"][0]["facebook_post_id"] == "111_feed"
        assert len(fake.paths("/feed")) == 1
        assert db.query(FacebookPagePost).one().status == "posted"
    finally:
        _clear_overrides()


def test_idempotency_key_returns_the_same_batch(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    client, db, _account, _user, lot = _client(tmp_path, connected=True)
    fake = FakeGraph()
    try:
        p1, p2, p3 = _graph(fake)
        with p1, p2, p3:
            first = _post(client, lot, headers={"Idempotency-Key": "click-1"}).json()
            second = _post(client, lot, headers={"Idempotency-Key": "click-1"}).json()
        assert first["batch_id"] == second["batch_id"]
        assert second["posted"] == 1
        assert len(fake.paths("/feed")) == 1
        assert db.query(SocialPublishBatch).count() == 1
        assert db.query(FacebookPagePost).count() == 1
    finally:
        _clear_overrides()


def test_transient_errors_retry_with_backoff_then_stop(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    _worker_mode(monkeypatch)
    client, db, _account, _user, lot = _client(tmp_path, connected=True)
    fake = FakeGraph()
    fake.feed_errors = [
        MetaGraphError("(#2) Service temporarily unavailable", code=2, is_transient=True) for _ in range(5)
    ]
    try:
        p1, p2, p3 = _graph(fake)
        with p1, p2, p3:
            batch_id = _post(client, lot).json()["batch_id"]
            t0 = datetime.now(UTC)
            _drain(db, at=t0)
            assert len(fake.paths("/feed")) == 1
            job = db.query(SocialPublishJob).one()
            assert job.status == "queued" and job.attempts == 1
            status = client.get(f"/facebook/posts/batches/{batch_id}").json()
            assert status["active"] is True and status["retrying"] == 1

            _drain(db, at=t0 + timedelta(seconds=10))
            assert len(fake.paths("/feed")) == 1, "no retry inside the backoff"
            _drain(db, at=t0 + timedelta(minutes=1))
            assert len(fake.paths("/feed")) == 2
            _drain(db, at=t0 + timedelta(minutes=5))
            assert len(fake.paths("/feed")) == 3
            _drain(db, at=t0 + timedelta(hours=1))
            assert len(fake.paths("/feed")) == 3, f"capped at {facebook_post_tasks.JOB_MAX_ATTEMPTS}"
        db.expire_all()
        job = db.query(SocialPublishJob).one()
        assert job.status == "failed" and job.active_key is None
        final = client.get(f"/facebook/posts/batches/{batch_id}").json()
        assert final["active"] is False and final["failed"] == 1
    finally:
        _clear_overrides()


def test_unknown_outcome_is_never_retried(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    _worker_mode(monkeypatch)
    client, db, _account, _user, lot = _client(tmp_path, connected=True)
    fake = FakeGraph()
    fake.feed_errors = [MetaGraphError("read timed out", is_transient=True, unknown_outcome=True)]
    try:
        p1, p2, p3 = _graph(fake)
        with p1, p2, p3:
            _post(client, lot)
            t0 = datetime.now(UTC)
            _drain(db, at=t0)
            _drain(db, at=t0 + timedelta(hours=1))
        assert len(fake.paths("/feed")) == 1
        db.expire_all()
        assert db.query(SocialPublishJob).one().status == "failed"
        row = db.query(FacebookPagePost).one()
        assert row.status == "failed"
        assert row.review_note == UNKNOWN_OUTCOME_NOTE
    finally:
        _clear_overrides()


def test_stale_running_job_is_requeued_when_nothing_was_sent(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    _worker_mode(monkeypatch)
    client, db, _account, _user, lot = _client(tmp_path, connected=True)
    fake = FakeGraph()
    try:
        p1, p2, p3 = _graph(fake)
        with p1, p2, p3:
            _post(client, lot)
            job = db.query(SocialPublishJob).one()
            job.status = "running"
            job.attempts = 1
            job.claimed_at = datetime.now(UTC) - timedelta(minutes=20)
            db.commit()
            _drain(db)
        db.expire_all()
        assert db.query(SocialPublishJob).one().status == "done"
        assert len(fake.paths("/feed")) == 1
    finally:
        _clear_overrides()


def test_stale_running_job_with_a_half_sent_post_fails_safe(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    _worker_mode(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    fake = FakeGraph()
    try:
        p1, p2, p3 = _graph(fake)
        with p1, p2, p3:
            _post(client, lot)
            record = FacebookPagePost(
                account_id=account.id,
                user_id=user.id,
                lot_id=lot.id,
                status="publishing",
                claimed_at=datetime.now(UTC) - timedelta(minutes=20),
            )
            db.add(record)
            db.flush()
            job = db.query(SocialPublishJob).one()
            job.status = "running"
            job.attempts = 1
            job.post_record_id = record.id
            job.claimed_at = datetime.now(UTC) - timedelta(minutes=20)
            db.commit()
            _drain(db)
        assert fake.calls == []
        db.expire_all()
        assert db.query(SocialPublishJob).one().status == "failed"
        record = db.get(FacebookPagePost, record.id)
        assert record.status == "failed"
        assert record.review_note == facebook_post_tasks.STALE_UNKNOWN
    finally:
        _clear_overrides()


def test_a_calendar_slot_has_at_most_one_live_job(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    _client_, db, account, user, lot = _client(tmp_path, connected=True)
    try:
        item = {"lot_id": lot.id, "caption": "x", "calendar_key": "pushcal:12:lot:20261107:0900"}
        kwargs = dict(account_id=account.id, user_id=user.id, include_follow_hook=False, force=False, source="push_calendar")
        facebook_post_tasks.enqueue_batch(db, items=[item], **kwargs)
        second, _ = facebook_post_tasks.enqueue_batch(db, items=[item], **kwargs)
        jobs = db.query(SocialPublishJob).filter(SocialPublishJob.batch_id == second).all()
        assert [job.status for job in jobs] == ["skipped"]
        assert json.loads(jobs[0].result_json)["error"] == facebook_post_tasks.ALREADY_QUEUED
        assert facebook_post_tasks.live_slot_keys(db, account.id) == {item["calendar_key"]}
    finally:
        _clear_overrides()


def test_one_account_runs_one_job_at_a_time(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch)
    _worker_mode(monkeypatch)
    client, db, account, _user, lot = _client(tmp_path, connected=True)
    try:
        _post(client, lot)
        _post(client, lot)
        now = datetime.now(UTC)
        first = facebook_post_tasks.claim_next_job(db, account.id, now=now)
        assert first is not None
        assert facebook_post_tasks.claim_next_job(db, account.id, now=now) is None
    finally:
        _clear_overrides()


def test_accounts_drain_concurrently(monkeypatch) -> None:
    barrier = threading.Barrier(2, timeout=5)
    calls: list[str] = []

    def fake_drain_account(_factory, account_id, **_kwargs):
        calls.append(account_id)
        barrier.wait()  # deadlocks (BrokenBarrierError) if accounts ran one after another
        return 1

    rounds = iter([["acct-a", "acct-b"], []])
    monkeypatch.setattr(facebook_post_tasks, "drain_account", fake_drain_account)
    monkeypatch.setattr(facebook_post_tasks, "due_account_ids", lambda _db, **_kw: next(rounds))
    monkeypatch.setattr(facebook_post_tasks, "recover_stale_jobs", lambda _db, **_kw: 0)

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    assert facebook_post_tasks.drain_publish_queue(lambda: _Session(), concurrency=2) == 2
    assert sorted(calls) == ["acct-a", "acct-b"]


# --- Instagram / Stories -------------------------------------------------------------------


def test_instagram_slot_is_reserved_then_published_at_slot_time(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch, instagram=True)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    account.instagram_user_id = "ig-1"
    db.commit()
    fake = FakeGraph()
    try:
        by_time = _four_lot_sale(client, db, account, user, lot)
        ig_slot = by_time["12:15"]
        assert ig_slot["publish_target"] == "instagram_feed"
        assert ig_slot["facebook_schedulable"] is True
        p1, p2, p3 = _graph(fake)
        with p1, p2, p3:
            result = _approve(client, ig_slot["slot_key"])["results"][0]
            assert result["ok"] is True and result["status"] == "scheduled"
            assert fake.calls == [], "Instagram cannot be scheduled through Graph; nothing is sent yet"
            row = db.query(FacebookPagePost).one()
            assert (row.status, row.publish_target, row.facebook_post_id) == ("scheduled", "instagram_feed", None)
            calendar = {r["slot_key"]: r for r in client.get("/engagement/sales/12/calendar").json()}
            pinned = calendar[ig_slot["slot_key"]]
            assert pinned["facebook_status"] == "scheduled"
            assert pinned["pinned"] is True
            assert "slot time" in pinned["facebook_status_label"]

            _drain(db, at=_slot_time(row) - timedelta(minutes=5))
            assert fake.calls == [], "not before slot time"
            _drain(db, at=_slot_time(row) + timedelta(minutes=1))
        assert fake.paths("/feed") == [], "never the Page feed"
        assert len(fake.paths("/ig-1/media_publish")) == 1
        db.expire_all()
        row = db.query(FacebookPagePost).one()
        assert row.status == "posted"
        assert row.instagram_media_id.startswith("ig-media-")
        assert db.query(SocialPublishJob).one().status == "done"
    finally:
        _clear_overrides()


def test_instagram_retry_never_publishes_a_container_twice(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch, instagram=True)
    _worker_mode(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    account.instagram_user_id = "ig-1"
    db.commit()
    fake = FakeGraph()
    try:
        by_time = _four_lot_sale(client, db, account, user, lot)
        p1, p2, p3 = _graph(fake)
        with p1, p2, p3:
            res = client.post(
                "/engagement/sales/12/calendar/facebook",
                json={"confirm": True, "post_now": True, "slot_keys": [by_time["12:15"]["slot_key"]]},
            )
            assert res.status_code == 200, res.text

            # media_publish times out after Instagram actually published it.
            def timeout_after_publish():
                container = fake.paths("/media")[-1]
                creation = f"c{fake.calls.index(container) + 1}"
                fake.container_status[creation] = "PUBLISHED"
                return MetaGraphError("read timed out", is_transient=True, unknown_outcome=True)

            original = fake.__call__

            def flaky(method, path, **kw):
                if path.endswith("/media_publish") and not fake.paths("/media_publish"):
                    fake.calls.append((method, path, dict(kw.get("data") or {})))
                    raise timeout_after_publish()
                return original(method, path, **kw)

            with patch("app.services.meta_graph.graph_request", flaky):
                _drain(db)
        assert len(fake.paths("/media_publish")) == 1, "the container was PUBLISHED; no second publish"
        db.expire_all()
        row = db.query(FacebookPagePost).one()
        assert row.status == "posted" and row.instagram_media_id
    finally:
        _clear_overrides()


def test_instagram_transient_publish_error_resumes_the_same_container(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch, instagram=True)
    _worker_mode(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    account.instagram_user_id = "ig-1"
    db.commit()
    fake = FakeGraph()
    fake.publish_errors = [MetaGraphError("(#4) Application request limit reached", code=4, is_transient=True)]
    discarded: list[dict] = []
    try:
        by_time = _four_lot_sale(client, db, account, user, lot)
        p1, p2, p3 = _graph(fake)
        with p1, p2, p3, patch(
            "app.services.social_targets.discard_staged_media", lambda **kw: discarded.append(kw)
        ):
            client.post(
                "/engagement/sales/12/calendar/facebook",
                json={"confirm": True, "post_now": True, "slot_keys": [by_time["12:15"]["slot_key"]]},
            )
            t0 = datetime.now(UTC)
            _drain(db, at=t0)
            containers_after_first = len([c for c in fake.calls if c[1].endswith("/media")])
            assert db.query(SocialPublishJob).one().status == "queued"
            assert discarded == [], "a retry may still need the staged image URLs"
            _drain(db, at=t0 + timedelta(minutes=1))
        assert len([c for c in fake.calls if c[1].endswith("/media")]) == containers_after_first
        assert len(fake.paths("/media_publish")) == 2
        assert len(discarded) == 1 and discarded[0]["photo_ids"]
        db.expire_all()
        assert db.query(FacebookPagePost).one().status == "posted"
    finally:
        _clear_overrides()


def test_story_slot_posts_page_and_instagram_stories_not_the_feed(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch, instagram=True)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    account.instagram_user_id = "ig-1"
    db.commit()
    fake = FakeGraph()
    try:
        by_time = _four_lot_sale(client, db, account, user, lot)
        story = by_time["16:30"]
        assert story["publish_target"] == "stories"
        p1, p2, p3 = _graph(fake)
        with p1, p2, p3:
            _approve(client, story["slot_key"])
            row = db.query(FacebookPagePost).one()
            _drain(db, at=_slot_time(row) + timedelta(minutes=1))
        assert fake.paths("/feed") == []
        assert len(fake.paths("/111/photo_stories")) == 1
        ig = [c for c in fake.paths("/ig-1/media") if c[2].get("media_type") == "STORIES"]
        assert len(ig) == 1
        db.expire_all()
        row = db.query(FacebookPagePost).one()
        assert row.status == "posted"
        assert row.facebook_post_id == "111_story"
        assert row.instagram_media_id
    finally:
        _clear_overrides()


def test_instagram_story_failure_never_reposts_the_page_story(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch, instagram=True)
    _worker_mode(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    account.instagram_user_id = "ig-1"
    db.commit()
    fake = FakeGraph()
    fake.ig_story_error = MetaGraphError("(#9004) media download failed", code=9004)
    try:
        by_time = _four_lot_sale(client, db, account, user, lot)
        p1, p2, p3 = _graph(fake)
        with p1, p2, p3:
            client.post(
                "/engagement/sales/12/calendar/facebook",
                json={"confirm": True, "post_now": True, "slot_keys": [by_time["16:30"]["slot_key"]]},
            )
            t0 = datetime.now(UTC)
            _drain(db, at=t0)
            _drain(db, at=t0 + timedelta(hours=1))
        assert len(fake.paths("/photo_stories")) == 1
        db.expire_all()
        row = db.query(FacebookPagePost).one()
        assert row.status == "posted"
        assert "Instagram story failed" in (row.review_note or "")
    finally:
        _clear_overrides()


def test_page_story_without_instagram(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch, instagram=False)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    fake = FakeGraph()
    try:
        by_time = _four_lot_sale(client, db, account, user, lot)
        assert by_time["12:15"]["publish_target"] == "copy_only"
        p1, p2, p3 = _graph(fake)
        with p1, p2, p3:
            client.post(
                "/engagement/sales/12/calendar/facebook",
                json={"confirm": True, "post_now": True, "slot_keys": [by_time["16:30"]["slot_key"]]},
            )
        assert len(fake.paths("/photo_stories")) == 1
        assert [c for c in fake.calls if "/media" in c[1]] == []
        assert fake.paths("/feed") == []
    finally:
        _clear_overrides()


def test_unapproving_a_reserved_slot_frees_it_and_posts_nothing(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch, instagram=True)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    account.instagram_user_id = "ig-1"
    db.commit()
    fake = FakeGraph()
    try:
        by_time = _four_lot_sale(client, db, account, user, lot)
        key = by_time["12:15"]["slot_key"]
        p1, p2, p3 = _graph(fake)
        with p1, p2, p3:
            _approve(client, key)
            row = db.query(FacebookPagePost).one()
            slot_at = _slot_time(row)
            res = client.post("/engagement/sales/12/calendar/facebook/cancel", json={"slot_keys": [key]})
            assert res.json()["results"][0]["ok"] is True
            assert facebook_post_tasks.live_slot_keys(db, account.id) == set()
            _drain(db, at=slot_at + timedelta(minutes=1))
            assert fake.calls == []
            again = _approve(client, key)["results"][0]
            assert again["ok"] is True and again["status"] == "scheduled"
    finally:
        _clear_overrides()


def test_a_reserved_slot_the_worker_reaches_too_late_is_not_posted(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch, instagram=True)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    account.instagram_user_id = "ig-1"
    db.commit()
    fake = FakeGraph()
    try:
        by_time = _four_lot_sale(client, db, account, user, lot)
        p1, p2, p3 = _graph(fake)
        with p1, p2, p3:
            _approve(client, by_time["12:15"]["slot_key"])
            row = db.query(FacebookPagePost).one()
            _drain(db, at=_slot_time(row) + timedelta(hours=3))
        assert fake.calls == []
        db.expire_all()
        row = db.query(FacebookPagePost).one()
        assert row.status == "failed"
        assert row.review_note == DEFERRED_MISSED_NOTE
    finally:
        _clear_overrides()


def test_snapshot_sync_never_marks_a_reserved_slot_posted(tmp_path, monkeypatch) -> None:
    from app.services.facebook_calendar import sync_facebook_calendar_after_snapshot

    _meta(monkeypatch, instagram=True)
    _worker_mode(monkeypatch)
    client, db, account, user, lot = _client(tmp_path, connected=True)
    account.instagram_user_id = "ig-1"
    db.commit()
    try:
        by_time = _four_lot_sale(client, db, account, user, lot)
        _approve(client, by_time["12:15"]["slot_key"])
        _drain(db)
        row = db.query(FacebookPagePost).one()
        assert row.status == "scheduled"
        sync_facebook_calendar_after_snapshot(db, account.id, 12, now=_slot_time(row) + timedelta(minutes=30))
        db.refresh(row)
        assert row.status == "scheduled", "only the queue posts Instagram; Facebook never does it on its own"
    finally:
        _clear_overrides()


def test_instagram_scopes_and_link_only_when_enabled(tmp_path, monkeypatch) -> None:
    _meta(monkeypatch, instagram=False)
    client, db, account, _user, _lot = _client(tmp_path, connected=True)
    try:
        status = client.get("/integrations/facebook/status").json()
        assert status["instagram_enabled"] is False
        assert "instagram_content_publish" not in status["oauth_scopes"]
        monkeypatch.setattr(settings, "meta_instagram_publishing", True)
        with patch(
            "app.services.meta_graph.graph_request",
            lambda *_a, **_k: {"instagram_business_account": {"id": "ig-9", "username": "ctestate"}},
        ):
            res = client.post("/integrations/facebook/instagram/refresh")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["instagram_connected"] is True
        assert body["instagram_username"] == "ctestate"
        assert "instagram_content_publish" in body["oauth_scopes"]
        db.refresh(account)
        assert account.instagram_user_id == "ig-9"
    finally:
        _clear_overrides()


@pytest.mark.parametrize(
    ("size", "expected_ratio"),
    [((400, 1000), 0.8), ((2000, 500), 1.91), ((800, 800), 1.0)],
)
def test_instagram_feed_images_fit_the_allowed_aspect_range(size, expected_ratio) -> None:
    import io

    from PIL import Image

    from app.services.social_targets import fit_instagram_feed

    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="JPEG")
    with Image.open(io.BytesIO(fit_instagram_feed(buf.getvalue()))) as out:
        assert abs(out.size[0] / out.size[1] - expected_ratio) < 0.01
