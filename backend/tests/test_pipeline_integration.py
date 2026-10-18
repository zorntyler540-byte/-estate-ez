"""Tests for estate pipeline integration helpers (no live Gemini calls)."""

from __future__ import annotations

from app.services.pipeline_integration import _brands_from_triage_and_draft, _lot_from_flash_draft
from pipeline.pipeline import (
    _build_pro_image_payload,
    aggregate_triage_ocr,
    is_complex_lot_for_pro_author,
    select_analysis_photos,
)


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


def _triage_row(filename: str, **overrides: object) -> dict:
    row: dict = {
        "filename": filename,
        "photo_role": "angle_only",
        "label_legibility": "none",
        "quality": "sharp",
        "confidence": "high",
        "shows_whole_lot": False,
        "is_primary_front_for_item": False,
    }
    row.update(overrides)
    return row


def test_select_analysis_photos_never_picks_tape_as_hero() -> None:
    """A tape frame must not win hero even when Gemini also sets shows_whole_lot."""
    triage = [
        _triage_row(
            "tape.jpg",
            photo_role="tape",
            shows_whole_lot=True,
            lot_tape_marking="MS777",
            label_legibility="clear",
        ),
        _triage_row("group.jpg", photo_role="hero", shows_whole_lot=True),
        _triage_row(
            "front.jpg",
            photo_role="item_front",
            distinct_item_id="ITEM_1",
            is_primary_front_for_item=True,
            label_legibility="clear",
            brand_text="Levi's",
        ),
    ]
    selected = select_analysis_photos(triage)
    assert selected["hero_filename"] == "group.jpg"
    assert "tape.jpg" in selected["tape_filenames"]
    assert selected["hero_triage_metadata"]["filename"] == "group.jpg"
    assert selected["hero_triage_metadata"]["photo_role"] == "hero"


def test_select_analysis_photos_demotes_mis_tagged_tape_hero() -> None:
    """Tape close-up labeled hero (no whole-lot) is persisted as tape; group shot becomes hero."""
    triage = [
        _triage_row(
            "tape.jpg",
            photo_role="hero",
            shows_whole_lot=False,
            lot_tape_marking="MS777",
            label_legibility="clear",
        ),
        _triage_row(
            "group.jpg",
            photo_role="item_front",
            shows_whole_lot=True,
            distinct_item_id="ITEM_1",
            is_primary_front_for_item=True,
            label_legibility="clear",
            brand_text="Levi's",
        ),
    ]
    selected = select_analysis_photos(triage)
    assert selected["hero_filename"] == "group.jpg"
    by_name = {r["filename"]: r for r in triage}
    assert by_name["tape.jpg"]["photo_role"] == "tape"
    assert by_name["group.jpg"]["photo_role"] == "hero"
    assert "tape.jpg" in selected["tape_filenames"]


def test_select_analysis_photos_demotes_mis_tagged_tape_item_front() -> None:
    """Handwritten SKU-tape close-up tagged item_front is persisted as tape (TG371AD)."""
    triage = [
        _triage_row(
            "mobile-01.jpg",
            photo_role="item_front",
            shows_whole_lot=False,
            lot_tape_marking="TG371AD",
            label_legibility="clear",
            brand_text="TG371AD",
            is_primary_front_for_item=True,
            distinct_item_id="ITEM_1",
        ),
        _triage_row(
            "mobile-02.jpg",
            photo_role="hero",
            shows_whole_lot=False,
            distinct_item_id="BROOCH",
            is_primary_front_for_item=True,
            label_legibility="none",
        ),
    ]
    selected = select_analysis_photos(triage)
    by_name = {r["filename"]: r for r in triage}
    assert by_name["mobile-01.jpg"]["photo_role"] == "tape"
    assert selected["hero_filename"] == "mobile-02.jpg"
    assert "mobile-01.jpg" in selected["tape_filenames"]
    assert "mobile-01.jpg" not in selected["listing_filenames"]


def test_select_analysis_photos_keeps_group_hero_with_tape_at_edge() -> None:
    """A real group shot with SKU tape visible at the edge stays hero."""
    triage = [
        _triage_row(
            "tape.jpg",
            photo_role="tape",
            lot_tape_marking="MS777",
            label_legibility="clear",
        ),
        _triage_row(
            "group.jpg",
            photo_role="hero",
            shows_whole_lot=True,
            lot_tape_marking="MS777",
        ),
    ]
    selected = select_analysis_photos(triage)
    assert selected["hero_filename"] == "group.jpg"
    by_name = {r["filename"]: r for r in triage}
    assert by_name["group.jpg"]["photo_role"] == "hero"
    assert by_name["tape.jpg"]["photo_role"] == "tape"


def test_select_analysis_photos_tape_only_lot_has_no_hero() -> None:
    triage = [
        _triage_row(
            "tape.jpg",
            photo_role="tape",
            shows_whole_lot=True,
            lot_tape_marking="MS777",
            label_legibility="clear",
        ),
    ]
    selected = select_analysis_photos(triage)
    assert selected["hero_filename"] == ""
    assert selected["brand_needs_review"] is True
    assert selected["tape_filenames"] == ["tape.jpg"]


