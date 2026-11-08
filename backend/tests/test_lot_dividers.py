"""Blank divider frames split batch uploads into lots when no SKU tape is used (no live Gemini)."""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw

from app.config import settings
from app.services.lot_dividers import frame_edge_fractions, frame_looks_blank, is_lot_divider
from app.services.processing import _segment_batch_lots_by_tape
from pipeline.schemas import PhotoTriageResult


def _blank_page(path: Path, *, base: int = 235) -> Path:
    """Paper / wall: soft lighting gradient plus sensor noise, nothing in frame."""
    rng = random.Random(7)
    im = Image.new("RGB", (1200, 900))
    px = im.load()
    for y in range(900):
        for x in range(1200):
            v = base - int(30 * x / 1200) - int(15 * y / 900) + rng.randint(-6, 6)
            px[x, y] = (v, v, max(0, v - 8))
    im.save(path, "JPEG", quality=85)
    return path


def _item_photo(path: Path) -> Path:
    """Busy table of objects."""
    rng = random.Random(3)
    im = Image.new("RGB", (1200, 900), (180, 150, 120))
    draw = ImageDraw.Draw(im)
    for _ in range(40):
        x, y = rng.randint(0, 1100), rng.randint(0, 800)
        w, h = rng.randint(30, 200), rng.randint(30, 200)
        color = tuple(rng.randint(0, 255) for _ in range(3))
        draw.rectangle((x, y, x + w, y + h), fill=color, outline=(0, 0, 0), width=4)
    im.save(path, "JPEG", quality=85)
    return path


def _paper_on_table(path: Path) -> Path:
    """Blank sheet filling the middle of the frame with wood table showing around it."""
    rng = random.Random(5)
    im = Image.new("RGB", (1200, 900), (120, 80, 50))
    draw = ImageDraw.Draw(im)
    for _ in range(60):
        y = rng.randint(0, 900)
        draw.line((0, y, 1200, y + rng.randint(-20, 20)), fill=(105, 70, 42), width=3)
    draw.polygon([(110, 60), (1110, 90), (1090, 860), (90, 830)], fill=(242, 242, 238))
    im.save(path, "JPEG", quality=85)
    return path


def _figurine_on_backdrop(path: Path) -> Path:
    """Small dark object centered on a seamless light backdrop."""
    im = Image.new("RGB", (1200, 900), (230, 228, 222))
    draw = ImageDraw.Draw(im)
    draw.ellipse((540, 300, 660, 420), fill=(60, 40, 30))
    draw.rectangle((560, 410, 640, 640), fill=(70, 50, 35))
    im.save(path, "JPEG", quality=85)
    return path


def _item_off_center(path: Path) -> Path:
    """Blank-ish middle but a busy object pile along one side."""
    rng = random.Random(9)
    im = Image.new("RGB", (1200, 900), (235, 235, 235))
    draw = ImageDraw.Draw(im)
    for _ in range(60):
        x, y = rng.randint(0, 200), rng.randint(0, 850)
        draw.rectangle((x, y, x + 60, y + 50), fill=tuple(rng.randint(0, 200) for _ in range(3)), outline=(0, 0, 0), width=3)
    im.save(path, "JPEG", quality=85)
    return path


def _plate_on_white(path: Path) -> Path:
    """One white dish on a white cloth: almost blank pixels, but a real item."""
    im = Image.new("RGB", (1200, 900), (240, 240, 238))
    draw = ImageDraw.Draw(im)
    draw.ellipse((350, 200, 850, 700), fill=(250, 250, 250), outline=(150, 150, 150), width=6)
    im.save(path, "JPEG", quality=85)
    return path


def _row(filename: str, *, divider: bool = False, marking: str | None = None) -> dict:
    return {
        "filename": filename,
        "photo_role": "unusable" if divider else ("tape" if marking else "item_front"),
        "lot_tape_marking": marking,
        "label_legibility": "clear" if marking else "none",
        "quality": "sharp",
        "confidence": "high",
        "is_lot_divider": divider,
    }


