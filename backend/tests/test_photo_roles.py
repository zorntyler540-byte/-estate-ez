"""Tests for catalog photo_role helpers and CT Seller tape exclusion."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services.photo_roles import (
    filter_photos_excluding_sku_tape,
    is_sku_tape_photo_role,
    listing_attrs_by_path,
    normalize_catalog_photo_role,
    photo_roles_by_path,
    repaired_listing_attrs,
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
