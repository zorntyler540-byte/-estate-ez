"""Brand merge: label-first resolution and apparel spec cleanup."""

from __future__ import annotations

import unittest

from app.core.lotting import (
    LotOutput,
    _ensure_branded_auction_title,
    infer_brands_from_label_context,
    resolve_lot_brands,
)


class TestBrandResolution(unittest.TestCase):
    def test_drops_prada_when_st_johns_bay_on_label(self) -> None:
        brands, review = resolve_lot_brands(
            ["St. John's Bay", "Prada"],
            labels_and_tags=["AUTHENTIC ST. JOHN'S BAY ESTABLISHED 1984", "Size L/G"],
            specification_lines=["Brand: Prada", "Material: Cotton blend"],
            auction_title="St. John's Bay Cotton-Blend Zip-Up Jacket with Plaid Lining",
            professional_description="Classic St. John's Bay zip-up jacket with plaid lining.",
        )
        self.assertEqual(brands, ["St. John's Bay"])
        self.assertTrue(review)

    def test_merge_jacket_lot_drops_prada_and_dedupes_prose(self) -> None:
        lot = LotOutput.from_model(
            {
                "item_name": "St. John's Bay Cotton-Blend Zip-Up Jacket with Plaid Lining",
                "brand": "St. John's Bay",
                "Brands": ["St. John's Bay"],
                "labels_and_tags": ["AUTHENTIC ST. JOHN'S BAY TRADITIONAL FIT"],
                "professional_description": (
                    "This classic St. John's Bay zip-up jacket offers timeless casual style. "
                    "This classic St. John's Bay men's jacket features a versatile taupe exterior with plaid lining."
                ),
                "specification_lines": [
                    "Brand: St. John's Bay",
                    "Material: Cotton",
                    "Size: Large (L/G)",
                ],
            }
        )
        lot.merge_detail(
            {
                "brand": "Prada",
                "Brands": ["Prada"],
                "professional_description": "Close-up shows a metal zipper pull.",
                "specification_lines": [
                    "Brand: Prada",
                    "Condition note: Surface oxidation and tarnish consistent with age",
                    "Components: Interior slip pocket, heavy-duty zipper closure",
                ],
            }
        )
        self.assertEqual(lot.brands, ["St. John's Bay"])
        self.assertTrue(lot.brand_needs_review)
        self.assertNotIn("prada", " ".join(lot.specification_lines).lower())
        self.assertNotIn("oxidation", " ".join(lot.specification_lines).lower())
        low = lot.professional_description.lower()
        self.assertIn("st. john", low)
        self.assertNotIn("this classic st.\n", low)
        self.assertFalse(low.endswith("this classic st."))

    def test_infers_brand_from_authentic_label_line(self) -> None:
        brands = infer_brands_from_label_context(
            ["AUTHENTIC ST. JOHN'S BAY ESTABLISHED 1984", "L/G"],
            ["Components: Zipper closure, interior lining, care label", "Material: Synthetic blend"],
        )
        self.assertEqual(brands, ["St. John's Bay"])

    def test_finalize_title_when_only_labels_have_maker(self) -> None:
        lot = LotOutput.from_model(
            {
                "item_name": "Synthetic Blend Lined Utility Jacket",
                "brand": "",
                "Brands": [],
                "labels_and_tags": ["AUTHENTIC ST. JOHN'S BAY ESTABLISHED 1984", "MADE IN CHINA"],
                "professional_description": "Classic olive utility jacket with lining and zip front.",
                "specification_lines": [
                    "Components: Zipper closure, care label",
                    "Material: Synthetic blend",
                    "Style: Utility jacket",
                ],
            }
        )
        self.assertEqual(lot.brands, ["St. John's Bay"])
        self.assertIn("St. John's Bay", lot.auction_title)
        self.assertNotIn("Synthetic Blend Lined Utility", lot.auction_title)

    def test_detail_title_replaced_with_branded_title(self) -> None:
        out = _ensure_branded_auction_title(
            "Woven Utility Jacket Detail with Zippered Pocket",
            ["St. John's Bay"],
            [
                "Brand: St. John's Bay",
                "Material: Cotton Blend",
                "Style: Utility / Outerwear",
                "Era: Contemporary",
            ],
        )
        self.assertIn("St. John's Bay", out)
        self.assertIn("Jacket", out)
        self.assertNotIn("Detail with", out)

    def test_merge_keeps_branded_title_over_detail_frame(self) -> None:
        lot = LotOutput.from_model(
            {
                "item_name": "St. John's Bay Cotton-Blend Zip-Up Jacket",
                "brand": "St. John's Bay",
                "labels_and_tags": ["AUTHENTIC ST. JOHN'S BAY"],
                "specification_lines": ["Brand: St. John's Bay", "Material: Cotton"],
            }
        )
        lot.merge_detail(
            {
                "item_name": "Woven Utility Jacket Detail with Zippered Pocket",
                "brand": "",
                "specification_lines": ["Components: Zippered welt pocket"],
            }
        )
        self.assertIn("St. John's Bay", lot.auction_title)
        self.assertNotIn("Detail with", lot.auction_title)


if __name__ == "__main__":
    unittest.main()
