"""Audience hunt gate, payload parse, and CT Seller hold — no live Gemini."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.audience_hunt import (
    add_audience_usage,
    apply_skip_without_model,
    audience_usage_tokens,
    hunt_pro_cost_usd,
    is_us_audience_group,
    lot_has_audience_research,
    lot_is_held_from_ctseller,
    parse_audience_payload,
    plan_sale_audience_hunts,
    run_audience_hunt_for_lot,
    should_run_audience_hunt,
    summarize_hunt_events,
)


def test_should_run_audience_hunt_skips_generic_household() -> None:
    ok, reason = should_run_audience_hunt(
        title="Box of towels",
        brands=[],
        category="Household Goods Other",
    )
    assert ok is False
    assert "Everyday" in reason or "household" in reason.lower()


def test_should_run_audience_hunt_skips_empty_category_no_brand() -> None:
    ok, _reason = should_run_audience_hunt(title="Assorted items", brands=[], category=None)
    assert ok is False


def test_should_run_audience_hunt_with_brand() -> None:
    ok, reason = should_run_audience_hunt(
        title="Lamp",
        brands=["Tiffany"],
        category="Household Goods Other",
    )
    assert ok is True
    assert reason == ""


def test_should_run_audience_hunt_collector_category() -> None:
    ok, _reason = should_run_audience_hunt(title="Vase", brands=[], category="Art & Antiques")
    assert ok is True


def test_should_run_audience_hunt_title_hint() -> None:
    ok, _reason = should_run_audience_hunt(
        title="Vintage Fenton glass basket",
        brands=[],
        category="Household Goods Other",
    )
    assert ok is True


def test_should_run_audience_hunt_force() -> None:
    ok, _reason = should_run_audience_hunt(
        title="Towels",
        brands=[],
        category="linens",
        force=True,
    )
    assert ok is True


def test_parse_audience_payload_drops_blank_groups_and_unsafe_urls() -> None:
    out = parse_audience_payload(
        {
            "worthy": True,
            "groups": [
                {"name": ""},
                {"name": "Fenton Glass Collectors", "url": "javascript:alert(1)", "why": "niche"},
                {"name": "Depression Glass group", "url": "www.facebook.com/groups/example", "why": "era"},
            ],
            "blurb_ctbids": "Come bid.",
            "recommend_hold": True,
        }
    )
    assert len(out["groups"]) == 2
    assert out["groups"][0]["url"] == ""
    assert out["groups"][1]["url"] == "https://www.facebook.com/groups/example"
    assert out["worthy"] is True
    assert out["recommend_hold"] is True


def test_parse_audience_payload_drops_international_groups() -> None:
    out = parse_audience_payload(
        {
            "worthy": True,
            "recommend_hold": True,
            "groups": [
                {
                    "name": "UK Fenton Glass Collectors",
                    "url": "https://www.facebook.com/groups/ukfenton",
                    "why": "UK club",
                },
                {
                    "name": "Canadian Depression Glass",
                    "url": "https://www.facebook.com/groups/canadianglass",
                    "why": "Canada",
                },
                {
                    "name": "Worldwide Glass Collectors",
                    "url": "https://www.facebook.com/groups/worldwideglass",
                    "why": "international",
                },
                {
                    "name": "British Glass Society",
                    "url": "https://www.britishglass.co.uk/collectors",
                    "why": "club site",
                },
                {
                    "name": "Fenton Glass Collectors USA",
                    "url": "https://www.facebook.com/groups/fentonglassusa",
                    "why": "US buyers; English hobnail OK",
                },
                {
                    "name": "Mid-Century Modern Collectors",
                    "url": "https://www.facebook.com/groups/mcmusa",
                    "why": "collects Japanese pottery and English china",
                },
            ],
        }
    )
    names = {g["name"] for g in out["groups"]}
    assert names == {"Fenton Glass Collectors USA", "Mid-Century Modern Collectors"}
    assert out["worthy"] is True
    assert out["recommend_hold"] is True


def test_parse_audience_payload_all_foreign_clears_worthy() -> None:
    out = parse_audience_payload(
        {
            "worthy": True,
            "recommend_hold": True,
            "groups": [{"name": "Australia Glass Club", "url": "https://glass.org.au/club", "why": "AU"}],
        }
    )
    assert out["groups"] == []
    assert out["worthy"] is False
    assert out["recommend_hold"] is False


def test_is_us_audience_group_keeps_object_origin_words() -> None:
    assert is_us_audience_group(
        name="English China Collectors",
        url="https://www.facebook.com/groups/englishchina",
        why="US group for English bone china",
    )
    assert not is_us_audience_group(
        name="English China Collectors UK",
        url="https://www.facebook.com/groups/englishchinauk",
        why="based in the United Kingdom",
    )
    assert not is_us_audience_group(
        name="Glass Club",
        url="https://collectors.example.ca/forum",
        why="forum",
    )


def test_parse_audience_payload_invalid_json() -> None:
    out = parse_audience_payload("{not-json")
    assert out["groups"] == []
    assert out["worthy"] is False


def test_lot_is_held_from_ctseller() -> None:
    assert lot_is_held_from_ctseller(SimpleNamespace(audience_hold=True)) is True
    assert lot_is_held_from_ctseller(SimpleNamespace(audience_hold=False)) is False
    assert lot_is_held_from_ctseller(SimpleNamespace()) is False


def test_apply_skip_without_model() -> None:
    lot = SimpleNamespace(audience_json="{}")
    payload = apply_skip_without_model(lot, "Everyday household lot — no collector hunt.")
    assert payload["worthy"] is False
    assert "Everyday" in payload["skip_reason"]
    saved = json.loads(lot.audience_json)
    assert saved["skip_reason"].startswith("Everyday")
    assert saved["groups"] == []


def test_run_audience_hunt_for_lot_skips_without_model(monkeypatch) -> None:
    """Generic household skip must not call Gemini."""
    lot = SimpleNamespace(
        auction_title="Box of towels",
        brands_json="[]",
        ct_category="Household Goods Other",
        audience_hold=False,
        audience_json="{}",
    )

    def _fail_research(_lot):
        raise AssertionError("Gemini must not run on skipped household lots")

    monkeypatch.setattr("app.services.audience_hunt.run_audience_research_and_structure", _fail_research)
    payload = run_audience_hunt_for_lot(SimpleNamespace(), lot, force=False)
    assert payload["worthy"] is False
    assert payload["groups"] == []
    assert lot.audience_hold is False


def test_lot_has_audience_research() -> None:
    empty = SimpleNamespace(audience_json="{}")
    assert lot_has_audience_research(empty) is False
    hunted = SimpleNamespace(
        audience_json=json.dumps({"researched_at": "2026-01-01T00:00:00Z", "groups": []})
    )
    assert lot_has_audience_research(hunted) is True
    skipped = SimpleNamespace(
        audience_json=json.dumps({"skip_reason": "Everyday household lot — no collector hunt."})
    )
    assert lot_has_audience_research(skipped) is True


def test_plan_sale_audience_hunts_skips_household_and_already_hunted() -> None:
    household = SimpleNamespace(
        id="h1",
        sku="HH-01",
        auction_title="Box of towels",
        brands_json="[]",
        ct_category="Household Goods Other",
        audience_json="{}",
    )
    fenton = SimpleNamespace(
        id="f1",
        sku="FN-12",
        auction_title="Vintage Fenton glass basket",
        brands_json=json.dumps(["Fenton"]),
        ct_category="Glass",
        audience_json="{}",
    )
    already = SimpleNamespace(
        id="f2",
        sku="FN-13",
        auction_title="Fenton vase",
        brands_json=json.dumps(["Fenton"]),
        ct_category="Glass",
        audience_json=json.dumps({"researched_at": "2026-01-01T00:00:00Z", "groups": [{"name": "x"}]}),
    )
    queued, skipped = plan_sale_audience_hunts(
        [household, fenton, already],
        force=False,
        only_unhunted=True,
        limit=40,
    )
    assert queued == ["f1"]
    reasons = {lot_id: reason for lot_id, _sku, reason in skipped}
    assert "h1" in reasons
    assert "f2" in reasons
    assert "Already hunted" in reasons["f2"]


def test_audience_usage_tokens_includes_thinking() -> None:
    class _Meta:
        prompt_token_count = 1200
        candidates_token_count = 400
        thoughts_token_count = 8000

    class _Resp:
        usage_metadata = _Meta()

    inp, out = audience_usage_tokens(_Resp())
    assert inp == 1200
    assert out == 8400
    usage = {"pro_in": 0, "pro_out": 0, "search_calls": 0}
    add_audience_usage(usage, _Resp(), search=True)
    assert usage["search_calls"] == 1
    assert usage["pro_in"] == 1200
    assert usage["pro_out"] == 8400


def test_audience_usage_tokens_missing_metadata() -> None:
    assert audience_usage_tokens(object()) == (0, 0)


def test_summarize_hunt_events_and_cost() -> None:
    run = SimpleNamespace(
        skipped=False,
        error=None,
        group_count=4,
        search_calls=1,
        actual_pro_input_tokens=2000,
        actual_pro_output_tokens=8000,
        created_at="2026-08-01",
    )
    skip = SimpleNamespace(
        skipped=True,
        error=None,
        group_count=0,
        search_calls=0,
        actual_pro_input_tokens=0,
        actual_pro_output_tokens=0,
        created_at="2026-08-02",
    )
    fail = SimpleNamespace(
        skipped=False,
        error="RuntimeError: boom",
        group_count=0,
        search_calls=1,
        actual_pro_input_tokens=500,
        actual_pro_output_tokens=100,
        created_at="2026-08-03",
    )
    summary = summarize_hunt_events([run, skip, fail])
    assert summary["hunts_run"] == 1
    assert summary["hunts_skipped"] == 1
    assert summary["hunts_failed"] == 1
    assert summary["hunt_groups_found"] == 4
    assert summary["hunt_search_calls"] == 2
    assert summary["hunt_pro_input_tokens"] == 2500
    assert summary["hunt_pro_output_tokens"] == 8100
    # $1.25/M in + $10/M out
    cost = hunt_pro_cost_usd(pro_in=2500, pro_out=8100, in_rate=1.25, out_rate=10.0)
    assert cost == round(2500 / 1_000_000 * 1.25 + 8100 / 1_000_000 * 10.0, 4)
