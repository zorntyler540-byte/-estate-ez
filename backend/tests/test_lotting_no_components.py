"""Components / Style / Finish stripped from listing specs."""

from __future__ import annotations

import unittest

from app.core.lotting import (
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


if __name__ == "__main__":
    unittest.main()
