from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from app.services.image_preprocess import load_pipeline_working_jpeg
from app.services.memory_trim import trim_process_memory
from app.worker import parse_worker_argv


def test_load_pipeline_working_jpeg_is_smaller_than_catalog_original(tmp_path: Path) -> None:
    huge = tmp_path / "lot.jpg"
    Image.new("RGB", (3200, 2400), (12, 80, 40)).save(huge, format="JPEG", quality=95)
    original = huge.stat().st_size
    working = load_pipeline_working_jpeg(huge, max_edge=1600, quality=80)
    with Image.open(BytesIO(working)) as im:
        assert max(im.size) <= 1600
    assert len(working) < original


def test_trim_process_memory_does_not_raise() -> None:
    trim_process_memory()


def test_worker_parse_run_job_argv() -> None:
    assert parse_worker_argv(["app.worker", "--run-job", "jid", "acct"]) == ("jid", "acct")
    assert parse_worker_argv(["app.worker"]) is None
    assert parse_worker_argv(["app.worker", "--run-job", "jid"]) is None
