"""SKU tape detection helpers (blue tape + hero fallback + MS-prefix markings)."""

from __future__ import annotations

import unittest

from app.core.lotting import (
    LotOutput,
    normalize_manual_sku_input,
    strip_product_box_misread_as_tape_sku,
    tape_marking_canonical,
)
from app.services.pipeline_integration import _sku_candidate_filenames, _triage_tape_marking
from app.services.tape_focus import _is_blue_masking_tape_pixel


class TestTapeSkuSources(unittest.TestCase):
    def test_blue_masking_pixel(self) -> None:
        self.assertTrue(_is_blue_masking_tape_pixel(40, 160, 200))
        self.assertFalse(_is_blue_masking_tape_pixel(20, 20, 20))

    def test_tape_marking_canonical_ms_prefix(self) -> None:
        self.assertEqual(tape_marking_canonical("MS 530"), "MS530")
        self.assertEqual(tape_marking_canonical("ms530"), "MS530")

    def test_tape_marking_canonical_tz_prefix(self) -> None:
        self.assertEqual(tape_marking_canonical("tz8000"), "TZ8000")
        self.assertEqual(tape_marking_canonical("TZ 8000"), "TZ8000")

    def test_tape_marking_canonical_letter_suffix(self) -> None:
        self.assertEqual(tape_marking_canonical("JH603SK"), "JH603SK")
        self.assertEqual(tape_marking_canonical("JH 600SK"), "JH600SK")
        self.assertEqual(tape_marking_canonical("Ms6000RG"), "MS6000RG")

    def test_normalize_manual_sku_keeps_full_code(self) -> None:
        self.assertEqual(normalize_manual_sku_input("jh603sk"), "JH603SK")
        self.assertEqual(normalize_manual_sku_input("JH 600SK"), "JH600SK")
        self.assertEqual(normalize_manual_sku_input("Ms6000RG"), "MS6000RG")

    def test_manual_sku_preserves_letter_prefix(self) -> None:
        from types import SimpleNamespace

        from app.services.processing import _apply_single_lot_manual_sku

        lot = LotOutput(sku="9999", auction_title="x", professional_description="y")
        job = SimpleNamespace(single_lot_mode=True, single_lot_manual_sku="tz8000")
        _apply_single_lot_manual_sku(job, lot)
        self.assertEqual(lot.sku, "TZ8000")

    def test_manual_sku_preserves_letter_suffix(self) -> None:
        from types import SimpleNamespace

        from app.services.processing import _apply_single_lot_manual_sku

        lot = LotOutput(sku="9999", auction_title="x", professional_description="y")
        job = SimpleNamespace(single_lot_mode=True, single_lot_manual_sku="JH603SK")
        _apply_single_lot_manual_sku(job, lot)
        self.assertEqual(lot.sku, "JH603SK")

    def test_manual_sku_digits_only_still_works(self) -> None:
        from types import SimpleNamespace

        from app.services.processing import _apply_single_lot_manual_sku

        lot = LotOutput(sku=None, auction_title="x", professional_description="y")
        job = SimpleNamespace(single_lot_mode=True, single_lot_manual_sku="8000")
        _apply_single_lot_manual_sku(job, lot)
        self.assertEqual(lot.sku, "8000")

    def test_triage_tape_marking_prefers_lot_tape_field(self) -> None:
        row = {"lot_tape_marking": "MS 530", "brand_text": "ERTL"}
        self.assertEqual(_triage_tape_marking(row), "MS530")

    def test_sku_candidates_include_hero_when_no_tape_frames(self) -> None:
        names = _sku_candidate_filenames(
            {"tape_filenames": [], "hero_filename": "IMG_hero.jpg"},
            [],
        )
        self.assertEqual(names, ["IMG_hero.jpg"])

    def test_sku_candidates_tape_before_hero(self) -> None:
        names = _sku_candidate_filenames(
            {
                "tape_filenames": ["tape.jpg"],
                "hero_filename": "hero.jpg",
            },
            [],
        )
        self.assertEqual(names, ["tape.jpg", "hero.jpg"])

    def test_sku_candidates_include_triage_lot_tape_marking_frame(self) -> None:
        names = _sku_candidate_filenames(
            {"tape_filenames": [], "hero_filename": "hero.jpg", "listing_filenames": ["hero.jpg"]},
            [{"filename": "hero.jpg", "lot_tape_marking": "MS530", "photo_role": "hero"}],
        )
        self.assertEqual(names[0], "hero.jpg")

    def test_strip_product_box_misread(self) -> None:
        lot = LotOutput(
            sku="7209",
            auction_title="ERTL 1:18 Scale 1996 Firebird ITEM 7209 Die-Cast Model",
            professional_description="Collector's edition model car.",
            specification_lines=[],
            brands=[],
            labels_and_tags=[],
            condition="GOOD",
        )
        strip_product_box_misread_as_tape_sku(lot)
        self.assertIsNone(lot.sku)


if __name__ == "__main__":
    unittest.main()
