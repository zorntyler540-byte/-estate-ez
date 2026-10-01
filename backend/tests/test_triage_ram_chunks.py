from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.services.pipeline_integration import triage_ordered_photo_batch


def test_triage_batch_loads_only_chunk_sized_dicts(tmp_path: Path, monkeypatch) -> None:
    from PIL import Image

    paths = []
    for i in range(5):
        p = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (80, 60), (i * 40, 10, 10)).save(p, format="JPEG", quality=80)
        paths.append(p)

    seen_sizes: list[int] = []

    def _fake_triage(images, **kwargs):
        seen_sizes.append(len(images))
        return [
            {
                "filename": name,
                "photo_role": "angle_only",
                "label_legibility": "none",
                "quality": "ok",
                "confidence": "low",
            }
            for name in images
        ]

    monkeypatch.setenv("PIPELINE_TRIAGE_PARALLEL", "2")
    with patch("app.services.pipeline_integration._run_parallel_triage", side_effect=_fake_triage):
        with patch("app.services.pipeline_integration.settings") as settings:
            settings.pipeline_triage_parallel = 2
            settings.pipeline_working_max_edge = 1600
            settings.pipeline_working_jpeg_quality = 80
            settings.triage_image_max_edge = 2048
            settings.google_api_key = ""
            settings.gemini_flash_model = "gemini-2.5-flash"
            settings.gemini_pro_model = "gemini-2.5-pro"
            settings.listing_flash_max_frames = 5
            settings.brand_pro_max_label_frames = 4
            settings.brand_skip_pro_if_agreement = True
            settings.enable_grounded_valuation = False
            ordered = triage_ordered_photo_batch(paths)

    assert [p.name for p, _k, _r in ordered] == [p.name for p in paths]
    assert seen_sizes == [2, 2, 1]
    assert max(seen_sizes) == 2
