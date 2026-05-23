"""Multi-photo apparel lots: collapse duplicate Item rows and shorten Components."""

from __future__ import annotations

import re
import unittest

from app.core.lotting import (
    LotOutput,
    _collapse_conflicting_apparel_prose,
    _ensure_multi_piece_description,
    _scrub_misapplied_mary_jane_phrasing,
    dedupe_specification_lines_preserve_order,
    drop_components_lines_for_apparel_inventory,
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
        self.assertFalse(any(l.lower().startswith("components:") for l in merged))

    def test_drops_components_when_item_rows_present(self) -> None:
        lines = [
            "Components: Tribal jacket; Alfred Dunner cardigan; White Mountain clogs",
            "Item 1: Tribal utility jacket L/G",
            "Item 2: Alfred Dunner floral cardigan PS",
            "Item 3: White Mountain tan laser-cut open-toe clogs",
        ]
        out = drop_components_lines_for_apparel_inventory(lines)
        self.assertFalse(any(l.lower().startswith("components:") for l in out))
        self.assertEqual(len([l for l in out if l.lower().startswith("item ")]), 3)

    def test_keeps_components_when_no_item_rows_preserve_inventory(self) -> None:
        lines = [
            "Components: Tribal jacket; Alfred Dunner cardigan; matching tan trousers.",
            "Material: Cotton blend",
            "Brand: Tribal; Alfred Dunner",
        ]
        merged = dedupe_specification_lines_preserve_order(lines)
        component_lines = [l for l in merged if l.lower().startswith("components:")]
        self.assertEqual(len(component_lines), 1)
        self.assertIn("jacket", component_lines[0].lower())
        self.assertIn("cardigan", component_lines[0].lower())
        self.assertIn("trousers", component_lines[0].lower())

    def test_scrubs_mary_jane_when_lot_is_open_toe_clogs(self) -> None:
        blob = (
            "white mountain tan laser-cut open-toe clogs wedge "
            "item 3: white mountain tan laser-cut open-toe clogs"
        )
        prose = (
            "Charming tan leather Mary Jane wedge clogs featuring laser-cut floral perforations."
        )
        fixed = _scrub_misapplied_mary_jane_phrasing(prose, blob)
        self.assertNotIn("mary jane", fixed.lower())
        self.assertIn("clogs", fixed.lower())

    def test_six_frame_items_collapse_to_four_salable_pieces(self) -> None:
        """Duplicate jacket, left-shoe fragment, and extra Size: rows from multi-photo merge."""
        lines = [
            "Condition note: Excellent pre-owned condition",
            "Style: Perforated comfort slip-on",
            "Item 1: Tribal beige button-front utility jacket with zip pockets, size S/P",
            "Item 2: Left shoe, size 6.5M",
            "Item 3: Alfred Dunner floral knit button-up cardigan, size Petite",
            "Item 4: White Mountain perforated tan peep-toe wedge clogs",
            "Item 5: Beige button-front utility jacket with zippered pockets",
            "Item 6: Matching beige trousers",
            "Size: US Women's 8M",
            "Size: Women's 6.5 M",
            "Size: L (G/L)",
            "Brand: Tribal",
            "Size / tags: TRIBAL; alfred dunner PETITE; WHITE MOUNTAIN",
        ]
        merged = dedupe_specification_lines_preserve_order(lines)
        item_lines = [l for l in merged if l.lower().startswith("item ")]
        self.assertLessEqual(len(item_lines), 4)
        bodies = " ".join(item_lines).lower()
        self.assertNotIn("left shoe", bodies)
        self.assertFalse(any(re.match(r"(?i)^\s*size\s*:\s*", l) for l in merged))
        self.assertIn("tribal", bodies)
        self.assertIn("alfred", bodies)
        self.assertIn("clog", bodies)
        self.assertIn("trouser", bodies)

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


    def test_columbia_jacket_plus_vest_two_items_not_many(self) -> None:
        lines = [
            "Components: Full-zip front, zippered chest pocket, fleece-lined collar, mock collar, "
            "two-tone grey and black Columbia softshell jacket, ribbed V-neck, green sleeveless vest",
            "Style: V-neck pullover golf vest",
            "Material: Polyester blend",
            "Size: XL",
            "Brand: Columbia",
        ]
        merged = dedupe_specification_lines_preserve_order(lines)
        self.assertFalse(any(l.lower().startswith("components:") for l in merged))
        item_lines = [l for l in merged if l.lower().startswith("item ")]
        self.assertEqual(len(item_lines), 2)
        bodies = " ".join(item_lines).lower()
        self.assertIn("columbia", bodies)
        self.assertIn("vest", bodies)
        prose = (
            "A two-tone grey and black Columbia softshell jacket. "
            "A classic black Columbia fleece jacket. "
            "Several distinct pieces in this lot—refer to the photos."
        )
        fixed = _ensure_multi_piece_description(
            _collapse_conflicting_apparel_prose(prose, merged), merged
        )
        self.assertNotIn("several distinct", fixed.lower())
        self.assertNotIn("fleece jacket", fixed.lower())


if __name__ == "__main__":
    unittest.main()
