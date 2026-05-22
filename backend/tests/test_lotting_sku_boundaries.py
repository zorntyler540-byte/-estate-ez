"""Regression tests for tape SKU lot-boundary decisions."""

from __future__ import annotations

import unittest

from app.core.lotting import LotOutput, tape_sku_same_marking
from app.services.processing import _should_merge_different_tape_sku_candidate


def _lot(sku: str, title: str) -> LotOutput:
    return LotOutput.from_model(
        {
            "SKU": sku,
            "AuctionTitle": title,
            "ProfessionalDescription": f"Catalog entry for {title}.",
            "SpecificationLines": ["Material: Ceramic"],
        }
    )


class TestTapeSkuBoundaries(unittest.TestCase):
    def test_letter_prefixes_must_match_when_both_present(self) -> None:
        self.assertFalse(tape_sku_same_marking("SR006", "SA006"))
        self.assertFalse(tape_sku_same_marking("SA006", "SB006"))

    def test_digits_only_can_match_prefixed_same_number(self) -> None:
        self.assertTrue(tape_sku_same_marking("006", "SR006"))
        self.assertTrue(tape_sku_same_marking("lot #006", "SR 006"))

    def test_verified_different_tape_sku_opens_boundary_despite_title_overlap(self) -> None:
        current = _lot("SA001", "Vintage Ceramic Pitcher")
        candidate = _lot("SA002", "Vintage Ceramic Pitcher Detail")

        self.assertTrue(
            _should_merge_different_tape_sku_candidate(
                current,
                candidate,
                tape_verify_skipped=True,
            )
        )
        self.assertFalse(
            _should_merge_different_tape_sku_candidate(
                current,
                candidate,
                tape_verify_skipped=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
