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
from app.services.engagement import assign_push_ranks, score_item

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "engagement_oct15.json"
# The Protect iPod row's sheet note says $41 while Sale Price is 40.
PROTECT_NOTE_SKU = "JZ481JZ"


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
    buckets = {"Rescue": 0, "Push": 0, "Protect": 0, "Let Ride": 0}
    push_skus_sheet = []
    push_skus_ours = []
    for src, got in zip(rows, ranked):
        assert got.engagement_score == src["engagement_score"], src["sku"]
        assert got.momentum == src["momentum"], src["sku"]
        assert got.late_surge == src["late_surge"], src["sku"]
        assert got.trajectory == src["trajectory"], src["sku"]
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
    assert buckets == {"Rescue": 126, "Push": 18, "Protect": 1, "Let Ride": 1}
    assert sorted(push_skus_ours) == sorted(push_skus_sheet)
    ranks = [got.push_rank for got in ranked if got.bucket == "Push"]
    assert sorted(ranks) == list(range(1, 19))


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
        assert by_sku["NOLOT"]["bucket"] == "Rescue"
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
    assert all(post["status"] == "Planned" for post in posts)
