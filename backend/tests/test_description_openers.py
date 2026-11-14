"""Opener normalization and the within-sale first-sentence rewrite."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings, settings
from app.core.gemini import SYSTEM_INSTRUCTION
from app.db import Base
from app.models import Account, Lot, Sale, User
from app.services.description_openers import (
    OVERUSED_DESCRIPTION_OPENERS,
    build_opener_rewrite_config,
    description_opener_prompt_clause,
    listing_author_model,
    load_sale_descriptions,
    matched_opener_phrases,
    maybe_rewrite_description_opener,
    opening_key,
    opening_stem,
    opener_issue_score,
    opener_rewrite_system_instruction,
    replace_first_sentence,
    rewrite_reason,
    sale_description_loader,
    split_first_sentence,
)
from pipeline.pipeline import BRAND_AUDIT_SYSTEM, PRO_AUTHOR_DESCRIPTION_GUIDANCE

GRUEN = (
    "Add a touch of classic sparkle to your look—this Gruen Precision wristwatch "
    "keeps excellent time. Minor desk wear on the lugs."
)
NAPIER = (
    "A perfect statement piece for any collection—this Napier link bracelet stacks easily. "
    "The clasp is snug."
)
NAPIER_QUESTION = (
    "Who wouldn't want this Napier link bracelet stacked at the wrist? The clasp is snug."
)
TIGER_REPEATED = (
    "Add a touch of classic sparkle to your look—this enamel tiger brooch is ready for a lapel. "
    "The stones are bright."
)
TIGER_FRESH = "Ready to pounce—this enamel tiger brooch has its eye on the lapel!"
TIGER_AFTER = (
    "Ready to pounce—this enamel tiger brooch has its eye on the lapel! The stones are bright."
)
JEWELRY_SALE = [GRUEN, NAPIER_QUESTION]


class _Resp:
    def __init__(self, sentence: str, *, parsed=None, text: str | None = None):
        self.parsed = parsed
        if text is not None:
            self.text = text
        else:
            self.text = json.dumps({"first_sentence": sentence})


def _generate(sentence: str):
    calls: list[dict] = []

    def generate(*, model, contents, config):
        calls.append({"model": model, "contents": contents, "config": config})
        return _Resp(sentence)

    return generate, calls


def _assert_new_call_shape(config, contents) -> None:
    assert isinstance(contents, str)
    assert '"role"' not in contents
    dumped = config.model_dump(exclude_unset=True)
    assert dumped.get("response_mime_type") == "application/json" or dumped.get("responseMimeType") == "application/json"
    assert "temperature" not in dumped
    assert "thinking_config" not in dumped
    assert "thinking_budget" not in json.dumps(dumped)
    assert "first_sentence" in json.dumps(dumped)


def test_opening_key_normalizes_case_punctuation_and_dashes() -> None:
    assert opening_key(GRUEN) == "add a touch of"
    assert opening_stem(GRUEN) == "add a touch"
    assert opening_key("  ADD   a touch, of classic sparkle!") == "add a touch of"
    assert opening_key('"Gruen Precision wristwatch, in gold!"') == "gruen precision wristwatch in"
    assert split_first_sentence(GRUEN)[1] == "Minor desk wear on the lugs."
    assert split_first_sentence("look—this Gruen Precision keeps time")[1] == ""


def test_sentence_split_keeps_abbreviations() -> None:
    first, rest = split_first_sentence("St. John's Bay cotton jacket in olive. Zipper works.")
    assert first == "St. John's Bay cotton jacket in olive."
    assert rest == "Zipper works."
    first, rest = split_first_sentence("About 6 in. tall overall. The base is weighted.")
    assert first == "About 6 in. tall overall."
    assert rest == "The base is weighted."
    first, rest = split_first_sentence("U.S. Mint uncirculated set from 2003. Extra sentence.")
    assert first.startswith("U.S. Mint")
    assert rest == "Extra sentence."


def test_stock_phrases_match_whole_words_in_the_first_sentence_only() -> None:
    assert matched_opener_phrases(GRUEN)[0] == "Add a touch"
    assert "A perfect" in matched_opener_phrases(NAPIER)
    assert "statement piece for any" in matched_opener_phrases(NAPIER)
    assert matched_opener_phrases("A classic choice for everyday elegance. The cotton is soft.") == ["A classic"]
    assert matched_opener_phrases("A delightful accent for the shelf.") == ["A delightful"]
    assert matched_opener_phrases("A charming little brass bell.") == ["A charming"]
    assert matched_opener_phrases("Elevate your table with this brass candlestick.") == ["Elevate"]
    assert matched_opener_phrases(TIGER_FRESH) == ["ready to"]
    assert matched_opener_phrases("A timeless Gruen Precision wristwatch with a cream dial.") == ["timeless"]
    assert matched_opener_phrases(
        "Gruen Precision wristwatch with a cream dial. A timeless look for evenings."
    ) == []
    assert matched_opener_phrases(
        "A classical guitar from Valencia, with a spruce top. The case is worn."
    ) == []
    assert rewrite_reason(GRUEN, []) is None


def test_a_stock_hook_is_kept_when_the_sale_has_not_used_it() -> None:
    assert rewrite_reason(GRUEN, [NAPIER_QUESTION]) is None
    assert rewrite_reason(NAPIER, []) is None
    assert rewrite_reason(TIGER_FRESH + " The stones are bright.", [GRUEN, NAPIER_QUESTION]) is None


def test_duplicate_and_near_duplicate_and_second_stock_phrase() -> None:
    same_four = "Gruen Precision wristwatch in a gold-tone case. The crown is unsigned."
    sibling = "Gruen Precision wristwatch in a silver-tone case, with a cream dial."
    assert opening_key(same_four) == opening_key(sibling) == "gruen precision wristwatch in"
    assert rewrite_reason(same_four, [sibling]) == "duplicate"
    assert opener_issue_score(same_four, [sibling]) == (1, 0)
    assert rewrite_reason(
        "Gold-tone Gruen Precision wristwatch with a cream dial.",
        [sibling],
    ) is None

    near = (
        "Picture this gold dial catching the light—this Napier link bracelet stacks easily. "
        "The clasp is snug."
    )
    sibling_scene = (
        "Picture this gold case on the wrist—this Gruen Precision wristwatch keeps excellent time."
    )
    assert opening_key(near) == "picture this gold dial"
    assert opening_key(sibling_scene) == "picture this gold case"
    assert opening_stem(near) == opening_stem(sibling_scene) == "picture this gold"
    assert rewrite_reason(near, [sibling_scene]) == "near-duplicate"

    second_classic = "A classic sparkle for evening—this Trifari brooch winks. The stones are clear."
    first_classic = "A classic choice for everyday elegance—this Monet brooch sits softly. The pin is straight."
    assert opening_stem(second_classic) != opening_stem(first_classic)
    assert rewrite_reason(second_classic, [first_classic]) == "stock-phrase"
    assert rewrite_reason(second_classic, []) is None


def test_replace_first_sentence_keeps_the_rest() -> None:
    assert replace_first_sentence(TIGER_REPEATED, TIGER_FRESH) == TIGER_AFTER
    rewritten = replace_first_sentence(
        TIGER_REPEATED,
        "Ready to pounce—this enamel tiger brooch has its eye on the lapel! The pin was replaced.",
    )
    assert rewritten == TIGER_AFTER


def test_repeated_jewelry_hook_stays_flavorful(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_PRO_MODEL", "gemini-2.5-pro")
    generate, calls = _generate(TIGER_FRESH)
    result = maybe_rewrite_description_opener(
        TIGER_REPEATED,
        sibling_descriptions=lambda: JEWELRY_SALE,
        title="Enamel Tiger Brooch",
        sale_id="sale-1",
        enabled=True,
        generate=generate,
    )
    assert result == TIGER_AFTER
    assert len(calls) == 1
    assert calls[0]["model"] == "gemini-2.5-pro"
    _assert_new_call_shape(calls[0]["config"], calls[0]["contents"])
    prompt = calls[0]["contents"]
    assert "equally flavorful" in prompt
    assert "plain factual opener" in prompt
    gruen_first, _rest = split_first_sentence(GRUEN)
    assert gruen_first in prompt
    assert "The stones are bright." in prompt
    assert "Add a touch (already used once in this sale)" in prompt


@pytest.mark.parametrize("model_id", ["gemini-2.5-pro", "gemini-3.8-flash"])
def test_rewrite_call_passes_the_listing_model_through(monkeypatch, model_id: str) -> None:
    monkeypatch.setenv("GEMINI_PRO_MODEL", model_id)
    assert listing_author_model() == model_id
    generate, calls = _generate(TIGER_FRESH)
    maybe_rewrite_description_opener(
        TIGER_REPEATED,
        sibling_descriptions=lambda: JEWELRY_SALE,
        enabled=True,
        generate=generate,
    )
    assert calls[0]["model"] == model_id
    _assert_new_call_shape(calls[0]["config"], calls[0]["contents"])


def test_duplicate_opening_is_passed_as_something_to_avoid(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_PRO_MODEL", "gemini-2.5-pro")
    fresh = "Picture this gold-tone dial catching the light—this Gruen Precision wristwatch is a keeper."
    generate, calls = _generate(fresh)
    original = "Gruen Precision wristwatch in a gold-tone case, with a clean dial. Minor desk wear on the lugs."
    sibling = "Gruen Precision wristwatch in a silver-tone case, with a cream dial."
    result = maybe_rewrite_description_opener(
        original,
        sibling_descriptions=lambda: [sibling, "Oak dresser with a mirrored back."],
        title="Gruen Precision Wristwatch",
        sale_id="sale-9",
        enabled=True,
        generate=generate,
    )
    assert result == (
        "Picture this gold-tone dial catching the light—this Gruen Precision wristwatch is a keeper. "
        "Minor desk wear on the lugs."
    )
    assert len(calls) == 1
    assert sibling in calls[0]["contents"]
    assert "oak dresser with a" in calls[0]["contents"]
    assert "equally flavorful" in calls[0]["contents"]
    _assert_new_call_shape(calls[0]["config"], calls[0]["contents"])


def test_second_retry_replaces_a_hook_still_used_in_the_sale() -> None:
    sentences = iter(
        [
            "A perfect statement piece for the lapel—this enamel tiger brooch gleams.",
            TIGER_FRESH,
        ]
    )
    calls: list[str] = []

    def generate(*, model, contents, config):
        calls.append(contents)
        return _Resp(next(sentences))

    result = maybe_rewrite_description_opener(
        TIGER_REPEATED,
        sibling_descriptions=lambda: [GRUEN, NAPIER],
        enabled=True,
        generate=generate,
    )
    assert len(calls) == 2
    assert "A perfect statement piece for the lapel" in calls[1]
    assert "equally flavorful" in calls[0]
    assert result == TIGER_AFTER


def test_clean_rewrite_stops_before_the_second_try() -> None:
    calls = {"n": 0}

    def generate(*, model, contents, config):
        calls["n"] += 1
        return _Resp(TIGER_FRESH)

    maybe_rewrite_description_opener(
        TIGER_REPEATED,
        sibling_descriptions=lambda: JEWELRY_SALE,
        enabled=True,
        generate=generate,
    )
    assert calls["n"] == 1


def test_retries_keep_the_original_when_nothing_is_better() -> None:
    def generate(*, model, contents, config):
        return _Resp("A classic choice for weekend wear.")

    original = "A classic choice for everyday elegance. The cotton is soft."
    sibling = "A classic choice for everyday elegance—this field jacket has a plaid lining."
    result = maybe_rewrite_description_opener(
        original,
        sibling_descriptions=lambda: [sibling],
        enabled=True,
        generate=generate,
        max_retries=2,
    )
    assert result == original


def test_model_error_keeps_the_listing() -> None:
    def generate(*, model, contents, config):
        raise RuntimeError("gemini down")

    assert (
        maybe_rewrite_description_opener(
            TIGER_REPEATED,
            sibling_descriptions=lambda: JEWELRY_SALE,
            enabled=True,
            generate=generate,
        )
        == TIGER_REPEATED
    )


def test_disabled_flag_skips_the_model_and_the_sale_read() -> None:
    def generate(*, model, contents, config):
        raise AssertionError("model should not be called")

    def load():
        raise AssertionError("siblings should not be read")

    assert (
        maybe_rewrite_description_opener(
            TIGER_REPEATED,
            sibling_descriptions=load,
            enabled=False,
            generate=generate,
        )
        == TIGER_REPEATED
    )


def test_settings_flag_defaults_on_and_can_turn_the_check_off(monkeypatch) -> None:
    assert Settings.model_fields["description_opener_rewrite"].default is True
    monkeypatch.setattr(settings, "description_opener_rewrite", False)

    def generate(*, model, contents, config):
        raise AssertionError("model should not be called")

    assert (
        maybe_rewrite_description_opener(
            TIGER_REPEATED,
            sibling_descriptions=lambda: JEWELRY_SALE,
            generate=generate,
        )
        == TIGER_REPEATED
    )


def test_distinct_jewelry_hooks_do_not_call_the_model() -> None:
    def generate(*, model, contents, config):
        raise AssertionError("model should not be called")

    tiger = TIGER_FRESH + " The stones are bright."
    for text, siblings in (
        (GRUEN, []),
        (NAPIER_QUESTION, [GRUEN]),
        (tiger, [GRUEN, NAPIER_QUESTION]),
    ):
        assert (
            maybe_rewrite_description_opener(
                text,
                sibling_descriptions=lambda siblings=siblings: siblings,
                enabled=True,
                generate=generate,
            )
            == text
        )


def test_sibling_read_failure_keeps_the_listing() -> None:
    def generate(*, model, contents, config):
        raise AssertionError("model should not be called")

    def load():
        raise RuntimeError("db down")

    assert (
        maybe_rewrite_description_opener(
            TIGER_REPEATED,
            sibling_descriptions=load,
            enabled=True,
            generate=generate,
        )
        == TIGER_REPEATED
    )


def test_parsed_response_is_accepted() -> None:
    from app.services.description_openers import DescriptionFirstSentence

    def generate(*, model, contents, config):
        return _Resp("", parsed=DescriptionFirstSentence(first_sentence=TIGER_FRESH), text="")

    result = maybe_rewrite_description_opener(
        TIGER_REPEATED,
        sibling_descriptions=lambda: JEWELRY_SALE,
        enabled=True,
        generate=generate,
    )
    assert result == TIGER_AFTER


def test_rewrite_is_logged(caplog) -> None:
    generate, _calls = _generate(TIGER_FRESH)
    with caplog.at_level(logging.INFO, logger="app.services.description_openers"):
        maybe_rewrite_description_opener(
            TIGER_REPEATED,
            sibling_descriptions=lambda: JEWELRY_SALE,
            sale_id="sale-log",
            enabled=True,
            generate=generate,
        )
    assert "Listing description opener rewritten" in caplog.text
    assert "sale-log" in caplog.text
    assert "Add a touch of classic sparkle" in caplog.text
    assert "Ready to pounce" in caplog.text


def test_parallel_rewrites_are_not_serialized() -> None:
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def generate(*, model, contents, config):
        barrier.wait(timeout=3)
        return _Resp(TIGER_FRESH)

    def run() -> None:
        try:
            result = maybe_rewrite_description_opener(
                TIGER_REPEATED,
                sibling_descriptions=lambda: JEWELRY_SALE,
                enabled=True,
                generate=generate,
            )
            assert result == TIGER_AFTER
        except BaseException as exc:  # noqa: BLE001 - fail the test from the thread
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert errors == []
    assert all(not thread.is_alive() for thread in threads)


def test_author_prompts_vary_the_hook_without_banning_it() -> None:
    clause = description_opener_prompt_clause()
    assert clause in BRAND_AUDIT_SYSTEM
    assert clause in PRO_AUTHOR_DESCRIPTION_GUIDANCE
    assert clause in SYSTEM_INSTRUCTION
    assert "evocative" in clause
    assert "who would love it" in clause
    assert "ready to pounce" in clause
    assert "Do not use these openers" not in clause
    assert "not a generic lifestyle hook" not in BRAND_AUDIT_SYSTEM
    for phrase in OVERUSED_DESCRIPTION_OPENERS:
        assert phrase in clause
    assert "Add a touch of classic sparkle to your look—this Gruen Precision" in PRO_AUTHOR_DESCRIPTION_GUIDANCE
    assert "Ready to pounce—this enamel tiger brooch" in PRO_AUTHOR_DESCRIPTION_GUIDANCE
    assert "Who wouldn't want this Napier link bracelet" in PRO_AUTHOR_DESCRIPTION_GUIDANCE
    rewrite = opener_rewrite_system_instruction()
    assert "flavorful" in rewrite
    assert "plain factual opener" in rewrite
    config = build_opener_rewrite_config()
    _assert_new_call_shape(config, "user text only")
    assert rewrite in (config.system_instruction or "")


def test_load_sale_descriptions_reads_other_lots_in_the_sale(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'openers.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    account = Account(id="acct", name="CT")
    owner = User(
        id="owner",
        account_id=account.id,
        email="owner@example.com",
        password_hash="x",
        role="owner",
    )
    sale = Sale(id="sale-1", account_id=account.id, user_id=owner.id, name="Justina")
    other = Sale(id="sale-2", account_id=account.id, user_id=owner.id, name="Other")
    db.add_all([account, owner, sale, other])
    db.add_all(
        [
            Lot(
                id="mine",
                account_id=account.id,
                user_id=owner.id,
                sale_id=sale.id,
                auction_title="Mine",
                professional_description="Mine opener stays out.",
            ),
            Lot(
                id="sibling",
                account_id=account.id,
                user_id=owner.id,
                sale_id=sale.id,
                auction_title="Sibling",
                professional_description="  Oak dresser with a mirrored back.  ",
            ),
            Lot(
                id="blank",
                account_id=account.id,
                user_id=owner.id,
                sale_id=sale.id,
                auction_title="Blank",
                professional_description="   ",
            ),
            Lot(
                id="elsewhere",
                account_id=account.id,
                user_id=owner.id,
                sale_id=other.id,
                auction_title="Elsewhere",
                professional_description="Elsewhere opener here.",
            ),
        ]
    )
    db.commit()
    assert load_sale_descriptions(db, sale.id, exclude_lot_id="mine") == [
        "Oak dresser with a mirrored back."
    ]
    assert sale_description_loader(db, sale.id, exclude_lot_id="mine")() == [
        "Oak dresser with a mirrored back."
    ]
    assert set(load_sale_descriptions(db, sale.id)) == {
        "Mine opener stays out.",
        "Oak dresser with a mirrored back.",
    }
    db.close()