def _batch(tmp_path: Path, spec: list[str]) -> tuple[Path, list[tuple[Path, str, dict]]]:
    """spec letters: i = item photo flagged normal, d = blank flagged divider,
    f = item photo wrongly flagged divider, b = blank photo NOT flagged divider."""
    folder = tmp_path / "batch"
    folder.mkdir(exist_ok=True)
    ordered = []
    for i, kind in enumerate(spec):
        path = folder / f"web-b1-{i + 1:04d}.jpg"
        if kind in ("d", "b"):
            _blank_page(path)
        else:
            _item_photo(path)
        ordered.append((path, path.name, _row(path.name, divider=kind in ("d", "f"))))
    return folder, ordered


def _names(segments) -> list[list[int]]:
    return [[int(p.stem.rsplit("-", 1)[1]) for p, _k in seg] for seg in segments]


def test_blank_page_wall_and_dark_frame_look_blank(tmp_path: Path) -> None:
    assert frame_looks_blank(_blank_page(tmp_path / "page.jpg"))
    assert frame_looks_blank(_blank_page(tmp_path / "dark.jpg", base=40))
    assert frame_looks_blank(_paper_on_table(tmp_path / "paper.jpg"))


def test_item_photos_do_not_look_blank(tmp_path: Path) -> None:
    assert not frame_looks_blank(_item_photo(tmp_path / "items.jpg"))
    assert not frame_looks_blank(_figurine_on_backdrop(tmp_path / "figurine.jpg"))
    assert not frame_looks_blank(_item_off_center(tmp_path / "side.jpg"))


def test_unreadable_file_is_never_a_divider(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    assert frame_edge_fractions(bad) is None
    assert not is_lot_divider(bad, _row("bad.jpg", divider=True))


def test_white_plate_needs_triage_agreement(tmp_path: Path) -> None:
    path = _plate_on_white(tmp_path / "plate.jpg")
    assert not is_lot_divider(path, _row(path.name, divider=False))


def test_divider_with_tape_marking_is_not_a_divider(tmp_path: Path) -> None:
    path = _blank_page(tmp_path / "tape.jpg")
    assert not is_lot_divider(path, _row(path.name, divider=True, marking="SR006"))


def test_dividers_split_lots_and_are_dropped(tmp_path: Path) -> None:
    folder, ordered = _batch(tmp_path, list("iidiiidi"))
    dividers: list[Path] = []
    segments = _segment_batch_lots_by_tape(folder, ordered, dividers=dividers)
    assert _names(segments) == [[1, 2], [4, 5, 6], [8]]
    assert [p.name for p in dividers] == ["web-b1-0003.jpg", "web-b1-0007.jpg"]


def test_leading_trailing_and_repeated_dividers_make_no_empty_lots(tmp_path: Path) -> None:
    folder, ordered = _batch(tmp_path, list("ddiidddiid"))
    segments = _segment_batch_lots_by_tape(folder, ordered)
    assert _names(segments) == [[3, 4], [8, 9]]


def test_either_signal_alone_keeps_the_photo(tmp_path: Path) -> None:
    folder, ordered = _batch(tmp_path, list("iifibi"))
    segments = _segment_batch_lots_by_tape(folder, ordered)
    assert _names(segments) == [[1, 2, 3, 4, 5, 6]]


def test_divider_split_can_be_turned_off(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "catalog_divider_splits", False)
    folder, ordered = _batch(tmp_path, list("iidii"))
    segments = _segment_batch_lots_by_tape(folder, ordered)
    assert _names(segments) == [[1, 2, 3, 4, 5]]


def test_divider_resets_tape_so_next_sku_does_not_double_split(tmp_path: Path) -> None:
    folder, ordered = _batch(tmp_path, list("iidii"))
    p0, k0, r0 = ordered[0]
    ordered[0] = (p0, k0, {**r0, "photo_role": "tape", "lot_tape_marking": "100"})
    p3, k3, r3 = ordered[3]
    ordered[3] = (p3, k3, {**r3, "photo_role": "tape", "lot_tape_marking": "200"})
    segments = _segment_batch_lots_by_tape(folder, ordered)
    assert _names(segments) == [[1, 2], [4, 5]]


def test_triage_schema_has_divider_flag_defaulting_false() -> None:
    row = PhotoTriageResult(
        filename="a.jpg",
        photo_role="item_front",
        shows_whole_lot=False,
        is_primary_front_for_item=True,
        label_legibility="none",
        brand_text="",
        quality="sharp",
        confidence="high",
    )
    assert row.is_lot_divider is False
