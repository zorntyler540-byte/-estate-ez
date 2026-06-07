"""Tests for appraisal check worker helpers."""

from __future__ import annotations

import unittest

from app.services.appraisal import _appraisal_photo_label


class TestAppraisalService(unittest.TestCase):
    def test_duplicate_original_names_get_unique_worker_labels(self) -> None:
        names = ["image.jpg", "image.jpg"]

        self.assertEqual(
            _appraisal_photo_label(0, "appraisals/acct/check/00.jpg", names),
            "01 - image.jpg",
        )
        self.assertEqual(
            _appraisal_photo_label(1, "appraisals/acct/check/01.jpg", names),
            "02 - image.jpg",
        )


if __name__ == "__main__":
    unittest.main()
