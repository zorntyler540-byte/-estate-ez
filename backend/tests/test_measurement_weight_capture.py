"""Measurement + weight capture consistency (frame selection + deterministic spec injection)."""

from __future__ import annotations

from app.core.lotting import LotOutput
from app.services.pipeline_integration import _inject_measurement_specs_from_triage
from pipeline.pipeline import select_analysis_photos


def _triage_row(filename: str, **overrides) -> dict:
    row = {
        "filename": filename,
        "photo_role": "angle_only",
        "label_legibility": "none",
        "quality": "sharp",
        "confidence": "high",
        "shows_whole_lot": False,
    }
    row.update(overrides)
    return row


def test_weight_scale_frame_included_in_listing_and_pro() -> None:
    triage = [
        _triage_row("hero.jpg", photo_role="hero", shows_whole_lot=True),
        _triage_row("ruler.jpg", shows_measuring_scale=True, measurement_text="12 in"),
        _triage_row("scale.jpg", shows_weight_scale=True, weight_text="2.4 lb"),
    ]
    selected = select_analysis_photos(triage)
    assert "scale.jpg" in selected["weight_filenames"]
    # Weight frame must reach the authoring calls so the readout is actually seen.
    assert "scale.jpg" in selected["listing_filenames"]
    assert "scale.jpg" in selected["pro_filenames"]
    # Dimension frame path still works alongside it.
    assert "ruler.jpg" in selected["dimension_filenames"]
    assert "ruler.jpg" in selected["listing_filenames"]


def test_unusable_weight_frame_is_not_selected() -> None:
    triage = [
        _triage_row("hero.jpg", photo_role="hero", shows_whole_lot=True),
        _triage_row("blur_scale.jpg", photo_role="unusable", shows_weight_scale=True),
    ]
    selected = select_analysis_photos(triage)
    assert "blur_scale.jpg" not in selected["weight_filenames"]


def test_inject_adds_dimensions_and_weight_when_missing() -> None:
    lot = LotOutput.from_model(
        {"item_name": "Cast Iron Skillet", "specification_lines": ["Brand: Lodge"]}
    )
    triage = [
        {"filename": "a.jpg", "measurement_text": "10 in", "weight_text": None},
        {"filename": "b.jpg", "measurement_text": None, "weight_text": "3.2 lb"},
    ]
    _inject_measurement_specs_from_triage(lot, triage)
    specs = [s.lower() for s in lot.specification_lines]
    assert any(s.startswith("dimensions:") and "10 in" in s for s in specs)
    assert any(s.startswith("weight:") and "3.2 lb" in s for s in specs)


def test_inject_does_not_override_existing_lines() -> None:
    lot = LotOutput.from_model(
        {
            "item_name": "Cast Iron Skillet",
            "specification_lines": ["Dimensions: 9 in", "Weight: 2 lb"],
        }
    )
    triage = [{"filename": "a.jpg", "measurement_text": "10 in", "weight_text": "3.2 lb"}]
    _inject_measurement_specs_from_triage(lot, triage)
    specs = [s.lower() for s in lot.specification_lines]
    # Authoring model's own reads win; we do not add a second/conflicting line.
    assert any("9 in" in s for s in specs)
    assert not any("10 in" in s for s in specs)
    assert sum(1 for s in specs if s.startswith("dimensions:")) == 1
    assert sum(1 for s in specs if s.startswith("weight:")) == 1


def test_inject_noop_without_reads() -> None:
    lot = LotOutput.from_model(
        {"item_name": "Plain Item", "specification_lines": ["Brand: Acme"]}
    )
    before = list(lot.specification_lines)
    _inject_measurement_specs_from_triage(lot, [{"filename": "a.jpg"}])
    assert lot.specification_lines == before
