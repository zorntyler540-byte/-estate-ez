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
    BANNED_DESCRIPTION_OPENERS,
    DISCOURAGED_DESCRIPTION_FILLER,
    build_opener_rewrite_config,
    description_opener_prompt_clause,
    listing_author_model,
    load_sale_descriptions,
    sale_description_loader,
    matched_opener_phrases,
    maybe_rewrite_description_opener,
    opening_key,
    opener_issue_score,
    replace_first_sentence,
    rewrite_reason,
    split_first_sentence,
)
from pipeline.pipeline import BRAND_AUDIT_SYSTEM, PRO_AUTHOR_DESCRIPTION_GUIDANCE

GRUEN_BEFORE = (
    "Add a touch of classic sparkle to your look—this Gruen Precision wristwatch "
    "keeps excellent time. Minor desk wear on the lugs."
)
GRUEN_SENTENCE = "Gruen Precision wristwatch in a gold-tone case, with a clean two-tone dial."
GRUEN_AFTER = (
    "Gruen Precision wristwatch in a gold-tone case, with a clean two-tone dial. "
    "Minor desk wear on the lugs."
)
DUPLICATE_BEFORE = (
    "Gruen Precision wristwatch in a gold-tone case, with a clean dial. "
    "Minor desk wear on the lugs."
)
DUPLICATE_SIBLING = "Gruen Precision wristwatch in a silver-tone case, with a cream dial."
DUPLICATE_SENTENCE = "Gold-tone Gruen Precision wristwatch with a two-tone dial and slim lugs."
DUPLICATE_AFTER = (
    "Gold-tone Gruen Precision wristwatch with a two-tone dial and slim lugs. "
    "Minor desk wear on the lugs."
)


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
    assert opening_key(GRUEN_BEFORE) == "add a touch of"
    assert opening_key("  ADD   a touch, of classic sparkle!") == "add a touch of"
    assert opening_key('"Gruen Precision wristwatch, in gold!"') == "gruen precision wristwatch in"
    assert opening_key("Gruen Precision wristwatch in a gold-tone case.") == "gruen precision wristwatch in"
    assert split_first_sentence(GRUEN_BEFORE)[1] == "Minor desk wear on the lugs."
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


def test_banned_phrases_match_whole_words_in_the_first_sentence_only() -> None:
    assert matched_opener_phrases(GRUEN_BEFORE)[0] == "Add a touch"
    assert "A perfect" in matched_opener_phrases(
        "A perfect statement piece for any collection—this Napier bracelet stacks easily."
    )
    assert "statement piece for any" in matched_opener_phrases(
        "A perfect statement piece for any collection—this Napier bracelet stacks easily."
    )
    assert matched_opener_phrases("A classic choice for everyday elegance. The cotton is soft.") == ["A classic"]
    assert matched_opener_phrases("A delightful accent for the shelf.") == ["A delightful"]
    assert matched_opener_phrases("A charming little brass bell.") == ["A charming"]
    assert matched_opener_phrases("Elevate your table with this brass candlestick.") == ["Elevate"]
    assert matched_opener_phrases("Ready to bring warmth to the mantel.") == ["ready to"]
    assert matched_opener_phrases("A timeless Gruen Precision wristwatch with a cream dial.") == ["timeless"]
    assert matched_opener_phrases(
        "Gruen Precision wristwatch with a cream dial. A timeless look for evenings."
    ) == []
    assert matched_opener_phrases(
        "A classical guitar from Valencia, with a spruce top. The case is worn."
    ) == []
    assert rewrite_reason("Gruen Precision wristwatch with a cream dial.", []) is None


def test_duplicate_opening_uses_the_first_four_words() -> None:
    siblings = [DUPLICATE_SIBLING]
    assert opening_key(DUPLICATE_BEFORE) == opening_key(DUPLICATE_SIBLING) == "gruen precision wristwatch in"
    assert rewrite_reason(DUPLICATE_BEFORE, siblings) == "duplicate"
    assert rewrite_reason(
        "Gruen Precision wristwatch with a cream dial.",
        siblings,
    ) is None
    assert opener_issue_score(DUPLICATE_BEFORE, {"gruen precision wristwatch in"}) == (0, 1)


def test_replace_first_sentence_keeps_the_rest() -> None:
    assert replace_first_sentence(GRUEN_BEFORE, GRUEN_SENTENCE) == GRUEN_AFTER
    rewritten = replace_first_sentence(
        GRUEN_BEFORE,
        "Gruen Precision wristwatch with a cream dial. The crown was replaced.",
    )
    assert rewritten == (
        "Gruen Precision wristwatch with a cream dial. Minor desk wear on the lugs."
    )


