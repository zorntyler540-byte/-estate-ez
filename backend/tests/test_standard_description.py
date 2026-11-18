"""Standard CT Seller description note, and reserve pushed on lot save."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.standard_description import (
    append_standard_description,
    replace_trailing_standard_description,
    resolve_standard_description,
    suffix_already_present,
)


def test_append_is_verbatim_and_not_duplicated() -> None:
    note = "Reserve is $5"
    once = append_standard_description("Silver pitcher.", note)
    assert once == "Silver pitcher.\n\nReserve is $5"
    assert append_standard_description(once, note) == once
    assert append_standard_description("Silver pitcher.\nReserve is $5", note) == "Silver pitcher.\nReserve is $5"
    # A longer amount is not the same note.
    assert append_standard_description("Reserve is $50", note) == "Reserve is $50\n\nReserve is $5"
    assert suffix_already_present("Intro\n\nReserve is $5\n\nThanks", note)
    assert append_standard_description("", note) == note
    assert append_standard_description("Body", "  ") == "Body"


def test_replace_trailing_note_then_append_new_one() -> None:
    old = "Reserve is $5"
    new = "Reserve is $10"
    body = "Silver pitcher.\n\nReserve is $5"
    assert replace_trailing_standard_description(body, old, new) == "Silver pitcher.\n\nReserve is $10"
    assert replace_trailing_standard_description(body, old, "") == "Silver pitcher."
    # Already the new note: do not stack it.
    current = "Silver pitcher.\n\nReserve is $10"
    assert replace_trailing_standard_description(current, old, new) == current


def test_sale_override_beats_account_including_blank() -> None:
    account = SimpleNamespace(standard_description="Reserve is $5")
    inherit = SimpleNamespace(standard_description=None)
    custom = SimpleNamespace(standard_description="Pickup Saturday.")
    off = SimpleNamespace(standard_description="")
    assert resolve_standard_description(inherit, account) == "Reserve is $5"
    assert resolve_standard_description(custom, account) == "Pickup Saturday."
    assert resolve_standard_description(off, account) == ""
    assert resolve_standard_description(None, account) == "Reserve is $5"


def test_post_sends_reserve_and_standard_description(monkeypatch) -> None:
    from app.services.ctseller_post import post_one_lot_to_ctseller

    captured: dict = {}

    class Client:
        def create_item(self, item: dict) -> int:
            captured["item"] = item
            return 77

        def list_areas(self) -> list[str]:
            return []

    lot = SimpleNamespace(
        id="lot1",
        account_id="acct",
        sale_id="sale1",
        sku="TG1",
        auction_title="Silver pitcher",
        professional_description="A silver pitcher.",
        listing_channel="ctseller",
        ctseller_item_id=None,
        audience_hold=False,
        specification_lines_json="[]",
        brands_json="[]",
        labels_and_tags_json="[]",
        search_tags_json="[]",
        condition_text="GOOD",
        ct_category="Household Goods Other",
        starting_price=1,
        reserve_price=8,
    )
    sale = SimpleNamespace(id="sale1", account_id="acct", standard_description=None, ctseller_reserve_price=3)
    account = SimpleNamespace(id="acct", standard_description="Reserve is $5")

    def query(model):
        name = getattr(model, "__name__", "")
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = []
        if name == "Sale":
            q.first.return_value = sale
        elif name == "Account":
            q.first.return_value = account
        else:
            q.first.return_value = None
        return q

    db = MagicMock()
    db.query.side_effect = query
    monkeypatch.setattr("app.services.ctseller_post.lot_is_held_from_ctseller", lambda _lot: False)
    monkeypatch.setattr("app.services.ctseller_post.is_facebook_listing_channel", lambda _ch: False)

    out = post_one_lot_to_ctseller(
        db=db,
        lot=lot,  # type: ignore[arg-type]
        client=Client(),  # type: ignore[arg-type]
        remote_sale_id=99,
        location_id=1,
        item_receipt_method=None,
        allowed_areas=[],
    )
    assert out.ctseller_item_id == 77
    item = captured["item"]
    assert item["reserveprice"] == 8.0
    assert item["description"] == "A silver pitcher.\n\nReserve is $5"
    # The note is not fed back into the description a second time.
    lot.professional_description = item["description"]
    lot.ctseller_item_id = None
    post_one_lot_to_ctseller(
        db=db,
        lot=lot,  # type: ignore[arg-type]
        client=Client(),  # type: ignore[arg-type]
        remote_sale_id=99,
        location_id=1,
        item_receipt_method=None,
        allowed_areas=[],
    )
    assert captured["item"]["description"] == "A silver pitcher.\n\nReserve is $5"


def _reserve_client(tmp_path, name: str):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.auth import get_current_user
    from app.db import Base, get_db
    from app.main import app
    from app.models import Account, Lot, Sale, User

    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = Session()
    account = Account(id="acct-sd", name="CT")
    user = User(id="user-sd", account_id=account.id, email="sd@example.com", password_hash="x", role="owner")
    sale = Sale(id="sale-sd", account_id=account.id, user_id=user.id, name="Oak Street")
    open_lot = Lot(
        id="lot-open",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="001",
        auction_title="Lamp",
        professional_description="Brass lamp.",
    )
    posted = Lot(
        id="lot-posted",
        account_id=account.id,
        user_id=user.id,
        sale_id=sale.id,
        sku="002",
        auction_title="Chair",
        professional_description="Oak chair.",
        ctseller_item_id=880,
        reserve_price=4,
    )
    db.add_all([account, user, sale, open_lot, posted])
    db.commit()

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    # Keep the session that owns `user` alive. Commit expires attributes, and
    # dropping this session detaches the override user before the request runs.
    return TestClient(app), Session, app, db


def test_account_default_and_sale_override_apply_only_to_unposted(tmp_path) -> None:
    client, Session, app, _db = _reserve_client(tmp_path, "std.db")
    try:
        saved = client.patch(
            "/account/listing-defaults",
            json={"standard_description": "Reserve is $5", "apply_to_unposted": True},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["lots_updated"] == 1
        me = client.get("/auth/me")
        assert me.json()["standard_description"] == "Reserve is $5"

        inner = Session()
        try:
            from app.models import Lot

            by_id = {row.id: row.professional_description for row in inner.query(Lot).all()}
            assert by_id["lot-open"] == "Brass lamp.\n\nReserve is $5"
            assert by_id["lot-posted"] == "Oak chair."
        finally:
            inner.close()

        sale = client.post(
            "/sales/sale-sd/standard-description",
            json={"standard_description": "Pickup Saturday.", "apply_to_unposted": True},
        )
        assert sale.status_code == 200, sale.text
        body = sale.json()
        assert body["standard_description"] == "Pickup Saturday."
        assert body["posting_description_suffix"] == "Pickup Saturday."
        assert body["lots_updated"] == 1

        cleared = client.post(
            "/sales/sale-sd/standard-description",
            json={"inherit_account": True, "apply_to_unposted": False},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["standard_description"] is None
        assert cleared.json()["posting_description_suffix"] == "Reserve is $5"
        assert cleared.json()["lots_updated"] == 0
    finally:
        app.dependency_overrides.clear()


def test_blank_sale_override_suppresses_account_note(tmp_path) -> None:
    client, _Session, app, _db = _reserve_client(tmp_path, "std-blank.db")
    try:
        client.patch("/account/listing-defaults", json={"standard_description": "Reserve is $5"})
        res = client.post(
            "/sales/sale-sd/standard-description",
            json={"standard_description": "", "apply_to_unposted": False},
        )
        assert res.status_code == 200, res.text
        assert res.json()["standard_description"] == ""
        assert res.json()["posting_description_suffix"] == ""
    finally:
        app.dependency_overrides.clear()


def test_lot_patch_pushes_reserve_to_posted_item(tmp_path) -> None:
    client, Session, app, _db = _reserve_client(tmp_path, "reserve-push.db")
    try:
        fake = MagicMock()
        fake.update_item_reserve.return_value = {"status": "success"}
        with patch("app.main._ctseller_client_for_account", return_value=fake):
            res = client.patch("/lots/lot-posted", json={"reserve_price": 12})
        assert res.status_code == 200, res.text
        assert res.json()["reserve_price"] == 12
        assert res.json()["ctseller_reserve_warning"] is None
        fake.update_item_reserve.assert_called_once()
        assert fake.update_item_reserve.call_args.kwargs["item_id"] == 880
        assert fake.update_item_reserve.call_args.kwargs["reserve_price"] == 12

        fake.reset_mock()
        with patch("app.main._ctseller_client_for_account", return_value=fake):
            same = client.patch("/lots/lot-posted", json={"reserve_price": 12})
        assert same.status_code == 200
        fake.update_item_reserve.assert_not_called()

        from app.services.ctseller import CTSellerError

        fake.update_item_reserve.side_effect = CTSellerError("seller rejected reserve")
        with patch("app.main._ctseller_client_for_account", return_value=fake):
            failed = client.patch("/lots/lot-posted", json={"reserve_price": 20})
        assert failed.status_code == 200, failed.text
        assert failed.json()["reserve_price"] == 20
        assert "CT Seller was not updated" in (failed.json()["ctseller_reserve_warning"] or "")
        inner = Session()
        try:
            from app.models import Lot

            assert inner.query(Lot).filter(Lot.id == "lot-posted").one().reserve_price == 20
        finally:
            inner.close()

        # Unposted lots are saved and are not sent until post.
        fake.update_item_reserve.side_effect = None
        with patch("app.main._ctseller_client_for_account", return_value=fake) as build:
            open_res = client.patch("/lots/lot-open", json={"reserve_price": 6})
        assert open_res.status_code == 200
        assert open_res.json()["reserve_price"] == 6
        build.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_sale_reserve_reports_ctseller_failure_without_losing_the_save(tmp_path) -> None:
    from fastapi import HTTPException

    client, Session, app, _db = _reserve_client(tmp_path, "reserve-fail.db")
    try:
        with patch(
            "app.main._ctseller_client_for_account",
            side_effect=HTTPException(status_code=400, detail="Connect your CT Seller account on Account → CT Seller first"),
        ):
            res = client.post(
                "/sales/sale-sd/reserve-price",
                json={"reserve_price": 15, "apply_to_lots": True, "push_posted": True, "update_sale_default": True},
            )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["reserve_price"] == 15
        assert body["lots_updated"] == 2
        assert body["posted_updated"] == 0
        assert body["posted_failed"] == 1
        assert "Connect your CT Seller" in (body["results"][0]["error"] or "")
        inner = Session()
        try:
            from app.models import Lot, Sale

            assert inner.query(Sale).one().ctseller_reserve_price == 15
            assert {row.reserve_price for row in inner.query(Lot).all()} == {15}
        finally:
            inner.close()
    finally:
        app.dependency_overrides.clear()
