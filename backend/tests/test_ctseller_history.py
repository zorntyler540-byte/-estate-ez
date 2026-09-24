from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Account, Lot, Sale, User
from app.services.ctseller_history import (
    history_item_outcome,
    parse_ctseller_datetime,
    pull_ctseller_sale_history,
    split_history_category,
    upsert_ctseller_history_items,
)
from app.services.team_activity import summarize_team_activity

UTC = timezone.utc
T0 = datetime(2026, 9, 5, 16, 0, tzinfo=UTC)


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ctseller-history.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def test_split_history_category_group_leaf() -> None:
    assert split_history_category("Jewelry#*#Rings") == ("Jewelry", "Rings")
    assert split_history_category("Lamps") == ("Lamps", None)
    assert split_history_category("") == ("Uncategorized", None)


def test_parse_ctseller_datetime() -> None:
    dt = parse_ctseller_datetime("2026-09-04 18:00:00")
    assert dt == datetime(2026, 9, 4, 18, 0, tzinfo=UTC)
    assert parse_ctseller_datetime("") is None


def test_closed_sale_pending_counts_as_unsold() -> None:
    outcome, price = history_item_outcome({"itemstatus": "Pending"}, sale_open=False)
    assert outcome == "unsold"
    assert price == 0.0
    live, _ = history_item_outcome({"itemstatus": "Ready"}, sale_open=True)
    assert live == "live"


def test_history_fills_sold_docket_for_sales_not_in_catalog(tmp_path: Path) -> None:
    db = _session(tmp_path)
    db.add(Account(id="acct-a", name="CT LV"))
    db.add(
        User(
            id="owner-a",
            account_id="acct-a",
            email="owner@ct.test",
            password_hash="x",
            role="owner",
        )
    )
    db.commit()

    class Client:
        def list_ended_sales(self, **_kwargs):
            return [
                SimpleNamespace(
                    id=99,
                    title="Old Henderson",
                    status="Closed",
                    endtime="2026-09-04 18:00:00",
                )
            ]

        def list_open_sales(self):
            return []

        def list_sale_items(self, sale_id: int):
            assert sale_id == 99
            return [
                {
                    "id": 501,
                    "SKU": "H1",
                    "title": "Gold ring",
                    "itemstatus": "Sold",
                    "soldprice": 80,
                    "category": "Jewelry#*#Rings",
                },
                {
                    "id": 502,
                    "SKU": "H2",
                    "title": "Table lamp",
                    "itemstatus": "Unsold",
                    "category": "Lamps",
                },
            ]

    result = pull_ctseller_sale_history(
        db, "acct-a", Client(), T0 - timedelta(days=2), T0
    )
    db.commit()
    assert result.ended_sales == 1
    assert result.items_upserted == 2
    summary = summarize_team_activity(db, "acct-a", T0 - timedelta(days=2), T0)
    skus = [row.sku for row in summary.sold_items]
    assert skus == ["H1", "H2"]
    by_sku = {row.sku: row for row in summary.sold_items}
    assert by_sku["H1"].outcome == "sold"
    assert by_sku["H1"].sold_price == 80
    assert by_sku["H1"].category == "Jewelry"
    assert by_sku["H1"].subcategory == "Rings"
    assert by_sku["H1"].source == "ctseller"
    assert by_sku["H2"].outcome == "unsold"
    jewelry = next(c for c in summary.sold_by_category if c.category == "Jewelry")
    assert jewelry.sold_count == 1
    assert jewelry.unsold_count == 0
    lamps = next(c for c in summary.sold_by_category if c.category == "Lamps")
    assert lamps.unsold_count == 1


def test_history_does_not_duplicate_catalog_lot(tmp_path: Path) -> None:
    db = _session(tmp_path)
    db.add(Account(id="acct-a", name="CT LV"))
    owner = User(
        id="owner-a",
        account_id="acct-a",
        email="owner@ct.test",
        password_hash="x",
        role="owner",
    )
    db.add(owner)
    db.add(Sale(id="sale-a", account_id="acct-a", user_id=owner.id, name="Sale A"))
    db.add(
        Lot(
            id="lot-sold",
            account_id="acct-a",
            user_id=owner.id,
            sale_id="sale-a",
            sku="JL1",
            auction_title="Ring from catalog",
            ct_category="Jewelry",
            created_at=T0 - timedelta(hours=3),
            ctseller_item_id=501,
            ctseller_posted_at=T0 - timedelta(hours=3),
            ctseller_item_status="Sold",
            ctseller_sold_price=80,
        )
    )
    db.commit()
    sale = SimpleNamespace(id=99, title="Old Henderson", status="Closed", endtime="2026-09-04 18:00:00")
    upsert_ctseller_history_items(
        db,
        "acct-a",
        sale=sale,
        items=[
            {
                "id": 501,
                "SKU": "H1",
                "title": "Gold ring",
                "itemstatus": "Sold",
                "soldprice": 80,
                "category": "Jewelry#*#Rings",
            }
        ],
        sale_open=False,
    )
    db.commit()
    summary = summarize_team_activity(db, "acct-a", T0 - timedelta(days=2), T0)
    sold_rows = [row for row in summary.sold_items if row.outcome == "sold"]
    assert len(sold_rows) == 1
    assert sold_rows[0].source == "catalog"
    assert sold_rows[0].lot_id == "lot-sold"
    assert sold_rows[0].sku == "JL1"
