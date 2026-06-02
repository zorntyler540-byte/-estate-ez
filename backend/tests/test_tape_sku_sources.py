"""SKU tape detection helpers (blue tape + hero fallback)."""

from __future__ import annotations

import unittest

from app.services.pipeline_integration import _sku_candidate_filenames
from app.services.tape_focus import _is_blue_masking_tape_pixel


class TestTapeSkuSources(unittest.TestCase):
    def test_blue_masking_pixel(self) -> None:
        # Cyan-blue painter's tape (high G and B, moderate R)
        self.assertTrue(_is_blue_masking_tape_pixel(40, 160, 200))
        self.assertFalse(_is_blue_masking_tape_pixel(20, 20, 20))

    def test_sku_candidates_include_hero_when_no_tape_frames(self) -> None:
        names = _sku_candidate_filenames(
            {"tape_filenames": [], "hero_filename": "IMG_hero.jpg"}
        )
        self.assertEqual(names, ["IMG_hero.jpg"])

    def test_sku_candidates_tape_before_hero(self) -> None:
        names = _sku_candidate_filenames(
            {
                "tape_filenames": ["tape.jpg"],
                "hero_filename": "hero.jpg",
            }
        )
        self.assertEqual(names, ["tape.jpg", "hero.jpg"])


if __name__ == "__main__":
    unittest.main()
