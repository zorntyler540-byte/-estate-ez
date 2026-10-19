"""Find groups: Gemini/Search hits stay primary; Facebook search links are fallbacks."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import get_current_user
from app.db import Base, get_db
from app.main import app
from app.models import Account, Lot, Sale, User
from app.services.audience_hunt import audience_research_prompt
from app.services.facebook_caption import (
    FOLLOW_HOOK_LINE,
    SHIPS_NATIONWIDE_LINE,
    group_post_copy,
    template_caption,
)
from app.services.facebook_groups import (
    FACEBOOK_GROUP_SEARCH,
    classify_group_kind,
    facebook_group_search_url,
    fallback_group_searches,
    found_groups_from_lots,
    infer_location_parts,
    sale_facebook_groups_payload,
)
from types import SimpleNamespace
from datetime import datetime, timezone


def test_facebook_group_search_url_encodes_query() -> None:
    url = facebook_group_search_url("Las Vegas estate sales")
    assert url.startswith(FACEBOOK_GROUP_SEARCH)
    assert "facebook.com/search/groups/" in url
    assert "Las+Vegas+estate+sales" in url
    assert " " not in url.split("?q=", 1)[-1]


def test_infer_location_from_caring_transitions_franchise_name() -> None:
    primary, places = infer_location_parts(
        account_name="Caring Transitions of Enterprise-Las Vegas",
        sale_name="Henderson pickup",
        page_name="Caring Transitions of Las Vegas",
    )
    assert primary == "Las Vegas"
    lowered = {p.lower() for p in places}
    assert "las vegas" in lowered
    assert "enterprise" in lowered
    assert any("henderson" in p.lower() for p in places)
    _primary, demo_places = infer_location_parts(sale_name="Facebook demo sale")
    assert not any("facebook" in p.lower() or "demo" in p.lower() for p in demo_places)


def test_fallback_searches_split_local_and_interest() -> None:
    rows = fallback_group_searches(
        places=["Las Vegas", "Henderson"],
        themes=[("vintage costume jewelry collectors", "Jewelry lots in this sale.")],
    )
    local = [r for r in rows if r["kind"] == "local"]
    interest = [r for r in rows if r["kind"] == "interest"]
    assert local
    assert any(r["query"] == "Las Vegas estate sales" for r in local)
    assert any(r["query"] == "Henderson buy sell trade" for r in local)
    assert all(r["url"].startswith(FACEBOOK_GROUP_SEARCH) for r in rows)
    assert interest
    assert interest[0]["query"] == "vintage costume jewelry collectors"
    assert rows[0]["kind"] == "interest"
    interest_idx = [i for i, row in enumerate(rows) if row["kind"] == "interest"]
    local_idx = [i for i, row in enumerate(rows) if row["kind"] == "local"]
    assert max(interest_idx) < min(local_idx)


def test_found_groups_dedupe_and_classify() -> None:
    lots = [
        SimpleNamespace(
            id="lot-1",
            sku="AA1",
            audience_json=json.dumps(
                {
                    "groups": [
                        {
                            "name": "Las Vegas Estate Sales",
                            "url": "https://www.facebook.com/groups/lv",
                            "why": "local sale shoppers",
                        },
                        {
                            "name": "Fenton Glass Collectors",
                            "url": "https://www.facebook.com/groups/fenton",
                            "why": "hobnail glass",
                        },
                    ]
                }
            ),
        ),
        SimpleNamespace(
            id="lot-2",
            sku="AA2",
            audience_json=json.dumps(
                {
                    "groups": [
                        {
                            "name": "Fenton Glass Collectors",
                            "url": "https://www.facebook.com/groups/fenton",
                            "why": "duplicate",
                        }
                    ]
                }
            ),
        ),
    ]
    rows = found_groups_from_lots(lots, ["Las Vegas"])
    assert len(rows) == 2
    kinds = {r["name"]: r["kind"] for r in rows}
    assert kinds["Las Vegas Estate Sales"] == "local"
    assert kinds["Fenton Glass Collectors"] == "interest"
    assert rows[0]["kind"] == "interest"
    assert rows[0]["name"] == "Fenton Glass Collectors"
    assert rows[-1]["kind"] == "local"
    assert classify_group_kind("Buy Sell Trade Henderson", places=["Henderson"]) == "local"


def test_group_post_copy_is_short_and_skips_follow_and_hashtags() -> None:
    close = datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc)
    page = template_caption(
        title="Blue Rhinestone Brooch",
        description="Purple glass stones on a silver-tone pin. " * 4,
        item_url="https://ctbids.com/estate-sale/48212/item/1",
        close_at=close,
        include_follow_hook=True,
        current_bid=12,
        watchers=4,
    )
    text = group_post_copy(
        caption=page,
        title="Blue Rhinestone Brooch",
        item_url="https://ctbids.com/estate-sale/48212/item/1",
        close_at=close,
        sale_url="https://ctbids.com/estate-sale/48212",
    )
    assert "Blue Rhinestone Brooch" in text
    assert "Bidding closes" in text
    assert SHIPS_NATIONWIDE_LINE in text
    assert "https://ctbids.com/estate-sale/48212/item/1" in text
    assert "See the whole sale: https://ctbids.com/estate-sale/48212" in text
    assert FOLLOW_HOOK_LINE not in text
    assert "#" not in text
    assert text.index("Blue Rhinestone Brooch") < text.index("Bidding closes")
    assert text.index("Bidding closes") < text.index(SHIPS_NATIONWIDE_LINE)
    assert text.index(SHIPS_NATIONWIDE_LINE) < text.index("https://ctbids.com/estate-sale/48212/item/1")
    assert len(text) < len(page)


def test_audience_research_prompt_keeps_google_search_brief_and_location() -> None:
    lot = SimpleNamespace(
        auction_title="Fenton hobnail vase",
        sku="FN-1",
        ct_category="Art & Antiques",
        brands_json='["Fenton"]',
        specification_lines_json="[]",
        condition_text="good",
        professional_description="Opalescent glass.",
    )
    prompt = audience_research_prompt(lot, location="Las Vegas")
    assert "Google" not in prompt  # the tool is attached on the API call, not the prose
    assert "Facebook groups" in prompt
    assert "national" in prompt.lower() or "US-wide" in prompt
    assert "ships nationwide" in prompt.lower()
    assert "never a filter" in prompt.lower()
    assert "SECONDARY" in prompt
    assert "Las Vegas" in prompt
    assert prompt.lower().index("primary") < prompt.lower().index("secondary")
    assert "Fenton hobnail vase" in prompt


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'groups.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _seed(db, *, role: str = "owner"):
    account = Account(id="acct-g", name="Caring Transitions of Enterprise-Las Vegas")
    user = User(
        id="user-g",
        account_id=account.id,
        email="g@example.com",
        password_hash="x",
        role=role,
    )
    sale = Sale(id="sale-g", account_id=account.id, user_id=user.id, name="Henderson")
    lot = Lot(
        id="lot-g",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="TG1",
        auction_title="Vintage costume jewelry lot",
        ct_category="Jewelry",
        brands_json='["Coro"]',
        audience_json=json.dumps(
            {
                "worthy": True,
                "groups": [
                    {
                        "name": "Costume Jewelry Collectors",
                        "url": "https://www.facebook.com/groups/cj",
                        "why": "Coro and similar makers",
                    }
                ],
            }
        ),
    )
    db.add_all([account, user, sale, lot])
    db.commit()
    return account, user, sale, lot


def test_sale_facebook_groups_endpoint_returns_hits_and_search_links(tmp_path) -> None:
    Session = _session(tmp_path)
    db = Session()
    account, user, sale, lot = _seed(db)

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    try:
        res = client.get(f"/sales/{sale.id}/facebook-groups")
        assert res.status_code == 200
        body = res.json()
        assert body["location"] == "Las Vegas"
        assert body["groups"][0]["name"] == "Costume Jewelry Collectors"
        assert body["groups"][0]["url"] == "https://www.facebook.com/groups/cj"
        assert body["groups"][0]["kind"] == "interest"
        queries = {row["query"] for row in body["searches"]}
        assert "Las Vegas estate sales" in queries
        assert any("jewelry" in q.lower() for q in queries)
        kinds = [row["kind"] for row in body["searches"]]
        assert "interest" in kinds and "local" in kinds
        assert kinds.index("interest") < kinds.index("local")
        assert "Share → Share to a group" in body["tips"] or "Share to a group" in body["tips"]
        payload = sale_facebook_groups_payload(sale=sale, account=account, lots=[lot], hunt_status=None)
        assert payload["hunted_lots"] == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def test_sale_facebook_groups_allows_catalogger_read(tmp_path) -> None:
    Session = _session(tmp_path)
    db = Session()
    _account, user, sale, _lot = _seed(db, role="catalogger")

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    try:
        res = client.get(f"/sales/{sale.id}/facebook-groups")
        assert res.status_code == 200
        hunt = client.post(f"/sales/{sale.id}/audience-hunt", json={"force": False})
        assert hunt.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()