def test_select_analysis_photos_routes_measuring_scale_frames_to_listing_and_pro() -> None:
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
            "filename": "ruler.jpg",
            "photo_role": "angle_only",
            "shows_measuring_scale": True,
            "label_legibility": "none",
            "quality": "sharp",
            "confidence": "high",
            "shows_whole_lot": False,
        },
        {
            "filename": "angle.jpg",
            "photo_role": "angle_only",
            "shows_measuring_scale": False,
            "label_legibility": "none",
            "quality": "ok",
            "confidence": "high",
            "shows_whole_lot": False,
        },
    ]
    selected = select_analysis_photos(triage)
    assert "ruler.jpg" in selected["listing_filenames"]
    assert "ruler.jpg" in selected["pro_filenames"]
    assert "ruler.jpg" not in selected["catalog_only"]
    assert "angle.jpg" in selected["catalog_only"]


def test_select_analysis_photos_guarantees_maker_foil_sticker_for_pro() -> None:
    """Bottom foil with 22 KT GOLD must reach Pro even when blue tape ranks higher by OCR length."""
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
            "filename": "tape.jpg",
            "photo_role": "tape",
            "label_legibility": "clear",
            "brand_text": "$22.00 22kt gold estate sale handwritten blue masking tape lot marking",
            "quality": "sharp",
            "confidence": "high",
            "shows_whole_lot": False,
        },
        {
            "filename": "front.jpg",
            "photo_role": "item_front",
            "distinct_item_id": "ITEM_1",
            "is_primary_front_for_item": True,
            "label_legibility": "partial",
            "brand_text": "Love Hope Peace Joy",
            "quality": "sharp",
            "confidence": "high",
            "shows_whole_lot": False,
        },
        {
            "filename": "foil_bottom.jpg",
            "photo_role": "angle_only",
            "label_legibility": "clear",
            "brand_text": "Hand-Sculpted Glass 22 KT GOLD UG",
            "quality": "sharp",
            "confidence": "high",
            "shows_whole_lot": False,
        },
    ]
    selected = select_analysis_photos(triage)
    assert selected["maker_sticker_filename"] == "foil_bottom.jpg"
    assert "foil_bottom.jpg" in selected["pro_filenames"]
    assert "foil_bottom.jpg" not in selected["catalog_only"]


def test_pro_score_boosts_maker_foil_over_longer_non_maker_ocr() -> None:
    from pipeline.pipeline import PhotoTriageResult, _pro_score

    tape = PhotoTriageResult.model_validate(
        {
            "filename": "tape.jpg",
            "photo_role": "tape",
            "label_legibility": "clear",
            "brand_text": "x" * 80,
            "quality": "sharp",
            "confidence": "high",
        }
    )
    foil = PhotoTriageResult.model_validate(
        {
            "filename": "foil.jpg",
            "photo_role": "angle_only",
            "label_legibility": "clear",
            "brand_text": "Hand-Sculpted Glass 22 KT GOLD UG",
            "quality": "sharp",
            "confidence": "high",
        }
    )
    assert _pro_score(foil) > _pro_score(tape)


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


def test_aggregate_triage_ocr_dedupes_and_includes_tape() -> None:
    triage = [
        {
            "filename": "tag.jpg",
            "photo_role": "item_front",
            "label_legibility": "clear",
            "brand_text": "BROWN BESS Pedersoli Cal. 80",
            "quality": "sharp",
            "confidence": "high",
        },
        {
            "filename": "lock.jpg",
            "photo_role": "angle_only",
            "label_legibility": "partial",
            "brand_text": "PEDERSOLI MADE IN ITALY",
            "quality": "sharp",
            "confidence": "high",
        },
        {
            "filename": "tape.jpg",
            "photo_role": "tape",
            "label_legibility": "clear",
            "brand_text": "",
            "lot_tape_marking": "SA1000",
            "quality": "sharp",
            "confidence": "high",
        },
    ]
    rows = aggregate_triage_ocr(triage)
    assert len(rows) == 3
    assert any("Pedersoli" in r["text"] for r in rows)
    assert any("lot tape: SA1000" in r["text"] for r in rows)


def test_is_complex_lot_many_photos_or_maker_marks() -> None:
    many = [
        {
            "filename": f"p{i}.jpg",
            "photo_role": "angle_only",
            "label_legibility": "none",
            "quality": "ok",
            "confidence": "high",
        }
        for i in range(12)
    ]
    assert is_complex_lot_for_pro_author(many, {"brand_needs_review": False}) is True

    musket = [
        {
            "filename": "tag.jpg",
            "photo_role": "item_front",
            "label_legibility": "clear",
            "brand_text": "BROWN BESS Cal. 80 Pedersoli #10908 with sling",
            "quality": "sharp",
            "confidence": "high",
        },
        {
            "filename": "lock.jpg",
            "photo_role": "angle_only",
            "label_legibility": "clear",
            "brand_text": "PEDERSOLI TOWER GR",
            "quality": "sharp",
            "confidence": "high",
        },
    ]
    selected = select_analysis_photos(musket)
    assert is_complex_lot_for_pro_author(musket, selected) is True

    simple = [
        {
            "filename": "hero.jpg",
            "photo_role": "hero",
            "shows_whole_lot": True,
            "label_legibility": "none",
            "quality": "sharp",
            "confidence": "high",
        },
        {
            "filename": "tag.jpg",
            "photo_role": "item_front",
            "label_legibility": "clear",
            "brand_text": "Levi's",
            "quality": "sharp",
            "confidence": "high",
            "is_primary_front_for_item": True,
            "distinct_item_id": "ITEM_1",
        },
    ]
    sel = select_analysis_photos(simple)
    assert is_complex_lot_for_pro_author(simple, sel) is False

