"""Audience hunt gate, payload parse, and CT Seller hold — no live Gemini."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.audience_hunt import (
    add_audience_usage,
    apply_skip_without_model,
    audience_usage_tokens,
    hunt_pro_cost_usd,
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


def test_sale_hunt_status_tracks_phases_and_counts() -> None:
    from app import audience_tasks

    audience_tasks.reset_hunt_runtime_state()
    audience_tasks.register_sale_hunt(
        sale_id="s1",
        account_id="a1",
        lot_ids=["l1", "l2", "l3"],
        skus={"l1": "A-1", "l2": "A-2", "l3": "A-3"},
    )
    snap = audience_tasks.sale_hunt_status(sale_id="s1", account_id="a1")
    assert snap["active"] is True
    assert snap["total"] == 3
    assert snap["done"] == 0
    assert snap["current_lot_id"] == "l1"
    assert snap["lots"][0]["status"] == "running"
    assert snap["lots"][1]["status"] == "queued"

    audience_tasks.set_lot_hunt_phase("l1", "searching")
    snap = audience_tasks.sale_hunt_status(sale_id="s1", account_id="a1")
    assert snap["current_phase"] == "searching"

    audience_tasks.set_lot_hunt_phase("l1", "structuring")
    snap = audience_tasks.sale_hunt_status(sale_id="s1", account_id="a1")
    assert snap["current_phase"] == "structuring"
    assert snap["lots"][0]["phase"] == "structuring"

    audience_tasks.set_lot_hunt_phase("l1", None)
    audience_tasks._mark_sale_lot_finished(sale_id="s1", lot_id="l1")
    snap = audience_tasks.sale_hunt_status(sale_id="s1", account_id="a1")
    assert snap["done"] == 1
    assert snap["current_lot_id"] == "l2"
    assert snap["lots"][0]["status"] == "done"
    assert snap["active"] is True

    audience_tasks._mark_sale_lot_finished(sale_id="s1", lot_id="l2")
    audience_tasks._mark_sale_lot_finished(sale_id="s1", lot_id="l3")
    snap = audience_tasks.sale_hunt_status(sale_id="s1", account_id="a1")
    assert snap["active"] is False
    assert snap["done"] == 3
    assert snap["current_lot_id"] is None
    assert all(row["status"] == "done" for row in snap["lots"])

    empty = audience_tasks.sale_hunt_status(sale_id="s1", account_id="other")
    assert empty["active"] is False
    assert empty["total"] == 0

    missing = audience_tasks.sale_hunt_status(sale_id="nope", account_id="a1")
    assert missing["total"] == 0


def test_run_audience_research_reports_phases(monkeypatch) -> None:
    from app.services import audience_hunt as hunt_mod

    phases: list[str] = []

    class _Resp:
        text = "Public Fenton glass group: https://www.facebook.com/groups/example"
        parsed = None
        usage_metadata = None

    class _Models:
        def generate_content(self, **_kwargs):
            return _Resp()

    class _Client:
        models = _Models()

    monkeypatch.setattr(hunt_mod, "_ensure_gemini_env", lambda: None)
    monkeypatch.setattr("pipeline.pipeline._get_client", lambda: _Client())
    monkeypatch.setattr(
        "pipeline.gemini_schema.pydantic_model_to_gemini_schema",
        lambda _model: {"type": "object"},
    )

    class _Parsed:
        def model_dump(self):
            return {
                "worthy": True,
                "groups": [{"name": "Fenton Glass Collectors", "url": "https://www.facebook.com/groups/example", "why": "brand"}],
                "blurb_ctbids": "Come bid.",
                "blurb_hold": "Hold this glass.",
                "recommend_hold": False,
            }

        @classmethod
        def model_validate(cls, _data):
            return cls()

    monkeypatch.setattr("pipeline.schemas.AudienceHuntResult", _Parsed)

    lot = SimpleNamespace(
        auction_title="Fenton vase",
        sku="FN-1",
        ct_category="Glass",
        brands_json='["Fenton"]',
        specification_lines_json="[]",
        condition_text="good",
        professional_description="hobnail",
    )
    _Resp.parsed = _Parsed()
    hunt_mod.run_audience_research_and_structure(lot, on_phase=phases.append)
    assert phases == ["searching", "structuring"]
