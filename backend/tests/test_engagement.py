"""Oct 15 engagement scorer, snapshot ingest, and planned social calendar."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import get_current_user
from app.db import Base, get_db
from app.main import app
from app.models import Account, Lot, Photo, Sale, User
from app.services.engagement import (
    assign_push_ranks,
    display_bucket,
    operational_bucket,
    push_watcher_floor,
    score_item,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "engagement_oct15.json"
# The Protect iPod row's sheet note says $41 while Sale Price is 40.
PROTECT_NOTE_SKU = "JZ481JZ"
GOLD_RING_SKU = "JZ9206JZ"


def _load_fixture() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_oct15_labels_match_workbook() -> None:
    rows = _load_fixture()
    assert len(rows) == 146
    readings = [
        score_item(
            views=row["views"],
            watchers=row["watchers"],
            bids=row["bids"],
            price=row["price"],
            reserve=row["reserve"],
        )
        for row in rows
    ]
    ranked = assign_push_ranks(readings)
    buckets = {"No interest": 0, "Needs eyes": 0, "Push": 0, "Protect": 0, "Let Ride": 0}
    push_skus_sheet = []
    push_skus_ours = []
    for src, got in zip(rows, ranked):
        assert got.engagement_score == src["engagement_score"], src["sku"]
        assert got.momentum == src["momentum"], src["sku"]
        if src["sku"] == GOLD_RING_SKU:
            assert src["bucket"] == "Rescue"
            assert got.bucket == "Push"
            assert got.late_surge == "Yes"
            assert got.trajectory == "Late Climb"
            assert got.rescue_candidate is False
        else:
            assert got.late_surge == src["late_surge"], src["sku"]
            assert got.trajectory == src["trajectory"], src["sku"]
            if src["bucket"] == "Rescue":
                expected = display_bucket("Rescue", watchers=src["watchers"], bids=src["bids"])
                assert got.bucket == expected, src["sku"]
            else:
                assert got.bucket == src["bucket"], src["sku"]
            assert got.rescue_candidate is (src["rescue"] == "Yes")
        buckets[got.bucket] += 1
        if src["sku"] == PROTECT_NOTE_SKU:
            assert src["notes"].endswith("$41; active competition")
            assert got.notes == "3 bids and 8 watchers at $40; active competition"
        else:
            assert got.notes == src["notes"], (src["sku"], got.notes, src["notes"])
        if src["bucket"] == "Push":
            push_skus_sheet.append(src["sku"])
        if got.bucket == "Push":
            push_skus_ours.append(src["sku"])
            assert got.push_rank is not None
        else:
            assert got.push_rank is None
    assert buckets == {"No interest": 117, "Needs eyes": 8, "Push": 19, "Protect": 1, "Let Ride": 1}
    assert GOLD_RING_SKU in push_skus_ours
    assert GOLD_RING_SKU not in push_skus_sheet
    assert sorted(push_skus_ours) == sorted(push_skus_sheet + [GOLD_RING_SKU])
    ranks = [got.push_rank for got in ranked if got.bucket == "Push"]
    assert sorted(ranks) == list(range(1, 20))


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'engagement.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _seed(db):
    account = Account(id="acct-e", name="CT")
    user = User(
        id="user-e",
        account_id=account.id,
        email="engage@example.com",
        password_hash="x",
        role="owner",
    )
    sale = Sale(id="sale-e", account_id=account.id, user_id=user.id, name="Primitive")
    matched = Lot(
        id="lot-matched",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="TG368AD",
        auction_title="Playing cards",
        ctseller_item_id=5667527,
        ctseller_sale_id=48212,
    )
    sku_only = Lot(
        id="lot-sku",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="TG400SK",
        auction_title="Necklace",
    )
    photo = Photo(
        id="photo-1",
        account_id=account.id,
        user_id=user.id,
        lot_id=matched.id,
        original_name="cards.jpg",
        storage_path="catalog/cards.jpg",
        photo_role="hero",
        sort_order=0,
    )
    tape = Photo(
        id="photo-tape",
        account_id=account.id,
        user_id=user.id,
        lot_id=matched.id,
        original_name="tape.jpg",
        storage_path="catalog/tape.jpg",
        photo_role="tape",
        sort_order=1,
    )
    db.add_all([account, user, sale, matched, sku_only, photo, tape])
    db.commit()
    return user


def _client(tmp_path):
    Session = _session(tmp_path)
    db = Session()
    user = _seed(db)

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app), db


def test_snapshot_scores_matches_lots_and_rejects_bidder_fields(tmp_path) -> None:
    client, db = _client(tmp_path)
    body = {
        "ctseller_sale_id": 48212,
        "sale_title": "Primitive sale",
        "auction_url": "https://ctbids.com/estate-sale/48212/",
        "sale_ends_at": "2026-10-20T18:00:00-07:00",
        "captured_at": "2026-10-15T16:00:00-07:00",
        "items": [
            {
                "ctseller_item_id": 5667527,
                "sku": "TG368AD",
                "title": "Jumbo Pin-Up Girl Novelty Playing Cards Deck",
                "views": 25,
                "watchers": 8,
                "bids": 2,
                "price": 5,
                "reserve": 5,
                "item_url": "https://ctbids.com/estate-sale/48212/item/5667527",
                "item_status": "Ready",
            },
            {
                "ctseller_item_id": 5628417,
                "sku": "TG400SK",
                "title": "Monet necklace",
                "views": 23,
                "watchers": 4,
                "bids": 1,
                "price": 1,
                "reserve": 15,
                "item_url": "https://ctbids.com/estate-sale/48212/item/5628417",
                "item_status": "Ready",
            },
            {
                "ctseller_item_id": 999,
                "sku": "NOLOT",
                "title": "Uncataloged",
                "views": 2,
                "watchers": 0,
                "bids": 0,
                "price": 0,
                "reserve": 10,
                "item_url": "https://ctbids.com/estate-sale/48212/item/999",
                "item_status": "Ready",
            },
        ],
    }
    try:
        leaked = client.post(
            "/integrations/engagement/snapshots",
            json={**body, "items": [{**body["items"][0], "high_bidder": "secret", "email": "a@b.c"}]},
        )
        assert leaked.status_code == 422, leaked.text

        saved = client.post("/integrations/engagement/snapshots", json=body)
        assert saved.status_code == 200, saved.text
        summary = saved.json()
        assert summary["item_count"] == 3
        assert summary["matched_lots"] == 2
        assert summary["push"] == 2
        assert summary["no_interest"] == 1
        assert summary["needs_eyes"] == 0
        assert summary["rescue"] == 1

        sale = client.get("/engagement/sales/48212")
        assert sale.status_code == 200, sale.text
        payload = sale.json()
        assert payload["push"] == 2
        by_sku = {row["sku"]: row for row in payload["items"]}
        assert by_sku["TG368AD"]["bucket"] == "Push"
        assert by_sku["TG368AD"]["lot_id"] == "lot-matched"
        assert by_sku["TG368AD"]["photo_ids"] == ["photo-1"]
        assert "photo-tape" not in by_sku["TG368AD"]["photo_ids"]
        assert by_sku["TG400SK"]["lot_id"] == "lot-sku"
        assert by_sku["NOLOT"]["lot_id"] is None
        assert by_sku["NOLOT"]["bucket"] == "No interest"
        assert "high_bidder" not in json.dumps(payload)

        calendar = client.get("/engagement/sales/48212/calendar")
        assert calendar.status_code == 200, calendar.text
        posts = calendar.json()
        assert posts
        assert {post["status"] for post in posts} == {"Planned"}
        assert {post["sku"] for post in posts} <= {"TG368AD", "TG400SK"}
        assert all(post["preflight"] for post in posts)
        assert all("NOLOT" not in post["sku"] for post in posts)

        listed = client.get("/engagement/sales")
        assert listed.status_code == 200
        assert listed.json()[0]["ctseller_sale_id"] == 48212

        lot = client.get("/lots/lot-matched")
        assert lot.status_code == 200, lot.text
        engagement = lot.json()["engagement"]
        assert engagement["bucket"] == "Push"
        assert engagement["watchers"] == 8
        assert engagement["ctseller_item_id"] == 5667527

        # A later refresh that drops the uncataloged lot removes it.
        shrunk = client.post(
            "/integrations/engagement/snapshots",
            json={**body, "items": body["items"][:2]},
        )
        assert shrunk.status_code == 200, shrunk.text
        again = client.get("/engagement/sales/48212").json()
        assert {row["sku"] for row in again["items"]} == {"TG368AD", "TG400SK"}
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_calendar_uses_sale_end_and_stays_planned() -> None:
    from app.services.engagement import build_social_calendar

    posts = build_social_calendar(
        [
            {
                "push_rank": 1,
                "sku": "A",
                "title": "Cards",
                "price": 5,
                "watchers": 8,
                "item_url": "https://ctbids.com/estate-sale/1/item/2",
                "lot_id": "lot",
                "photo_ids": ["p1"],
            }
        ],
        sale_title="Primitive sale",
        captured_at=datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc),
        sale_ends_at=datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc),
    )
    assert posts[0]["status"] == "Planned"
    assert posts[-1]["hook"] == "Closing countdown"
    assert posts[-1]["post_date"] == "2026-10-20"
    assert "Primitive sale" in posts[0]["paste_copy"]
    assert "https://ctbids.com/estate-sale/1/item/2" in posts[0]["paste_copy"]
    assert "See the whole sale: https://ctbids.com/estate-sale/1" in posts[0]["paste_copy"]
    assert posts[0]["paste_copy"].index("https://ctbids.com/estate-sale/1/item/2") < posts[0][
        "paste_copy"
    ].index("See the whole sale:")
    assert "See the whole sale: https://ctbids.com/estate-sale/1" in posts[-1]["paste_copy"]
    assert "Cards" in posts[0]["group_copy"]
    assert "Ships nationwide" in posts[0]["group_copy"]
    assert "Follow our Page" not in posts[0]["group_copy"]
    assert "https://ctbids.com/estate-sale/1/item/2" in posts[0]["group_copy"]
    assert "See the whole sale: https://ctbids.com/estate-sale/1" in posts[0]["group_copy"]
    assert posts[0]["group_copy"].index("Cards") < posts[0]["group_copy"].index("https://ctbids.com/estate-sale/1/item/2")
    assert all(post["status"] == "Planned" for post in posts)
    facebook = [post for post in posts if post["platform"] == "Facebook"]
    assert facebook
    assert all(post["local_time"] == "09:00" for post in facebook)
    other = [post for post in posts if post["platform"] != "Facebook"]
    assert {post["platform"] for post in other} <= {"Instagram", "FB + IG Stories", "TikTok"}
    by_sku_days: dict[str, list[str]] = {}
    for post in posts:
        by_sku_days.setdefault(post["sku"], []).append(post["post_date"])
        assert len(set(by_sku_days[post["sku"]])) == len(by_sku_days[post["sku"]])
    times = sorted(post["local_time"] for post in posts if post["sku"] == "A")
    assert "09:00" in times
    assert "16:30" in times
    assert "17:40" in times


def test_push_watcher_floor_and_rescue_split() -> None:
    assert push_watcher_floor(5) == 2
    assert push_watcher_floor(99.99) == 2
    assert push_watcher_floor(100) == 4
    assert push_watcher_floor(249) == 4
    assert push_watcher_floor(450) == 6
    assert push_watcher_floor(999) == 8
    assert push_watcher_floor(1200) == 10
    assert operational_bucket(12, 1, 450, 650) == "Push"
    assert operational_bucket(2, 1, 5, 5) == "Push"
    assert operational_bucket(2, 1, 150, 200) == "Needs eyes"
    assert operational_bucket(2, 0, 125, 40) == "Needs eyes"
    assert operational_bucket(0, 0, 0, 10) == "No interest"
    assert operational_bucket(1, 0, 8, 10) == "Needs eyes"
    assert operational_bucket(0, 1, 8, 10) == "Needs eyes"
    assert operational_bucket(8, 3, 40, 41) == "Protect"
    assert operational_bucket(1, 6, 50, 40) == "Let Ride"
    assert display_bucket("Rescue", watchers=0, bids=0) == "No interest"
    assert display_bucket("Rescue", watchers=1, bids=0) == "Needs eyes"
    assert display_bucket("Push") == "Push"


def test_calendar_fills_needs_eyes_lightly_and_keeps_keys_unique() -> None:
    from app.services.engagement import build_social_calendar
    from app.services.facebook_calendar import calendar_slot_key, is_dated_slot_key

    push_rows = [
        {
            "push_rank": rank,
            "sku": f"P{rank}",
            "title": f"Push {rank}",
            "price": 8,
            "watchers": 6,
            "item_url": f"https://ctbids.com/estate-sale/1/item/{rank}",
            "lot_id": f"lot-p{rank}",
            "photo_ids": ["p1"],
            "bucket": "Push",
        }
        for rank in range(1, 5)
    ]
    needs = [
        {
            "sku": "EYE1",
            "title": "Quiet lot",
            "price": 12,
            "watchers": 1,
            "item_url": "https://ctbids.com/estate-sale/1/item/90",
            "lot_id": "lot-eye",
            "photo_ids": ["p2"],
            "bucket": "Needs eyes",
        }
    ]
    posts = build_social_calendar(
        push_rows,
        needs_eyes_rows=needs,
        sale_title="Primitive sale",
        captured_at=datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc),
        sale_ends_at=datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc),
        ctseller_sale_id=1,
    )
    assert any(post["sku"] == "EYE1" for post in posts)
    eye_dates = [post["post_date"] for post in posts if post["sku"] == "EYE1"]
    assert len(eye_dates) == 1
    by_day_sku: dict[tuple[str, str], int] = {}
    for post in posts:
        key = (post["sku"], post["post_date"])
        by_day_sku[key] = by_day_sku.get(key, 0) + 1
        assert by_day_sku[key] == 1
        slot = calendar_slot_key(1, post["lot_id"], post["local_time"].replace(":", ""), post["post_date"])
        assert is_dated_slot_key(slot)
    rank1 = [post for post in posts if post["sku"] == "P1"]
    assert len(rank1) >= 2
    assert any(post["hook"] == "Closing countdown" for post in rank1)
    lower = [post for post in posts if post["sku"] == "P4"]
    assert len(lower) >= 1
    no_interest_posts = build_social_calendar(
        [],
        needs_eyes_rows=[],
        captured_at=datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc),
        sale_ends_at=datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc),
    )
    assert no_interest_posts == []
