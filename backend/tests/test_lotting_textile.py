"""Regression tests for multi-photo textile / lace lots (merged dimensions, title, prose)."""

from __future__ import annotations

import unittest

from app.core.lotting import LotOutput


class TestTextileLotMerge(unittest.TestCase):
    def test_from_model_broadens_singular_title_when_multi_dimensions(self) -> None:
        lot = LotOutput.from_model(
            {
                "item_name": "Cotton Woven Lace Textile with Geometric Patterns",
                "professional_description": "This cream-toned lace textile features one weave.",
                "specification_lines": [
                    "Components: cream cotton lace runner",
                    "Dimensions: 41.5 in L",
                    "Dimensions: 12\" W",
                    "Material: Cotton",
                ],
            }
        )
        t = lot.auction_title.lower()
        self.assertIn("lot", t)
        self.assertTrue("lace" in t or "textile" in t)

    def test_merge_second_photo_applies_textile_lot_prose(self) -> None:
        lot = LotOutput.from_model(
            {
                "item_name": "Cotton Woven Lace Textile with Geometric Patterns",
                "professional_description": "This cream-toned lace textile features a complex weave.",
                "specification_lines": [
                    "Components: cream cotton lace runner",
                    "Dimensions: 41.5 in L",
                    "Dimensions: 12\" W",
                    "Material: Cotton",
                ],
            }
        )
        self.assertEqual(lot.photo_count, 1)
        lot.merge_detail(
            {
                "item_name": "Lace table runner",
                "professional_description": "Additional photos show further panels and tape reads.",
                "specification_lines": ["Dimensions: 39L in"],
            }
        )
        self.assertEqual(lot.photo_count, 2)
        low = lot.professional_description.lower()
        self.assertIn("measuring", low)
        self.assertTrue("lace" in low or "textile" in low or "panels" in low)


if __name__ == "__main__":
    unittest.main()
