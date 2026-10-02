from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.ctseller_post import post_one_lot_to_ctseller


class _FakeClient:
    def __init__(self) -> None:
        self.order: list[str] = []

    def list_areas(self) -> list[str]:
        return []

    def create_item(self, item: dict) -> int:
        self.order.append("create")
        return 4411

    def add_item_image(self, **kwargs) -> None:
        self.order.append("image")


def test_creates_item_before_downloading_photos(tmp_path, monkeypatch) -> None:
    jpeg = tmp_path / "a.jpg"
    jpeg.write_bytes(b"not-a-real-jpeg")
    client = _FakeClient()

    def fake_resolve(_path: str | None):
        client.order.append("download")
        return jpeg

    monkeypatch.setattr("app.services.ctseller_post.resolve_catalog_storage_path", fake_resolve)
    monkeypatch.setattr(
        "app.services.ctseller_post.photos_for_ctbids_listing",
        lambda photos: photos,
    )
    monkeypatch.setattr("app.services.ctseller_post.build_item_payload", lambda **kwargs: {"sku": "TG1"})
    monkeypatch.setattr("app.services.ctseller_post.resolve_lot_reserve_price", lambda lot, sale: None)
    monkeypatch.setattr("app.services.ctseller_post.search_tags_for_lot_row", lambda lot: [])
    monkeypatch.setattr("app.services.ctseller_post.lot_is_held_from_ctseller", lambda lot: False)
    monkeypatch.setattr("app.services.ctseller_post.is_facebook_listing_channel", lambda _ch: False)

    photo = SimpleNamespace(
        id="ph1",
        storage_path="a.jpg",
        original_name="a.jpg",
        sort_order=0,
        created_at=None,
        photo_role="item",
    )
    lot = SimpleNamespace(
        id="lot1",
        account_id="acct",
        sale_id="sale1",
        sku="TG1AD",
        auction_title="Silver pitcher",
        professional_description="desc",
        listing_channel="ctseller",
        ctseller_item_id=None,
        specification_lines_json="[]",
        condition_text="GOOD",
        ct_category="Household Goods Other",
        starting_price=1,
        search_tags_json=None,
    )
    sale = SimpleNamespace(id="sale1", account_id="acct")

    photo_q = MagicMock()
    photo_q.filter.return_value = photo_q
    photo_q.order_by.return_value = photo_q
    photo_q.all.return_value = [photo]
    sale_q = MagicMock()
    sale_q.filter.return_value = sale_q
    sale_q.first.return_value = sale

    db = MagicMock()

    def query(model):
        name = getattr(model, "__name__", str(model))
        if "Photo" in name:
            return photo_q
        return sale_q

    db.query.side_effect = query

    out = post_one_lot_to_ctseller(
        db=db,
        lot=lot,
        client=client,
        remote_sale_id=99,
        location_id=1,
        item_receipt_method=None,
        allowed_areas=[],
    )
    assert out.ctseller_item_id == 4411
    assert client.order[0] == "create"
    assert "download" in client.order
    assert client.order.index("create") < client.order.index("download")
    db.commit.assert_called()


def test_create_still_before_photos_when_several(tmp_path, monkeypatch) -> None:
    files = []
    for i in range(3):
        p = tmp_path / f"a{i}.jpg"
        p.write_bytes(b"not-a-real-jpeg")
        files.append(p)
    client = _FakeClient()

    def fake_resolve(_path: str | None):
        client.order.append("download")
        return files[0]

    monkeypatch.setattr("app.services.ctseller_post.resolve_catalog_storage_path", fake_resolve)
    monkeypatch.setattr("app.services.ctseller_post.jpeg_bytes_for_ctseller_post", lambda path: path.read_bytes())
    monkeypatch.setattr(
        "app.services.ctseller_post.photos_for_ctbids_listing",
        lambda photos: photos,
    )
    monkeypatch.setattr("app.services.ctseller_post.build_item_payload", lambda **kwargs: {"sku": "TG1"})
    monkeypatch.setattr("app.services.ctseller_post.resolve_lot_reserve_price", lambda lot, sale: None)
    monkeypatch.setattr("app.services.ctseller_post.search_tags_for_lot_row", lambda lot: [])
    monkeypatch.setattr("app.services.ctseller_post.lot_is_held_from_ctseller", lambda lot: False)
    monkeypatch.setattr("app.services.ctseller_post.is_facebook_listing_channel", lambda _ch: False)

    photos = [
        SimpleNamespace(
            id=f"ph{i}",
            storage_path=f"a{i}.jpg",
            original_name=f"a{i}.jpg",
            sort_order=i,
            created_at=None,
            photo_role="item",
        )
        for i in range(3)
    ]
    lot = SimpleNamespace(
        id="lot1",
        account_id="acct",
        sale_id="sale1",
        sku="TG1AD",
        auction_title="Silver pitcher",
        professional_description="desc",
        listing_channel="ctseller",
        ctseller_item_id=None,
        specification_lines_json="[]",
        condition_text="GOOD",
        ct_category="Household Goods Other",
        starting_price=1,
        search_tags_json=None,
    )
    sale = SimpleNamespace(id="sale1", account_id="acct")
    photo_q = MagicMock()
    photo_q.filter.return_value = photo_q
    photo_q.order_by.return_value = photo_q
    photo_q.all.return_value = photos
    sale_q = MagicMock()
    sale_q.filter.return_value = sale_q
    sale_q.first.return_value = sale
    db = MagicMock()

    def query(model):
        name = getattr(model, "__name__", str(model))
        if "Photo" in name:
            return photo_q
        return sale_q

    db.query.side_effect = query
    out = post_one_lot_to_ctseller(
        db=db,
        lot=lot,
        client=client,
        remote_sale_id=99,
        location_id=1,
        item_receipt_method=None,
        allowed_areas=[],
    )
    assert out.photos_uploaded == 3
    assert client.order[0] == "create"
    assert client.order.count("image") == 3
    assert client.order.index("create") < client.order.index("download")


def test_sale_post_status_empty() -> None:
    from app.ctseller_post_tasks import reset_post_runtime_state, sale_post_status

    reset_post_runtime_state()
    st = sale_post_status("missing")
    assert st["active"] is False
    assert st["total"] == 0


def test_post_routes_include_status_get() -> None:
    from app.main import app

    methods = {
        (getattr(r, "path", None), method)
        for r in app.routes
        for method in (getattr(r, "methods", None) or [])
    }
    assert ("/sales/{sale_id}/post-to-ctseller", "POST") in methods
    assert ("/sales/{sale_id}/post-to-ctseller", "GET") in methods
