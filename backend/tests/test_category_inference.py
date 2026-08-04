"""CT category inference for art supplies, jewelry, glassware, and kitchenware."""

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

    def test_portmeirion_bakeware_not_household_other(self) -> None:
        lot = LotOutput(
            sku="23001",
            auction_title="Portmeirion Pomona & The Botanic Garden Bakeware Collection",
            professional_description=(
                "English garden charm with botanical illustrations of Susan Williams-Ellis. "
                "Oven-safe ceramic pieces for baking and serving."
            ),
            brands=["Portmeirion"],
            condition="good",
        )
        category, _sub = infer_ct_category(lot)
        self.assertIn(category, {"Kitchen & Home", "Dining Room"})
        self.assertNotEqual(category, "Household Goods Other")

    def test_crystal_highballs_are_glassware_or_crystal(self) -> None:
        lot = LotOutput(
            sku="88",
            auction_title="Ralph Lauren Cut-Crystal Highball Set",
            professional_description="Six cut-crystal highballs with deep cross-hatch pattern.",
            condition="good",
        )
        category, _sub = infer_ct_category(lot)
        self.assertIn(category, {"Glassware", "Crystal"})

    def test_stemware_maps_to_glassware(self) -> None:
        lot = LotOutput(
            sku="89",
            auction_title="Set of Wine Glasses Stemware",
            professional_description="Clear stemware wine glasses for entertaining.",
            condition="good",
        )
        category, _sub = infer_ct_category(lot)
        self.assertEqual(category, "Glassware")

    def test_hardcover_book_is_books_not_decorative_collectibles(self) -> None:
        from app.services.category_inference import prefer_keyword_category_over_stored

        lot = LotOutput(
            sku="Z3130",
            auction_title='1943 Johnny Gruelle "Raggedy Ann and Betsy Bonnet String" Hardcover Book',
            professional_description=(
                "A delightful piece of childhood history—this is the 1943 hardcover edition "
                'of "Raggedy Ann and Betsy Bonnet String" by Johnny Gruelle.'
            ),
            condition="good",
        )
        category, _sub = infer_ct_category(lot)
        self.assertEqual(category, "Books")
        preferred, _ = prefer_keyword_category_over_stored("Decorative Collectibles", lot)
        self.assertEqual(preferred, "Books")

    def test_collectible_word_does_not_steal_books(self) -> None:
        lot = LotOutput(
            sku="90",
            auction_title="Vintage Collectible Hardcover Book",
            professional_description="A collectible childhood classic book in hardcover.",
            condition="good",
        )
        category, _sub = infer_ct_category(lot)
        self.assertEqual(category, "Books")
        self.assertNotEqual(category, "Decorative Collectibles")

    def test_curated_ai_categories_subset_of_live(self) -> None:
        from app.services.ctseller_categories import (
            CT_SELLER_CATEGORIES,
            CT_SELLER_CATEGORIES_FOR_AI,
            ct_categories_for_ai,
        )

        self.assertTrue(CT_SELLER_CATEGORIES_FOR_AI)
        self.assertTrue(CT_SELLER_CATEGORIES_FOR_AI <= CT_SELLER_CATEGORIES)
        self.assertEqual(ct_categories_for_ai(), sorted(CT_SELLER_CATEGORIES_FOR_AI))

    def test_resolve_keeps_specific_ai_category(self) -> None:
        from app.services.category_inference import resolve_lot_ct_category

        lot = LotOutput(
            sku="1",
            auction_title="Ceramic Mixing Bowls",
            professional_description="A set of mixing bowls for everyday baking.",
            condition="good",
            ct_category="Kitchen & Home",
        )
        category, _sub = resolve_lot_ct_category(lot)
        self.assertEqual(category, "Kitchen & Home")

    def test_resolve_overrides_weak_ai_with_books_keywords(self) -> None:
        from app.services.category_inference import resolve_lot_ct_category

        lot = LotOutput(
            sku="Z3130",
            auction_title='1943 Johnny Gruelle "Raggedy Ann and Betsy Bonnet String" Hardcover Book',
            professional_description=(
                "A delightful piece of childhood history—this is the 1943 hardcover edition "
                'of "Raggedy Ann and Betsy Bonnet String" by Johnny Gruelle.'
            ),
            condition="good",
            ct_category="Decorative Collectibles",
        )
        category, _sub = resolve_lot_ct_category(lot)
        self.assertEqual(category, "Books")

    def test_gaming_mouse_is_electronics(self) -> None:
        lot = LotOutput(
            sku="8000",
            auction_title="Razer Naga MMO Gaming Mouse with 12-Button Thumb Grid",
            professional_description=(
                "Ready to dominate the game? This Razer Naga MMO Gaming Mouse is engineered "
                "for precision and control, featuring its signature 12-button mechanical thumb grid."
            ),
            brands=["Razer"],
            condition="fair",
        )
        category, _sub = infer_ct_category(lot)
        self.assertEqual(category, "Electronics")

    def test_resolve_overrides_household_other_for_gaming_mouse(self) -> None:
        from app.services.category_inference import resolve_lot_ct_category

        lot = LotOutput(
            sku="8000",
            auction_title="Razer Naga MMO Gaming Mouse with 12-Button Thumb Grid",
            professional_description="Razer Naga MMO gaming mouse with 12-button thumb grid.",
            brands=["Razer"],
            condition="fair",
            ct_category="Household Goods Other",
        )
        category, _sub = resolve_lot_ct_category(lot)
        self.assertEqual(category, "Electronics")

    def test_pro_manifest_schema_includes_ct_category_enum(self) -> None:
        from pipeline.gemini_schema import fully_audited_manifest_gemini_schema
        from app.services.ctseller_categories import ct_categories_for_ai

        schema = fully_audited_manifest_gemini_schema()
        props = schema.get("properties") or {}
        cat = props.get("ct_category") or {}
        self.assertIn("enum", cat)
        self.assertEqual(cat["enum"], ct_categories_for_ai())
        self.assertIn("Electronics", cat["enum"])

    def test_apply_pro_manifest_sets_ct_category(self) -> None:
        from app.core.lotting import LotOutput
        from app.services.pipeline_integration import _apply_pro_manifest_to_lot
        from pipeline.schemas import FullyAuditedManifest

        lot = LotOutput(
            sku="8000",
            auction_title="Old title",
            professional_description="Old desc",
            ct_category="Household Goods Other",
        )
        manifest = FullyAuditedManifest(
            final_auction_title="Razer Naga MMO Gaming Mouse",
            final_professional_description="A Razer gaming mouse with thumb grid.",
            ct_category="Electronics",
            total_items_processed=1,
        )
        _apply_pro_manifest_to_lot(lot, manifest.model_dump_json())
        self.assertEqual(lot.ct_category, "Electronics")
        self.assertIn("Razer", lot.auction_title)

    def test_inventory_schema_includes_ct_category_enum(self) -> None:
        from app.core.gemini import inventory_table_entry_gemini_schema
        from app.services.ctseller_categories import ct_categories_for_ai

        schema = inventory_table_entry_gemini_schema()
        props = schema.get("properties") or {}
        cat = props.get("ct_category") or {}
        self.assertIn("enum", cat)
        self.assertEqual(cat["enum"], ct_categories_for_ai())


if __name__ == "__main__":
    unittest.main()
