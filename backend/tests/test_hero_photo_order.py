"""Hero-first photo ordering so dashboard thumbs match AI titles."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.services.processing import _hero_filename_from_pipeline, _order_paths_hero_first


class TestHeroFirstPhotoOrder(unittest.TestCase):
    def test_moves_hero_to_front(self) -> None:
        paths = [Path("a.jpg"), Path("musket_hero.jpg"), Path("b.jpg")]
        ordered = _order_paths_hero_first(paths, "musket_hero.jpg")
        self.assertEqual([p.name for p in ordered], ["musket_hero.jpg", "a.jpg", "b.jpg"])

    def test_noop_when_hero_already_first(self) -> None:
        paths = [Path("hero.jpg"), Path("b.jpg")]
        ordered = _order_paths_hero_first(paths, "hero.jpg")
        self.assertEqual([p.name for p in ordered], ["hero.jpg", "b.jpg"])

    def test_noop_when_hero_missing(self) -> None:
        paths = [Path("a.jpg"), Path("b.jpg")]
        ordered = _order_paths_hero_first(paths, "missing.jpg")
        self.assertEqual([p.name for p in ordered], ["a.jpg", "b.jpg"])

    def test_hero_filename_from_pipeline(self) -> None:
        self.assertEqual(
            _hero_filename_from_pipeline({"selected": {"hero_filename": "x.jpg"}}),
            "x.jpg",
        )
        self.assertIsNone(_hero_filename_from_pipeline({}))
        self.assertIsNone(_hero_filename_from_pipeline(None))


if __name__ == "__main__":
    unittest.main()
