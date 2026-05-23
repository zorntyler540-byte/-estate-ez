"""Components line stripped from all listing specs."""

from __future__ import annotations

import unittest

from app.core.lotting import dedupe_specification_lines_preserve_order


class TestNoComponentsSpec(unittest.TestCase):
    def test_dedupe_strips_components_for_supply_lot(self) -> None:
        lines = [
            "Components: Moleskine black pocket notebook; Creative Mark 5 x 7 sketchbooks",
            "Item 1: Moleskine black pocket notebook with elastic closure",
            "Item 2: Creative Mark 5 x 7 black textured hardcover sketchbooks",
            "Brand: Prismacolor",
            "Material: Paper",
        ]
        merged = dedupe_specification_lines_preserve_order(lines)
        self.assertFalse(any(l.lower().startswith("components:") for l in merged))
        self.assertEqual(len([l for l in merged if l.lower().startswith("item ")]), 2)


if __name__ == "__main__":
    unittest.main()
