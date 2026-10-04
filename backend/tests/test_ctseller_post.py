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
    assert ("/lots/{lot_id}/ctseller-photos", "POST") in methods
    assert ("/sales/{sale_id}/ctseller-photos", "POST") in methods


def test_image_ids_to_replace_keep_photos_and_drop_videos() -> None:
    from app.services.ctseller_post import ctseller_image_ids_to_replace

    ids = ctseller_image_ids_to_replace(
        [
            {"id": 10, "type": "image"},
            {"id": 11, "type": "video"},
            {"id": "12", "type": ""},
            {"id": "nope", "type": "image"},
            {"type": "image"},
            {"id": 13, "type": "VIDEO"},
        ]
    )
    assert ids == [10, 12]


def _photo_db(photos: list, lot_status: str = "Sold"):
    lot = SimpleNamespace(
        id="lot1",
        account_id="acct",
        sale_id="sale1",
        sku="JL102",
        auction_title="Oak dresser",
        listing_channel="ctseller",
        ctseller_item_id=8801,
        ctseller_sale_id=77,
        ctseller_item_status=lot_status,
        ctseller_sold_price=40,
        ctseller_posted_at=None,
    )
    photo_q = MagicMock()
    photo_q.filter.return_value = photo_q
    photo_q.order_by.return_value = photo_q
    photo_q.all.return_value = photos
    db = MagicMock()

    def query(model):
        name = getattr(model, "__name__", str(model))
        if "Photo" in name:
            return photo_q
        return MagicMock()

    db.query.side_effect = query
    return db, lot


def test_replace_photos_keeps_the_same_item(tmp_path, monkeypatch) -> None:
    from fastapi import HTTPException

    from app.services.ctseller_post import replace_lot_photos_on_ctseller

    jpeg = tmp_path / "a.jpg"
    jpeg.write_bytes(b"jpeg")
    client = _FakeClient()
    client.deleted: list[int] = []

    def list_item_images(item_id: int):
        client.order.append("list")
        assert item_id == 8801
        return [
            {"id": 10, "type": "image"},
            {"id": 11, "type": "video"},
            {"id": 12, "type": ""},
        ]

    def delete_item_images(item_id: int, image_ids: list[int]) -> None:
        client.order.append("delete")
        client.deleted = list(image_ids)

    def add_item_image(**kwargs) -> None:
        client.order.append("image")
        client.added.append(kwargs)

    client.list_item_images = list_item_images
    client.delete_item_images = delete_item_images
    client.add_item_image = add_item_image
    client.added = []

    monkeypatch.setattr("app.services.ctseller_post.resolve_catalog_storage_path", lambda _p: jpeg)
    monkeypatch.setattr("app.services.ctseller_post.jpeg_bytes_for_ctseller_post", lambda path: b"jpeg")
    monkeypatch.setattr("app.services.ctseller_post.photos_for_ctbids_listing", lambda photos: photos)
    monkeypatch.setattr("app.services.ctseller_post.lot_is_held_from_ctseller", lambda lot: True)

    photos = [
        SimpleNamespace(id="ph1", storage_path="a.jpg", original_name="dresser.jpg", photo_role="item"),
        SimpleNamespace(id="ph2", storage_path="b.jpg", original_name="drawer.jpg", photo_role="detail"),
    ]
    db, lot = _photo_db(photos)
    out = replace_lot_photos_on_ctseller(db=db, lot=lot, client=client)
    assert out.ctseller_item_id == 8801
    assert out.photos_uploaded == 2
    assert out.ctseller_sale_id == 77
    assert "create" not in client.order
    assert client.order[0] == "list"
    assert client.order.index("delete") < client.order.index("image")
    assert client.deleted == [10, 12]
    assert sorted(row["display_order"] for row in client.added) == [1, 2]
    covers = [row for row in client.added if row["is_display_image"]]
    assert len(covers) == 1 and covers[0]["display_order"] == 1
    assert lot.ctseller_item_status == "Sold"
    assert lot.ctseller_sold_price == 40
    assert lot.ctseller_item_id == 8801
    db.commit.assert_called()

    client.order.clear()
    lot.ctseller_item_id = None
    try:
        replace_lot_photos_on_ctseller(db=db, lot=lot, client=client)
        raise AssertionError("expected 400")
    except HTTPException as exc:
        assert exc.status_code == 400
    assert client.order == []

    lot.ctseller_item_id = 8801
    lot.listing_channel = "facebook"
    try:
        replace_lot_photos_on_ctseller(db=db, lot=lot, client=client)
        raise AssertionError("expected facebook 400")
    except HTTPException as exc:
        assert exc.status_code == 400
    assert "delete" not in client.order


