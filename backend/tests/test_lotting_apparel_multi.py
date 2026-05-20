"""Multi-photo apparel lots: collapse duplicate Item rows and shorten Components."""

from __future__ import annotations

import unittest

from app.core.lotting import (
    LotOutput,
    collapse_duplicate_item_spec_lines,
    dedupe_specification_lines_preserve_order,
)


class TestApparelMultiPhotoLot(unittest.TestCase):
    def test_collapses_duplicate_item_rows_to_distinct_garments(self) -> None:
        lines = [
            "Components: One Tribal tan utility jacket with snap front and zip pockets; "
            "one Alfred Dunner floral cardigan with sequins; matching tan trousers.",
            "Item 1: Tribal tan utility jacket, size L/G, snap closure, zippered pockets.",
            "Item 2: Tribal beige utility jacket with silver snaps and zip pockets.",
            "Item 3: Alfred Dunner Petite floral knit cardigan with sequined flowers, size PS.",
            "Item 4: Alfred Dunner abstract floral cardigan, mother-of-pearl buttons.",
            "Item 5: Tribal utility jacket tan twill size 8.",
            "Item 6: Tribal jacket beige L/G.",
            "Item 7: Alfred Dunner cardigan orange brown cream Petite.",
            "Item 8: Matching tan woven trousers.",
            "Item 9: Alfred Dunner knit top floral PS.",
            "Item 10: White Mountain slip-on wedge shoes size 6.5 M.",
        ]
        merged = dedupe_specification_lines_preserve_order(lines)
        item_lines = [l for l in merged if l.lower().startswith("item ")]
        self.assertLessEqual(len(item_lines), 4)
        sigs = set()
        for line in item_lines:
            self.assertRegex(line, r"(?i)^item\s+\d+:")
            body = line.split(":", 1)[1]
            low = body.lower()
            if "tribal" in low or "utility" in low:
                sigs.add("jacket")
            elif "alfred" in low or "dunner" in low:
                sigs.add("cardigan")
            elif "trouser" in low or "pant" in low:
                sigs.add("trousers")
            elif "white mountain" in low or "wedge" in low:
                sigs.add("shoes")
        self.assertIn("jacket", sigs)
        self.assertIn("cardigan", sigs)
        comp = next(l for l in merged if l.lower().startswith("components:"))
        self.assertLess(len(comp), 500)

    def test_merge_detail_does_not_flood_size_tag_lines(self) -> None:
        lot = LotOutput.from_model(
            {
                "item_name": "Tribal Jacket & Alfred Dunner Cardigan Lot",
                "brand": "Tribal",
                "labels_and_tags": ["TRIBAL G/L", "alfred dunner PETITE PS"],
                "specification_lines": [
                    "Components: Tribal jacket; Alfred Dunner cardigan; tan trousers",
                    "Item 1: Tribal tan utility jacket L/G",
                    "Item 2: Alfred Dunner Petite floral cardigan PS",
                    "Item 3: Matching tan trousers",
                ],
            }
        )
        for _ in range(6):
            lot.merge_detail(
                {
                    "labels_and_tags": [
                        "TRIBAL G/L MADE IN CHINA",
                        "55% RAMIE 45% COTTON PS",
                        "FAIT EN CHINE",
                    ],
                    "specification_lines": [
                        "Item 1: Tribal utility jacket detail frame",
                        "Item 2: Alfred Dunner cardigan close-up",
                    ],
                }
            )
        size_lines = [
            l
            for l in lot.specification_lines
            if l.lower().startswith("size / tag")
        ]
        self.assertLessEqual(len(size_lines), 2)
        item_lines = [l for l in lot.specification_lines if l.lower().startswith("item ")]
        self.assertLessEqual(len(item_lines), 4)


if __name__ == "__main__":
    unittest.main()
