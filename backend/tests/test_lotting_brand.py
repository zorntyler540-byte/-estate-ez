"""Brand merge: label-first resolution and apparel spec cleanup."""

from __future__ import annotations

import unittest

from app.core.lotting import LotOutput, resolve_lot_brands


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


if __name__ == "__main__":
    unittest.main()
