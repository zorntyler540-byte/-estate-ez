"""Tests for catalog photo_role helpers and CT Seller tape exclusion."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services.photo_roles import (
    filter_photos_excluding_sku_tape,
    is_hero_eligible_photo_role,
    is_sku_tape_photo_role,
    normalize_catalog_photo_role,
    photo_roles_by_path,
    photos_for_ctbids_listing,
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