def test_replace_does_not_wipe_a_tape_only_lot(monkeypatch) -> None:
    from fastapi import HTTPException

    from app.services.ctseller_post import replace_lot_photos_on_ctseller

    client = _FakeClient()
    client.list_item_images = lambda item_id: (_ for _ in ()).throw(AssertionError("list"))
    client.delete_item_images = lambda *a, **k: (_ for _ in ()).throw(AssertionError("delete"))
    photos = [SimpleNamespace(id="tape", storage_path="tape.jpg", original_name="sku-tape.jpg", photo_role="tape")]
    db, lot = _photo_db(photos)
    try:
        replace_lot_photos_on_ctseller(db=db, lot=lot, client=client)
        raise AssertionError("expected tape 400")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "tape" in str(exc.detail).lower()


def test_replace_reports_a_short_upload(tmp_path, monkeypatch) -> None:
    from fastapi import HTTPException

    from app.services.ctseller import CTSellerError
    from app.services.ctseller_post import replace_lot_photos_on_ctseller

    jpeg = tmp_path / "a.jpg"
    jpeg.write_bytes(b"jpeg")
    client = _FakeClient()
    client.order = []

    def list_item_images(_item_id: int):
        client.order.append("list")
        return [{"id": 10, "type": "image"}]

    def delete_item_images(_item_id: int, _image_ids: list[int]) -> None:
        client.order.append("delete")

    def add_item_image(**_kwargs) -> None:
        raise CTSellerError("upload failed")

    client.list_item_images = list_item_images
    client.delete_item_images = delete_item_images
    client.add_item_image = add_item_image
    monkeypatch.setattr("app.services.ctseller_post.resolve_catalog_storage_path", lambda _p: jpeg)
    monkeypatch.setattr("app.services.ctseller_post.jpeg_bytes_for_ctseller_post", lambda path: b"jpeg")
    monkeypatch.setattr("app.services.ctseller_post.photos_for_ctbids_listing", lambda photos: photos)
    photos = [SimpleNamespace(id="ph1", storage_path="a.jpg", original_name="a.jpg", photo_role="item")]
    db, lot = _photo_db(photos)
    try:
        replace_lot_photos_on_ctseller(db=db, lot=lot, client=client)
        raise AssertionError("expected 502")
    except HTTPException as exc:
        assert exc.status_code == 502
        assert "Click Update photos again" in str(exc.detail)
    assert client.order == ["list", "delete"]
    assert "create" not in client.order
    assert lot.ctseller_item_status == "Sold"
    db.commit.assert_not_called()


def test_photo_refresh_skips_auction_lookup(monkeypatch) -> None:
    from app.ctseller_post_tasks import _run_sale_post

    client = MagicMock()
    client.list_item_images.return_value = []
    client.sale_detail.side_effect = AssertionError("sale_detail")
    client.list_areas.side_effect = AssertionError("list_areas")
    account = SimpleNamespace(id="acct")
    lot = SimpleNamespace(id="lot1", sku="JL102", account_id="acct", ctseller_item_id=55)

    class _DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query(self, model):
            name = getattr(model, "__name__", "")
            q = MagicMock()
            if name == "Account":
                q.filter.return_value.first.return_value = account
            else:
                q.filter.return_value.first.return_value = lot
            return q

        def expire_all(self):
            return None

    monkeypatch.setattr("app.ctseller_post_tasks.SessionLocal", lambda: _DB())
    monkeypatch.setattr("app.main._ctseller_client_for_account", lambda *a, **k: client)
    monkeypatch.setattr(
        "app.ctseller_post_tasks.replace_lot_photos_on_ctseller",
        lambda **kwargs: SimpleNamespace(ctseller_item_id=55, photos_uploaded=2),
    )
    _run_sale_post(
        account_id="acct",
        sale_id="sale1",
        lot_ids=["lot1"],
        skus={"lot1": "JL102"},
        remote_sale_id=0,
        force=False,
        photos_only=True,
    )
    client.sale_detail.assert_not_called()
    client.list_areas.assert_not_called()
    client.list_item_images.assert_called()
