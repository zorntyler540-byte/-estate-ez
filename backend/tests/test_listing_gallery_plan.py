"""Cheap Flash listing-gallery plan: merge roles and complete filename order (no live Gemini)."""

from __future__ import annotations

from pipeline.pipeline import apply_gallery_plan_to_triage, complete_listing_order


def test_complete_listing_order_keeps_ai_order_and_appends_missing() -> None:
    known = ["group.jpg", "fish.jpg", "eagle.jpg", "tape.jpg"]
    wanted = ["group.jpg", "eagle.jpg"]
    assert complete_listing_order(wanted, known) == [
        "group.jpg",
        "eagle.jpg",
        "fish.jpg",
        "tape.jpg",
    ]


def test_complete_listing_order_matches_basename() -> None:
    known = ["lot1/group.jpg", "lot1/fish.jpg"]
    wanted = ["group.jpg", "FISH.JPG"]
    assert complete_listing_order(wanted, known) == ["lot1/group.jpg", "lot1/fish.jpg"]


def test_apply_gallery_plan_demotes_false_group_and_sets_item_ids() -> None:
    triage = [
        {
            "filename": "01.jpg",
            "photo_role": "hero",
            "shows_whole_lot": False,
            "distinct_item_id": None,
            "brand_text": "keep-ocr",
        },
        {
            "filename": "02.jpg",
            "photo_role": "hero",
            "shows_whole_lot": False,
            "distinct_item_id": None,
        },
    ]
    plan = {
        "photos": [
            {
                "filename": "01.jpg",
                "photo_role": "hero",
                "shows_whole_lot": True,
                "distinct_item_id": None,
                "is_primary_front_for_item": False,
                "shows_measuring_scale": False,
            },
            {
                "filename": "02.jpg",
                "photo_role": "item_front",
                "shows_whole_lot": False,
                "distinct_item_id": "FISH",
                "is_primary_front_for_item": True,
                "shows_measuring_scale": False,
            },
        ]
    }
    out = apply_gallery_plan_to_triage(triage, plan)
    assert out[0]["photo_role"] == "hero"
    assert out[0]["shows_whole_lot"] is True
    assert out[0]["brand_text"] == "keep-ocr"
    assert out[1]["photo_role"] == "item_front"
    assert out[1]["distinct_item_id"] == "FISH"
    assert out[1]["is_primary_front_for_item"] is True
