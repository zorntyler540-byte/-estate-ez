"""Tests for estate pipeline integration helpers (no live Gemini calls)."""

from __future__ import annotations

from app.services.pipeline_integration import _brands_from_triage_and_draft, _lot_from_flash_draft
from pipeline.pipeline import _build_pro_image_payload, select_analysis_photos


def test_select_analysis_photos_picks_hero_and_fronts() -> None:
    triage = [
        {
            "filename": "hero.jpg",
            "photo_role": "hero",
            "shows_whole_lot": True,
            "label_legibility": "none",
            "quality": "sharp",
            "confidence": "high",
            "is_primary_front_for_item": False,
        },
        {
            "filename": "front.jpg",
            "photo_role": "item_front",
            "distinct_item_id": "ITEM_1",
            "is_primary_front_for_item": True,
            "label_legibility": "clear",
            "brand_text": "Levi's",
            "quality": "sharp",
            "confidence": "high",
            "shows_whole_lot": False,
        },
        {
            "filename": "angle.jpg",
            "photo_role": "angle_only",
            "label_legibility": "none",
            "quality": "ok",
            "confidence": "high",
            "shows_whole_lot": False,
        },
        {
            "filename": "tag.jpg",
            "photo_role": "angle_only",
            "label_legibility": "clear",
            "brand_text": "ST. JOHN'S BAY",
            "quality": "sharp",
            "confidence": "high",
            "shows_whole_lot": False,
        },
    ]
    selected = select_analysis_photos(triage)
    assert selected["hero_filename"] == "hero.jpg"
    assert selected["front_filenames"] == ["front.jpg"]
    assert "angle.jpg" in selected["catalog_only"]
    assert "angle.jpg" not in selected["pro_filenames"]
    assert "tag.jpg" in selected["pro_filenames"]
    assert selected["hero_triage_metadata"] is not None
    assert selected["hero_triage_metadata"]["filename"] == "hero.jpg"
    assert selected["hero_triage_metadata"]["photo_role"] == "hero"


def test_build_pro_image_payload_puts_hero_first() -> None:
    images = {
        "hero.jpg": b"hero-bytes",
        "front.jpg": b"front-bytes",
        "label.jpg": b"label-bytes",
    }
    payload = _build_pro_image_payload(
        images,
        ["label.jpg", "front.jpg"],
        hero_filename="hero.jpg",
    )
    assert list(payload.keys()) == ["hero.jpg", "label.jpg", "front.jpg"]


def test_brands_from_triage_infers_label_makers() -> None:
    triage = [
        {
            "filename": "tag.jpg",
            "label_legibility": "clear",
            "brand_text": "ST. JOHN'S BAY 100% POLYESTER MADE IN CHINA",
        },
        {
            "filename": "tag2.jpg",
            "label_legibility": "clear",
            "brand_text": "Fashion Accessory by Mirabeau",
        },
        {
            "filename": "tag3.jpg",
            "label_legibility": "partial",
            "brand_text": "SHIBOYUAN SCARF",
        },
    ]
    brands = _brands_from_triage_and_draft(triage, None)
    assert "St. John's Bay" in brands
    assert "Mirabeau" in brands
    assert "Shiboyuan" in brands


def test_lot_from_flash_draft_uses_brand_audit() -> None:
    flash_draft = {
        "items": [
            {
                "temporary_id": "ITEM_1",
                "suggested_title": "Levi's denim jacket",
                "raw_ocr_text_found": "Levi's",
                "primary_front_filename": "front.jpg",
            }
        ]
    }
    brand_audit = {"brands": ["Levi's"], "brand_needs_review": False}
    lot = _lot_from_flash_draft(flash_draft, brand_audit)
    assert lot.brands == ["Levi's"]
    assert "Levi's" in lot.auction_title or "denim" in lot.auction_title.lower()
