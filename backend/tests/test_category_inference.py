"""CT category inference for art supplies and jewelry false positives."""

from __future__ import annotations

import unittest

from app.core.lotting import LotOutput
from app.services.category_inference import infer_ct_category


class TestCategoryInference(unittest.TestCase):
    def test_prismacolor_pencil_set_is_art_not_rings(self) -> None:
        lot = LotOutput(
            sku="501",
            auction_title="Prismacolor Colored Pencil Set in Zippered Case with Sketchpads",
            professional_description=(
                "This premium artist set features vibrant Prismacolor colored pencils "
                "in a zippered case with sketchpads."
            ),
            specification_lines=["Brand: Prismacolor", "Material: Paper"],
            brands=["Prismacolor"],
            condition="good",
        )
        category, _sub = infer_ct_category(lot)
        self.assertEqual(category, "Art")

    def test_ring_keyword_does_not_match_prismacolor(self) -> None:
        lot = LotOutput(
            sku="502",
            auction_title="Prismacolor Premier Pencil Tin",
            professional_description="Artist pencils in tin.",
            brands=["Prismacolor"],
            condition="good",
        )
        category, _sub = infer_ct_category(lot)
        self.assertNotEqual(category, "Rings")

    def test_framed_wall_art_maps_to_art_other(self) -> None:
        lot = LotOutput(
            sku="10",
            auction_title="Vintage Framed Wall Art Print",
            professional_description="Framed wall art print in wood frame.",
            condition="good",
        )
        category, _sub = infer_ct_category(lot)
        self.assertEqual(category, "Art Other")


if __name__ == "__main__":
    unittest.main()
