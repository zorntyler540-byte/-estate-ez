"""Tests for catalog photo_role helpers and CT Seller tape exclusion."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services.photo_roles import (
    PhotoListingAttrs,
    assign_detected_sku_tape_roles,
    coerce_sku_tape_listing_attrs,
    detected_sku_tape_role_for_file,
    filename_looks_like_sku_tape,
    filter_photos_excluding_sku_tape,
    is_hero_eligible_photo_role,
    is_sku_tape_photo_role,
    listing_attrs_by_path,
    normalize_catalog_photo_role,
    photo_roles_by_path,
    photos_for_ctbids_listing,
    repaired_listing_attrs,
    should_omit_from_ctbids_listing,
)


def test_normalize_catalog_photo_role() -> None:
    assert normalize_catalog_photo_role("tape") == "tape"
    assert normalize_catalog_photo_role("TAPE") == "tape"
    assert normalize_catalog_photo_role("hero") == "hero"
    assert normalize_catalog_photo_role("nope") is None
    assert normalize_catalog_photo_role(None) is None
    assert normalize_catalog_photo_role("") is None


def test_is_sku_tape_photo_role() -> None:
    assert is_sku_tape_photo_role("tape") is True
    assert is_sku_tape_photo_role("hero") is False
    assert is_sku_tape_photo_role(None) is False
    assert is_hero_eligible_photo_role("hero") is True
    assert is_hero_eligible_photo_role("item_front") is True
    assert is_hero_eligible_photo_role("tape") is False
    assert is_hero_eligible_photo_role("unusable") is False


def test_photo_roles_by_path_from_triage(tmp_path: Path) -> None:
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    c = tmp_path / "c.jpg"
    for p in (a, b, c):
        p.write_bytes(b"x")

    triage = [
        {"filename": "a.jpg", "photo_role": "tape"},
        {"filename": "b.jpg", "photo_role": "hero"},
        {"filename": "c.jpg", "photo_role": "item_front"},
    ]
    roles = photo_roles_by_path([a, b, c], triage)
    assert roles[a] == "tape"
    assert roles[b] == "hero"
    assert roles[c] == "item_front"


def test_photo_roles_by_path_uses_entry_keys(tmp_path: Path) -> None:
    a = tmp_path / "IMG_001.jpg"
    a.write_bytes(b"x")
    entries = [(a, "lot1/IMG_001.jpg")]
    triage = [{"filename": "lot1/IMG_001.jpg", "photo_role": "tape"}]
    roles = photo_roles_by_path([a], triage, entries=entries)
    assert roles[a] == "tape"


def test_filter_photos_excluding_sku_tape() -> None:
    photos = [
        SimpleNamespace(id="1", photo_role="tape", original_name="tape.jpg"),
        SimpleNamespace(id="2", photo_role="hero", original_name="hero.jpg"),
        SimpleNamespace(id="3", photo_role=None, original_name="legacy.jpg"),
        SimpleNamespace(id="4", photo_role="item_front", original_name="front.jpg"),
    ]
    kept = filter_photos_excluding_sku_tape(photos)
    assert [p.id for p in kept] == ["2", "3", "4"]


def test_filter_only_tape_leaves_empty() -> None:
    photos = [
        SimpleNamespace(id="1", photo_role="tape"),
        SimpleNamespace(id="2", photo_role="TAPE"),
    ]
    assert filter_photos_excluding_sku_tape(photos) == []
    assert photos_for_ctbids_listing(photos) == []


def test_ctbids_listing_photos_drop_tape_keep_product_shots() -> None:
    photos = [
        SimpleNamespace(id="tape", photo_role="tape"),
        SimpleNamespace(id="hero", photo_role="hero"),
        SimpleNamespace(id="front", photo_role="item_front"),
        SimpleNamespace(id="detail", photo_role="angle_only"),
    ]
    kept = photos_for_ctbids_listing(photos)
    assert [p.id for p in kept] == ["hero", "front", "detail"]


def test_repaired_listing_attrs_demotes_single_item_heroes() -> None:
    triage = [
        {"filename": "group-a.jpg", "photo_role": "hero", "shows_whole_lot": True},
        {
            "filename": "fish.jpg",
            "photo_role": "hero",
            "shows_whole_lot": False,
            "distinct_item_id": "FISH",
            "is_primary_front_for_item": True,
        },
        {"filename": "group-b.jpg", "photo_role": "hero", "shows_whole_lot": True},
        {
            "filename": "eagle.jpg",
            "photo_role": "hero",
            "shows_whole_lot": False,
            "distinct_item_id": "EAGLE",
            "is_primary_front_for_item": True,
        },
    ]
    attrs = repaired_listing_attrs(triage)
    assert attrs["group-a.jpg"].photo_role == "hero"
    assert attrs["group-b.jpg"].photo_role == "hero"
    assert attrs["fish.jpg"].photo_role == "item_front"
    assert attrs["eagle.jpg"].photo_role == "item_front"
    assert attrs["fish.jpg"].distinct_item_id == "FISH"
    assert attrs["fish.jpg"].is_primary_front is True


def test_repaired_listing_attrs_keeps_one_cover_when_no_group_shot() -> None:
    triage = [
        {"filename": "only-item.jpg", "photo_role": "hero", "shows_whole_lot": False},
        {"filename": "angle.jpg", "photo_role": "hero", "shows_whole_lot": False},
    ]
    attrs = repaired_listing_attrs(triage)
    assert attrs["only-item.jpg"].photo_role == "hero"
    assert attrs["angle.jpg"].photo_role == "item_front"


def test_listing_attrs_by_path_uses_repaired_roles(tmp_path: Path) -> None:
    group = tmp_path / "group.jpg"
    single = tmp_path / "single.jpg"
    group.write_bytes(b"x")
    single.write_bytes(b"x")
    triage = [
        {"filename": "group.jpg", "photo_role": "hero", "shows_whole_lot": True},
        {"filename": "single.jpg", "photo_role": "hero", "shows_whole_lot": False},
    ]
    attrs = listing_attrs_by_path([group, single], triage)
    assert attrs[group].photo_role == "hero"
    assert attrs[single].photo_role == "item_front"
    # Raw helper still reports Flash's original label.
    raw = photo_roles_by_path([group, single], triage)
    assert raw[single] == "hero"


def _rgb_image(path: Path, pixels: list[list[tuple[int, int, int]]]) -> Path:
    from PIL import Image

    height = len(pixels)
    width = len(pixels[0])
    im = Image.new("RGB", (width, height))
    im.putdata([rgb for row in pixels for rgb in row])
    im.save(path)
    return path


_LAVENDER = (160, 100, 200)
_BEIGE = (200, 190, 170)
_YELLOW_TAPE = (220, 200, 40)


def _band_image(path: Path, tape_rows: int, total_rows: int = 200, width: int = 200, color=_LAVENDER) -> Path:
    rows = [([color] * width if i < tape_rows else [_BEIGE] * width) for i in range(total_rows)]
    return _rgb_image(path, rows)


def test_filename_looks_like_sku_tape() -> None:
    assert filename_looks_like_sku_tape("IMG_sku-tape_01.jpg") is True
    assert filename_looks_like_sku_tape("lot_tape.jpg") is True
    assert filename_looks_like_sku_tape("estate tape closeup.jpg") is True
    assert filename_looks_like_sku_tape("IMG_1234.jpg") is False
    assert filename_looks_like_sku_tape("tapestry.jpg") is False


def test_omit_unusable_and_filename_hints_without_path() -> None:
    unusable = SimpleNamespace(id="u", photo_role="unusable", original_name="IMG_1.jpg")
    named = SimpleNamespace(id="n", photo_role=None, original_name="sku-tape.jpg")
    item = SimpleNamespace(id="i", photo_role="item_front", original_name="IMG_2.jpg")
    kept = photos_for_ctbids_listing([unusable, named, item])
    assert [p.id for p in kept] == ["i"]
    assert should_omit_from_ctbids_listing(unusable) is True
    assert should_omit_from_ctbids_listing(named) is True


def test_omit_storage_path_filename_hint() -> None:
    photo = SimpleNamespace(
        id="s",
        photo_role="hero",
        original_name="IMG_999.jpg",
        storage_path="catalog/acct/estate-tape.jpg",
        shows_whole_lot=False,
        shows_measuring_scale=False,
    )
    assert should_omit_from_ctbids_listing(photo) is True
    assert photos_for_ctbids_listing([photo]) == []


def test_pixel_ratio_omits_untagged_tape_closeup(tmp_path: Path) -> None:
    path = _band_image(tmp_path / "closeup.png", tape_rows=80)  # 40%
    photo = SimpleNamespace(
        id="tape",
        photo_role=None,
        original_name="IMG_1234.jpg",
        shows_whole_lot=False,
        shows_measuring_scale=False,
    )
    kept = photos_for_ctbids_listing([photo], path_for=lambda _p: path)
    assert kept == []
    assert detected_sku_tape_role_for_file(path, "IMG_1234.jpg") == "tape"


def test_pixel_ratio_keeps_item_with_edge_tape(tmp_path: Path) -> None:
    path = _band_image(tmp_path / "item.png", tape_rows=20)  # 10%
    photo = SimpleNamespace(
        id="item",
        photo_role="item_front",
        original_name="IMG_1234.jpg",
        shows_whole_lot=False,
        shows_measuring_scale=False,
    )
    kept = photos_for_ctbids_listing([photo], path_for=lambda _p: path)
    assert [p.id for p in kept] == ["item"]


def test_mid_coverage_keeps_group_shot_omits_without_flag(tmp_path: Path) -> None:
    path = _band_image(tmp_path / "mid.png", tape_rows=50)  # 25%
    group = SimpleNamespace(
        id="group",
        photo_role="hero",
        original_name="group.jpg",
        shows_whole_lot=True,
        shows_measuring_scale=False,
    )
    mistagged = SimpleNamespace(
        id="mistag",
        photo_role="hero",
        original_name="IMG_9.jpg",
        shows_whole_lot=False,
        shows_measuring_scale=False,
    )
    kept = photos_for_ctbids_listing([group, mistagged], path_for=lambda _p: path)
    assert [p.id for p in kept] == ["group"]


def test_high_coverage_omits_even_when_marked_whole_lot(tmp_path: Path) -> None:
    path = _band_image(tmp_path / "full.png", tape_rows=100)  # 50%
    photo = SimpleNamespace(
        id="full",
        photo_role="hero",
        original_name="IMG_1.jpg",
        shows_whole_lot=True,
        shows_measuring_scale=False,
    )
    assert photos_for_ctbids_listing([photo], path_for=lambda _p: path) == []


def test_measuring_scale_stays_even_with_yellow_coverage(tmp_path: Path) -> None:
    path = _band_image(tmp_path / "measure.png", tape_rows=120, color=_YELLOW_TAPE)
    photo = SimpleNamespace(
        id="m",
        photo_role="angle_only",
        original_name="scale.jpg",
        shows_whole_lot=False,
        shows_measuring_scale=True,
    )
    kept = photos_for_ctbids_listing([photo], path_for=lambda _p: path)
    assert [p.id for p in kept] == ["m"]


def test_coerce_listing_attrs_marks_untagged_closeup(tmp_path: Path) -> None:
    path = _band_image(tmp_path / "coerce.png", tape_rows=90)
    out = coerce_sku_tape_listing_attrs(path, PhotoListingAttrs(photo_role="hero"))
    assert out.photo_role == "tape"
    keep = coerce_sku_tape_listing_attrs(
        _band_image(tmp_path / "keep.png", tape_rows=10),
        PhotoListingAttrs(photo_role="item_front"),
    )
    assert keep.photo_role == "item_front"


def test_beige_furniture_is_not_treated_as_sku_tape(tmp_path: Path) -> None:
    path = _band_image(tmp_path / "beige.png", tape_rows=0)
    photo = SimpleNamespace(
        id="beige",
        photo_role=None,
        original_name="IMG_chair.jpg",
        shows_whole_lot=False,
        shows_measuring_scale=False,
    )
    kept = photos_for_ctbids_listing([photo], path_for=lambda _p: path)
    assert [p.id for p in kept] == ["beige"]


def test_assign_detected_sku_tape_roles_skips_unusable() -> None:
    tape = SimpleNamespace(id="1", photo_role=None)
    unusable = SimpleNamespace(id="2", photo_role="unusable")
    hero = SimpleNamespace(id="3", photo_role="hero")
    changed = assign_detected_sku_tape_roles([tape, unusable, hero], listing=[hero])
    assert changed == 1
    assert tape.photo_role == "tape"
    assert unusable.photo_role == "unusable"
    assert hero.photo_role == "hero"
