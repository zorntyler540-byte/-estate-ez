"""Brand merge: label-first resolution and apparel spec cleanup."""

from __future__ import annotations

import re
import unittest

from app.core.lotting import (
    LotOutput,
    _broaden_auction_title_for_components,
    _ensure_branded_auction_title,
    filter_brands_to_label_evidence,
    infer_brands_from_label_context,
    resolve_lot_brands,
    strip_leading_brands_from_text,
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

    def test_multi_brand_wardrobe_keeps_all_makers_in_brand_spec(self) -> None:
        lot = LotOutput.from_model(
            {
                "item_name": (
                    "Tribal Jacket, Alfred Dunner Cardigan, and White Mountain Wedge Clogs Ensemble"
                ),
                "brand": "Tribal",
                "Brands": ["Tribal"],
                "labels_and_tags": [
                    "TRIBAL S/P",
                    "alfred dunner PETITE",
                    "WHITE MOUNTAIN",
                ],
                "professional_description": (
                    "A coordinated ensemble with a Tribal jacket, Alfred Dunner cardigan, "
                    "and White Mountain wedge clogs."
                ),
                "specification_lines": [
                    "Item 1: Tribal beige button-front jacket, size S/P",
                    "Item 2: Alfred Dunner floral cardigan, size Petite",
                    "Item 3: White Mountain perforated wedge clogs",
                    "Brand: Tribal",
                    "Material: Cotton blend",
                ],
            }
        )
        self.assertEqual(
            set(lot.brands),
            {"Tribal", "Alfred Dunner", "White Mountain"},
        )
        brand_lines = [l for l in lot.specification_lines if l.lower().startswith("brand:")]
        self.assertEqual(len(brand_lines), 1)
        joined = brand_lines[0].lower()
        self.assertIn("tribal", joined)
        self.assertIn("alfred dunner", joined)
        self.assertIn("white mountain", joined)

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

    def test_multi_accessory_item_lines_avoid_brass_table_title(self) -> None:
        specs = [
            "Item 1: Black scarf with gold-tone floral embroidery",
            "Item 2: Black knit headband with feather embellishment",
            "Item 3: Black fleece winter hat with sherpa-style lining",
        ]
        out = _broaden_auction_title_for_components("Single scarf detail", specs)
        self.assertNotIn("Brass", out)
        self.assertNotIn("table", out.lower())
        self.assertIn("accessory", out.lower())

    def test_drops_brand_only_in_ai_title_not_on_labels(self) -> None:
        """Hallucinated maker in title/description must not become Brand: spec."""
        lot = LotOutput.from_model(
            {
                "item_name": "Shiboyuan Glass Angel Musical Box with Gold-Tone Accents",
                "brand": "Shiboyuan",
                "Brands": ["Shiboyuan"],
                "labels_and_tags": [
                    "Hand-Sculpted Glass",
                    "22 KT GOLD",
                    "Tune: Amazing Grace",
                    "WG",
                ],
                "professional_description": (
                    "This lovely Shiboyuan music box features a glass angel with gold-tone accents."
                ),
                "specification_lines": [
                    "Brand: Shiboyuan",
                    "Condition note: Appears in very good visual condition",
                ],
            }
        )
        self.assertEqual(lot.brands, [])
        self.assertNotIn("shiboyuan", lot.auction_title.lower())
        brand_lines = [ln for ln in lot.specification_lines if ln.lower().startswith("brand:")]
        self.assertEqual(brand_lines, [])

    def test_infer_brands_ignores_prose_only_hallucination(self) -> None:
        brands = infer_brands_from_label_context(
            [],
            [],
            "Shiboyuan Glass Angel Music Box",
            "Shiboyuan decorative music box with gold accents.",
        )
        self.assertEqual(brands, [])

    def test_filter_brands_keeps_label_ocr_evidence(self) -> None:
        kept = filter_brands_to_label_evidence(
            ["Shiboyuan", "Mirabeau"],
            ["SHIBOYUAN SCARF", "Fashion Accessory by Mirabeau"],
            [],
        )
        self.assertEqual(set(kept), {"Shiboyuan", "Mirabeau"})

    def test_strip_leading_brand_from_title(self) -> None:
        out = strip_leading_brands_from_text(
            "Shiboyuan Glass Angel Musical Box with Gold-Tone Accents",
            ["Shiboyuan"],
        )
        self.assertEqual(out, "Glass Angel Musical Box with Gold-Tone Accents")

    def test_scrubs_vintage_without_era_evidence(self) -> None:
        lot = LotOutput.from_model(
            {
                "item_name": "Vintage Glass Angel Musical Box with Gold Accents",
                "professional_description": (
                    "This vintage decorative music box features a glass angel with gold-tone accents."
                ),
                "specification_lines": [
                    "Brand:",
                    "Condition note: Appears in very good condition",
                    "Era: Vintage",
                    "Material: Glass",
                ],
            }
        )
        self.assertNotIn("vintage", lot.auction_title.lower())
        self.assertNotIn("vintage", lot.professional_description.lower())
        self.assertFalse(any(ln.lower().startswith("era:") for ln in lot.specification_lines))

    def test_keeps_vintage_once_when_dated_era_on_tag(self) -> None:
        lot = LotOutput.from_model(
            {
                "item_name": "Vintage 1970s Ceramic Vase",
                "professional_description": "A vintage ceramic vase from the era.",
                "labels_and_tags": ["© 1972"],
                "specification_lines": ["Material: Ceramic", "Era: 1970s"],
            }
        )
        self.assertLessEqual(len(re.findall(r"vintage", lot.auction_title + lot.professional_description, re.I)), 1)


if __name__ == "__main__":
    unittest.main()
