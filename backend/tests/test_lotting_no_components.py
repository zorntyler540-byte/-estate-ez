"""Components / Style / Finish stripped from listing specs."""

from __future__ import annotations

import unittest

from app.core.lotting import (
    LotOutput,
    dedupe_specification_lines_preserve_order,
    drop_style_and_finish_spec_lines,
)


class TestNoComponentsSpec(unittest.TestCase):
    def test_dedupe_strips_components_for_supply_lot(self) -> None:
        lines = [
            "Components: Moleskine black pocket notebook; Creative Mark 5 x 7 sketchbooks",
            "Item 1: Moleskine black pocket notebook with elastic closure",
            "Item 2: Creative Mark 5 x 7 black textured hardcover sketchbooks",
            "Brand: Prismacolor",
            "Material: Paper",
            "Style: Fine art supplies",
            "Finish: Matte pencil shafts",
        ]
        merged = dedupe_specification_lines_preserve_order(lines)
        self.assertFalse(any(l.lower().startswith("components:") for l in merged))
        self.assertFalse(any(l.lower().startswith("style:") for l in merged))
        self.assertFalse(any(l.lower().startswith("finish:") for l in merged))
        self.assertEqual(len([l for l in merged if l.lower().startswith("item ")]), 2)

    def test_components_only_inventory_becomes_item_rows_before_drop(self) -> None:
        merged = dedupe_specification_lines_preserve_order(
            [
                "Components: brass incense censer; brass ewer; hand bell",
                "Material: Brass",
            ]
        )
        joined = " ".join(merged).lower()
        self.assertFalse(any(l.lower().startswith("components:") for l in merged))
        self.assertEqual(len([l for l in merged if l.lower().startswith("item ")]), 3)
        self.assertIn("incense censer", joined)
        self.assertIn("brass ewer", joined)
        self.assertIn("hand bell", joined)

    def test_component_detail_features_still_drop_without_item_rows(self) -> None:
        merged = dedupe_specification_lines_preserve_order(
            [
                "Components: zippered welt pocket; heavy-duty zipper closure; care label",
                "Material: Cotton blend",
            ]
        )
        self.assertFalse(any(l.lower().startswith("components:") for l in merged))
        self.assertFalse(any(l.lower().startswith("item ") for l in merged))

    def test_components_only_decorative_lot_still_broadens_title_and_prose(self) -> None:
        lot = LotOutput.from_model(
            {
                "item_name": "Two-Piece Incense Censer",
                "professional_description": "Decorative brass incense censer for tabletop display.",
                "specification_lines": [
                    "Components: brass incense censer; brass ewer; hand bell",
                    "Material: Brass",
                ],
            }
        )
        self.assertFalse(any(l.lower().startswith("components:") for l in lot.specification_lines))
        self.assertIn("decorative table lot", lot.auction_title.lower())
        self.assertIn("several distinct pieces", lot.professional_description.lower())

    def test_multi_photo_coin_components_survive_as_item_rows_for_title_and_prose(self) -> None:
        lot = LotOutput.from_model(
            {
                "item_name": "1904 Lincoln Cent in Littleton Holder",
                "professional_description": "Single Lincoln cent presented in a holder.",
                "specification_lines": [
                    "Components: 1904 Lincoln cent in Littleton holder",
                    "Material: Copper",
                ],
            }
        )
        lot.merge_detail(
            {
                "item_name": "1906 Lincoln Cent in Littleton Holder",
                "professional_description": "Additional holder photos show more Lincoln cents.",
                "specification_lines": [
                    "Components: 1906 Lincoln cent in Littleton holder; "
                    "1908 Lincoln cent in Littleton holder; "
                    "1910-S Lincoln cent in Littleton holder",
                ],
            }
        )
        joined = " ".join(lot.specification_lines).lower()
        self.assertFalse(any(l.lower().startswith("components:") for l in lot.specification_lines))
        for year in ("1904", "1906", "1908", "1910-s"):
            self.assertIn(year, joined)
        self.assertIn("coin lot", lot.auction_title.lower())
        self.assertIn("1904, 1906, 1908, 1910-s", lot.auction_title.lower())
        self.assertIn("coin lot spans 1904, 1906, 1908, 1910-s", lot.professional_description.lower())

    def test_drop_style_and_finish(self) -> None:
        out = drop_style_and_finish_spec_lines(
            ["Style: Casual", "Finish: Satin metal", "Material: Brass", "Era: Vintage"]
        )
        labels = [l.split(":", 1)[0].lower() for l in out]
        self.assertEqual(labels, ["material", "era"])


if __name__ == "__main__":
    unittest.main()
