"""Listing-model comparison: fixtures, concurrency, rates, and the report."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from app.core.gemini_rates import CURRENT, JAN_2027, estimate_usd
from app.services.listing_compare import (
    DEFAULT_COMPARE_MODELS,
    OPTIONAL_COMPARE_MODEL,
    load_sample_lots,
    render_csv,
    render_markdown,
    run_comparison,
    write_report,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "scripts" / "fixtures" / "listing_samples"


def test_flash_rates_match_the_january_2027_notice() -> None:
    assert estimate_usd("gemini-3.8-flash", 1_000_000, 1_000_000, schedule=CURRENT) == pytest.approx(4.50)
    assert estimate_usd("gemini-3.6-flash", 1_000_000, 0, schedule=CURRENT) == pytest.approx(0.75)
    assert estimate_usd("gemini-3.8-flash", 1_000_000, 1_000_000, schedule=JAN_2027) == pytest.approx(9.00)
    assert estimate_usd("gemini-3.6-flash", 2_000_000, 1_000_000, schedule=JAN_2027) == pytest.approx(10.50)
    # Pro is unchanged across the Flash increase. Short-prompt analytics rate.
    assert estimate_usd("gemini-2.5-pro", 1_000_000, 1_000_000, schedule=CURRENT) == pytest.approx(11.25)
    assert estimate_usd("gemini-2.5-pro", 1_000_000, 1_000_000, schedule=JAN_2027) == pytest.approx(11.25)
    assert estimate_usd("gemini-2.5-flash", 100, 100, schedule=CURRENT) is None
    assert estimate_usd("gemini-3.8-flash", None, 10, schedule=CURRENT) is None


def test_default_compare_models() -> None:
    assert DEFAULT_COMPARE_MODELS == ("gemini-2.5-pro", "gemini-3.8-flash")
    assert OPTIONAL_COMPARE_MODEL == "gemini-3.6-flash"


def test_fixtures_are_local_and_feed_the_production_prompt() -> None:
    lots = load_sample_lots(_FIXTURES)
    assert [lot.lot_id for lot in lots] == ["sample-knives", "sample-levis"]
    knives = lots[0]
    assert "DEER HOOF HANDLE" in json.dumps(knives.triage_ocr)
    assert knives.images["hero.jpg"][:3] == b"\xff\xd8\xff"
    from app.services.listing_compare import _listing_parts

    parts, config = _listing_parts(lots[1])
    text = "\n".join(part for part in parts if isinstance(part, str))
    assert "LEVI STRAUSS" in text
    assert "final_auction_title" in text or "Write the CTBids" in text
    assert config is not None


def test_models_run_concurrently_and_report_side_by_side(tmp_path: Path) -> None:
    lots = load_sample_lots(_FIXTURES / "sample-knives")
    models = ["gemini-2.5-pro", "gemini-3.8-flash"]
    barrier = threading.Barrier(len(models), timeout=5)
    started: list[str] = []

    def generate(model, parts, config):
        started.append(model)
        barrier.wait()

        class _Meta:
            prompt_token_count = 1_000_000
            candidates_token_count = 1_000_000

        class _Resp:
            text = json.dumps(
                {
                    "final_auction_title": f"Title from {model}",
                    "final_professional_description": f"Description from {model}",
                }
            )
            parsed = None
            usage_metadata = _Meta()

        assert any(isinstance(part, str) for part in parts)
        assert config is not None
        return _Resp()

    rows = run_comparison(lots, models, generate=generate, max_workers=2)
    assert sorted(started) == sorted(models)
    assert all(not row.error for row in rows)
    by_model = {row.model: row for row in rows}
    assert by_model["gemini-2.5-pro"].cost_current_usd == pytest.approx(11.25)
    assert by_model["gemini-3.8-flash"].cost_current_usd == pytest.approx(4.50)
    assert by_model["gemini-3.8-flash"].cost_2027_usd == pytest.approx(9.00)
    assert by_model["gemini-2.5-pro"].cost_2027_usd == pytest.approx(11.25)
    md_path, csv_path = write_report(
        rows,
        tmp_path,
        models=models,
        generated_at="2026-11-06T00:00:00Z",
    )
    markdown = md_path.read_text(encoding="utf-8")
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "Title from gemini-2.5-pro" in markdown
    assert "Title from gemini-3.8-flash" in markdown
    assert "Description from gemini-3.8-flash" in markdown
    assert "$4.500000" in markdown
    assert "$9.000000" in markdown
    assert "gemini-2.5-pro" in csv_text and "gemini-3.8-flash" in csv_text
    assert "Batch API is not used" in markdown
    assert "GOOGLE_API_KEY" not in markdown
    assert "AIza" not in markdown


def test_dry_run_does_not_call_generate(tmp_path: Path) -> None:
    lots = load_sample_lots(_FIXTURES)
    called = {"n": 0}

    def generate(model, parts, config):
        called["n"] += 1
        raise AssertionError("dry-run must not call Gemini")

    rows = run_comparison(
        lots,
        list(DEFAULT_COMPARE_MODELS),
        generate=generate,
        max_workers=4,
        dry_run=True,
    )
    assert called["n"] == 0
    assert len(rows) == len(lots) * len(DEFAULT_COMPARE_MODELS)
    assert all(row.dry_run and row.prompt_chars > 0 for row in rows)
    markdown = render_markdown(rows, models=list(DEFAULT_COMPARE_MODELS), generated_at="t")
    assert "Dry run" in markdown
    assert "sample-knives" in markdown and "sample-levis" in markdown
    assert "lot_id" in render_csv(rows)