def test_banned_opener_rewrites_only_the_first_sentence(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_PRO_MODEL", "gemini-2.5-pro")
    generate, calls = _generate(GRUEN_SENTENCE)
    result = maybe_rewrite_description_opener(
        GRUEN_BEFORE,
        sibling_descriptions=lambda: [],
        title="Gruen Precision Wristwatch",
        sale_id="sale-1",
        enabled=True,
        generate=generate,
    )
    assert result == GRUEN_AFTER
    assert len(calls) == 1
    assert calls[0]["model"] == "gemini-2.5-pro"
    _assert_new_call_shape(calls[0]["config"], calls[0]["contents"])
    assert "Add a touch" in calls[0]["contents"]
    assert "Minor desk wear on the lugs." in calls[0]["contents"]


@pytest.mark.parametrize("model_id", ["gemini-2.5-pro", "gemini-3.8-flash"])
def test_rewrite_call_passes_the_listing_model_through(monkeypatch, model_id: str) -> None:
    monkeypatch.setenv("GEMINI_PRO_MODEL", model_id)
    assert listing_author_model() == model_id
    generate, calls = _generate(GRUEN_SENTENCE)
    maybe_rewrite_description_opener(
        GRUEN_BEFORE,
        sibling_descriptions=lambda: [],
        enabled=True,
        generate=generate,
    )
    assert calls[0]["model"] == model_id
    _assert_new_call_shape(calls[0]["config"], calls[0]["contents"])


def test_duplicate_opening_is_passed_as_something_to_avoid(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_PRO_MODEL", "gemini-2.5-pro")
    generate, calls = _generate(DUPLICATE_SENTENCE)
    result = maybe_rewrite_description_opener(
        DUPLICATE_BEFORE,
        sibling_descriptions=lambda: [DUPLICATE_SIBLING, "Oak dresser with a mirrored back."],
        title="Gruen Precision Wristwatch",
        sale_id="sale-9",
        enabled=True,
        generate=generate,
    )
    assert result == DUPLICATE_AFTER
    assert len(calls) == 1
    assert DUPLICATE_SIBLING in calls[0]["contents"]
    assert "oak dresser with a" in calls[0]["contents"]
    _assert_new_call_shape(calls[0]["config"], calls[0]["contents"])


def test_second_retry_replaces_a_still_banned_opening() -> None:
    sentences = iter(
        [
            "A charming jacket for cool mornings.",
            "Olive cotton field jacket with a zip front and plaid lining.",
        ]
    )
    calls: list[str] = []

    def generate(*, model, contents, config):
        calls.append(contents)
        return _Resp(next(sentences))

    original = "A classic choice for everyday elegance. The cotton is soft."
    result = maybe_rewrite_description_opener(
        original,
        sibling_descriptions=lambda: [],
        enabled=True,
        generate=generate,
    )
    assert len(calls) == 2
    assert "A charming" in calls[1]
    assert result == (
        "Olive cotton field jacket with a zip front and plaid lining. The cotton is soft."
    )


def test_clean_rewrite_stops_before_the_second_try() -> None:
    calls = {"n": 0}

    def generate(*, model, contents, config):
        calls["n"] += 1
        return _Resp(GRUEN_SENTENCE)

    maybe_rewrite_description_opener(
        GRUEN_BEFORE,
        sibling_descriptions=lambda: [],
        enabled=True,
        generate=generate,
    )
    assert calls["n"] == 1


def test_retries_keep_the_original_when_nothing_is_better() -> None:
    def generate(*, model, contents, config):
        return _Resp("A perfect jacket for weekends.")

    original = "A classic choice for everyday elegance. The cotton is soft."
    result = maybe_rewrite_description_opener(
        original,
        sibling_descriptions=lambda: [],
        enabled=True,
        generate=generate,
        max_retries=2,
    )
    assert result == original


def test_model_error_keeps_the_listing() -> None:
    def generate(*, model, contents, config):
        raise RuntimeError("gemini down")

    original = GRUEN_BEFORE
    assert (
        maybe_rewrite_description_opener(
            original,
            sibling_descriptions=lambda: [],
            enabled=True,
            generate=generate,
        )
        == original
    )


def test_disabled_flag_skips_the_model_and_the_sale_read() -> None:
    def generate(*, model, contents, config):
        raise AssertionError("model should not be called")

    def load():
        raise AssertionError("siblings should not be read")

    assert (
        maybe_rewrite_description_opener(
            GRUEN_BEFORE,
            sibling_descriptions=load,
            enabled=False,
            generate=generate,
        )
        == GRUEN_BEFORE
    )


def test_settings_flag_defaults_on_and_can_turn_the_check_off(monkeypatch) -> None:
    assert Settings.model_fields["description_opener_rewrite"].default is True
    monkeypatch.setattr(settings, "description_opener_rewrite", False)

    def generate(*, model, contents, config):
        raise AssertionError("model should not be called")

    assert (
        maybe_rewrite_description_opener(
            GRUEN_BEFORE,
            sibling_descriptions=lambda: [],
            generate=generate,
        )
        == GRUEN_BEFORE
    )


def test_specific_opening_does_not_call_the_model() -> None:
    def generate(*, model, contents, config):
        raise AssertionError("model should not be called")

    text = "Gruen Precision wristwatch with a cream dial. The crown is unsigned."
    assert (
        maybe_rewrite_description_opener(
            text,
            sibling_descriptions=lambda: ["Oak dresser with a mirrored back."],
            enabled=True,
            generate=generate,
        )
        == text
    )


def test_sibling_read_failure_still_rewrites_a_banned_opener() -> None:
    generate, calls = _generate(GRUEN_SENTENCE)

    def load():
        raise RuntimeError("db down")

    result = maybe_rewrite_description_opener(
        GRUEN_BEFORE,
        sibling_descriptions=load,
        enabled=True,
        generate=generate,
    )
    assert result == GRUEN_AFTER
    assert len(calls) == 1


def test_parsed_response_is_accepted() -> None:
    from app.services.description_openers import DescriptionFirstSentence

    def generate(*, model, contents, config):
        return _Resp("", parsed=DescriptionFirstSentence(first_sentence=GRUEN_SENTENCE), text="")

    result = maybe_rewrite_description_opener(
        GRUEN_BEFORE,
        sibling_descriptions=lambda: [],
        enabled=True,
        generate=generate,
    )
    assert result == GRUEN_AFTER


def test_rewrite_is_logged(caplog) -> None:
    generate, _calls = _generate(GRUEN_SENTENCE)
    with caplog.at_level(logging.INFO, logger="app.services.description_openers"):
        maybe_rewrite_description_opener(
            GRUEN_BEFORE,
            sibling_descriptions=lambda: [],
            sale_id="sale-log",
            enabled=True,
            generate=generate,
        )
    assert "Listing description opener rewritten" in caplog.text
    assert "sale-log" in caplog.text
    assert "Add a touch of classic sparkle" in caplog.text


def test_parallel_rewrites_are_not_serialized() -> None:
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def generate(*, model, contents, config):
        barrier.wait(timeout=3)
        return _Resp(GRUEN_SENTENCE)

    def run() -> None:
        try:
            result = maybe_rewrite_description_opener(
                GRUEN_BEFORE,
                sibling_descriptions=lambda: [],
                enabled=True,
                generate=generate,
            )
            assert result == GRUEN_AFTER
        except BaseException as exc:  # noqa: BLE001 - fail the test from the thread
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert errors == []
    assert all(not thread.is_alive() for thread in threads)


def test_author_prompts_use_the_shared_banned_list() -> None:
    clause = description_opener_prompt_clause()
    assert clause in BRAND_AUDIT_SYSTEM
    assert clause in PRO_AUTHOR_DESCRIPTION_GUIDANCE
    assert clause in SYSTEM_INSTRUCTION
    assert "flair" not in BRAND_AUDIT_SYSTEM
    assert "flair" not in PRO_AUTHOR_DESCRIPTION_GUIDANCE
    assert "flair" not in SYSTEM_INSTRUCTION
    assert "who would love it" not in BRAND_AUDIT_SYSTEM
    for phrase in BANNED_DESCRIPTION_OPENERS:
        assert phrase in clause
    for phrase in DISCOURAGED_DESCRIPTION_FILLER:
        assert phrase in clause
    assert "Gruen Precision wristwatch in a gold-tone case" in PRO_AUTHOR_DESCRIPTION_GUIDANCE
    config = build_opener_rewrite_config()
    _assert_new_call_shape(config, "user text only")
    assert description_opener_prompt_clause() in (config.system_instruction or "")


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
