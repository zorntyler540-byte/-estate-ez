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

    def test_drop_style_and_finish(self) -> None:
        out = drop_style_and_finish_spec_lines(
            ["Style: Casual", "Finish: Satin metal", "Material: Brass", "Era: Vintage"]
        )
        labels = [l.split(":", 1)[0].lower() for l in out]
        self.assertEqual(labels, ["material", "era"])

    def test_components_only_inventory_survives_as_item_rows(self) -> None:
        merged = dedupe_specification_lines_preserve_order(
            [
                "Components: 1 brass censer; 1 hand bell; 1 slender ewer; 1 dragon stand",
                "Material: Brass",
                "Era: Vintage",
            ]
        )

        self.assertFalse(any(l.lower().startswith("components:") for l in merged))
        item_lines = [l for l in merged if l.lower().startswith("item ")]
        self.assertEqual(len(item_lines), 4)
        self.assertIn("censer", " ".join(item_lines).lower())
        self.assertIn("ewer", " ".join(item_lines).lower())

    def test_components_only_inventory_still_broadens_narrow_title(self) -> None:
        lot = LotOutput.from_model(
            {
                "AuctionTitle": "Brass Incense Censer",
                "ProfessionalDescription": "A warm brass censer lot with bell, ewer, and dragon stand for display.",
                "SpecificationLines": [
                    "Components: vintage brass censer, hand bell, ewer, dragon stand",
                    "Material: Brass",
                    "Era: Vintage",
                ],
                "Condition": "GOOD",
            }
        )

        self.assertEqual(lot.auction_title, "Brass decorative table lot - multiple pieces")
        self.assertFalse(any(l.lower().startswith("components:") for l in lot.specification_lines))
        self.assertTrue(any(l.lower().startswith("item 1:") for l in lot.specification_lines))


if __name__ == "__main__":
    unittest.main()
