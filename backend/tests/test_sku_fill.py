"""Sequential SKUs for lots that were cataloged without tape numbers."""

from app.services.sku_fill import SkuStartError, assign_blank_skus, parse_sku_start
import pytest


def test_parse_plain_and_prefixed_starts() -> None:
    assert parse_sku_start(" 1001 ") == ("", 1001, 4)
    assert parse_sku_start("DK1001") == ("DK", 1001, 4)
    assert parse_sku_start("001") == ("", 1, 3)


def test_parse_rejects_a_start_with_no_number() -> None:
    with pytest.raises(SkuStartError):
        parse_sku_start("random")


def test_assign_skips_numbers_already_in_the_sale() -> None:
    assigned = assign_blank_skus(
        ["lot-a", "lot-b", "lot-c"],
        start="100",
        taken={"101", "dk100"},
    )
    assert assigned == [("lot-a", "100"), ("lot-b", "102"), ("lot-c", "103")]


def test_assign_keeps_the_prefix_and_grows_past_the_width() -> None:
    assigned = assign_blank_skus(["lot-a"], start="DK99", taken=set())
    assert assigned == [("lot-a", "DK99")]
    assigned = assign_blank_skus(["lot-a", "lot-b"], start="98", taken=set())
    assert [sku for _id, sku in assigned] == ["98", "99"]
