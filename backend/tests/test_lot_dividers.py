"""Blank divider frames split batch uploads into lots when no SKU tape is used (no live Gemini)."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.config import settings
from app.services import lot_dividers
from app.services.lot_dividers import (
    find_lot_dividers,
    frame_blank_probe,
    frame_edge_fractions,
    frame_is_confident_blank,
    frame_looks_blank,
    is_lot_divider,
)
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


@pytest.fixture(autouse=True)
def _no_context_call(monkeypatch):
    monkeypatch.setattr(lot_dividers, "check_divider_in_context", lambda *_a: None)


def _sheet_edge_down_middle(path: Path) -> Path:
    """Half dark backdrop, half white sheet: the sheet's edge runs through the centre."""
    rng = random.Random(11)
    im = Image.new("RGB", (900, 1200), (70, 75, 55))
    px = im.load()
    for y in range(1200):
        for x in range(450, 900):
            v = 225 + rng.randint(-5, 5) - int(10 * y / 1200)
            px[x, y] = (v, v, v)
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
    assert frame_looks_blank(_sheet_edge_down_middle(tmp_path / "sheet.jpg"))


def test_item_photos_do_not_look_blank(tmp_path: Path) -> None:
    assert not frame_looks_blank(_item_photo(tmp_path / "items.jpg"))
    assert not frame_looks_blank(_item_off_center(tmp_path / "side.jpg"))


def test_plain_backdrop_item_needs_gemini_agreement(tmp_path: Path) -> None:
    path = _figurine_on_backdrop(tmp_path / "figurine.jpg")
    assert not is_lot_divider(path, _row(path.name, divider=False))


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


def test_context_check_overrides_the_single_photo_flag(tmp_path: Path, monkeypatch) -> None:
    folder, ordered = _batch(tmp_path, list("iibiidii"))
    verdicts = {"web-b1-0003.jpg": True, "web-b1-0006.jpg": False}
    seen: list[tuple[str, str | None, str | None]] = []

    def fake(path, before, after):
        seen.append((path.name, before.name if before else None, after.name if after else None))
        return verdicts.get(path.name)

    monkeypatch.setattr(lot_dividers, "check_divider_in_context", fake)
    assert {p.name for p in find_lot_dividers(ordered)} == {"web-b1-0003.jpg"}
    assert ("web-b1-0003.jpg", "web-b1-0002.jpg", "web-b1-0004.jpg") in seen
    assert all(name in ("web-b1-0003.jpg", "web-b1-0006.jpg") for name, _b, _a in seen)
    segments = _segment_batch_lots_by_tape(folder, ordered)
    assert _names(segments) == [[1, 2], [4, 5, 6, 7, 8]]


def _creased_sheet(path: Path) -> Path:
    """White paper with a few hard folds: past the strict edge test, still low-detail."""
    im = Image.new("RGB", (1200, 900), (246, 246, 242))
    draw = ImageDraw.Draw(im)
    for x in (280, 460, 700, 900):
        draw.line((x, 0, x + 30, 900), fill=(30, 30, 28), width=7)
    im.save(path, "JPEG", quality=85)
    return path


def test_creased_sheet_splits_when_the_neighbour_check_agrees(tmp_path: Path, monkeypatch) -> None:
    folder, ordered = _batch(tmp_path, list("iii"))
    sheet = ordered[1][0]
    _creased_sheet(sheet)
    assert not frame_looks_blank(sheet)
    ordered[1] = (sheet, sheet.name, _row(sheet.name, divider=False))
    monkeypatch.setattr(lot_dividers, "check_divider_in_context", lambda path, _b, _a: path == sheet)
    assert find_lot_dividers(ordered) == {sheet}
    assert _names(_segment_batch_lots_by_tape(folder, ordered)) == [[1], [3]]


def test_triage_flag_on_a_real_photo_needs_the_neighbour_check(tmp_path: Path, monkeypatch) -> None:
    folder, ordered = _batch(tmp_path, list("ifi"))
    monkeypatch.setattr(lot_dividers, "check_divider_in_context", lambda *_a: False)
    assert find_lot_dividers(ordered) == set()
    assert _names(_segment_batch_lots_by_tape(folder, ordered)) == [[1, 2, 3]]


def test_context_unavailable_falls_back_to_triage_flag(tmp_path: Path) -> None:
    _folder, ordered = _batch(tmp_path, list("iibidi"))
    assert {p.name for p in find_lot_dividers(ordered)} == {"web-b1-0005.jpg"}


def _flat_white(path: Path) -> Path:
    Image.new("RGB", (80, 60), (248, 248, 246)).save(path, "JPEG", quality=85)
    return path


def _busy_thumb(path: Path, seed: int) -> Path:
    rng = random.Random(seed)
    im = Image.new("RGB", (64, 48), (90, 70, 50))
    draw = ImageDraw.Draw(im)
    for _ in range(8):
        x, y = rng.randint(0, 48), rng.randint(0, 32)
        draw.rectangle(
            (x, y, x + 14, y + 12),
            fill=tuple(rng.randint(0, 255) for _ in range(3)),
            outline=(0, 0, 0),
        )
    im.save(path, "JPEG", quality=70)
    return path


def _divider_indexes(n: int = 150) -> set[int]:
    """More than 40 separators, including the old 40-photo cap and a 100-photo boundary."""
    indexes = {i for i in range(n) if i % 3 == 2}
    indexes.update({40, 100})
    return indexes


def _expected_segments(n: int, dividers: set[int]) -> list[list[int]]:
    segments: list[list[int]] = []
    current: list[int] = []
    for i in range(n):
        if i in dividers:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(i)
    if current:
        segments.append(current)
    return segments


def test_flat_white_is_a_divider_without_a_model_or_triage_flag(tmp_path: Path, monkeypatch) -> None:
    path = _flat_white(tmp_path / "white.jpg")
    assert frame_is_confident_blank(frame_blank_probe(path))
    assert is_lot_divider(path, _row(path.name, divider=False))
    called = {"n": 0}

    def boom(*_a):
        called["n"] += 1
        return False

    monkeypatch.setattr(lot_dividers, "check_divider_in_context", boom)
    folder, ordered = _batch(tmp_path, list("iii"))
    white = ordered[1][0]
    _flat_white(white)
    ordered[1] = (white, white.name, _row(white.name, divider=False))
    assert find_lot_dividers(ordered) == {white}
    assert called["n"] == 0
    assert _names(_segment_batch_lots_by_tape(folder, ordered)) == [[1], [3]]


def test_flat_white_dividers_split_150_photos_including_boundaries(tmp_path: Path, monkeypatch) -> None:
    """150 frames, >40 white dividers, including indexes 40 and 100. No model, no dropped cut."""
    n = 150
    dividers = _divider_indexes(n)
    assert len(dividers) > 40 and 40 in dividers and 100 in dividers
    folder = tmp_path / "batch"
    folder.mkdir()
    ordered = []
    for i in range(n):
        path = folder / f"web-batch-{i:04d}.jpg"
        if i in dividers:
            _flat_white(path)
            row = _row(path.name, divider=False)
        else:
            _busy_thumb(path, i)
            row = _row(path.name, divider=False)
        ordered.append((path, path.name, row))
    monkeypatch.setattr(lot_dividers, "check_divider_in_context", lambda *_a: False)
    found = find_lot_dividers(ordered)
    assert {p.name for p in found} == {ordered[i][0].name for i in dividers}
    segments = _segment_batch_lots_by_tape(folder, ordered)
    assert [[int(p.stem.rsplit("-", 1)[1]) for p, _k in seg] for seg in segments] == _expected_segments(
        n, dividers
    )
    # The 40-photo part cap must not glue the frames on either side of a divider back together.
    from app.services.processing import cap_lot_segments

    for entries, _part, _parts in cap_lot_segments(segments):
        indexes = [int(p.stem.rsplit("-", 1)[1]) for p, _k in entries]
        assert not any(i in dividers for i in indexes)
        assert max(indexes) - min(indexes) == len(indexes) - 1


def test_more_than_forty_ambiguous_dividers_are_all_checked(tmp_path: Path, monkeypatch) -> None:
    """Ambiguous frames past the old cap of 40 still get a neighbour check, including index 100."""
    n = 150
    dividers = _divider_indexes(n)
    folder = tmp_path / "batch"
    folder.mkdir()
    ordered = []
    for i in range(n):
        path = folder / f"web-batch-{i:04d}.jpg"
        _busy_thumb(path, i + 1)
        ordered.append((path, path.name, _row(path.name, divider=i in dividers)))
    assert not frame_is_confident_blank(frame_blank_probe(ordered[100][0]))
    seen: list[tuple[str, str | None, str | None]] = []

    def fake(path, before, after):
        seen.append((path.name, before.name if before else None, after.name if after else None))
        return path.name in {ordered[i][0].name for i in dividers}

    monkeypatch.setattr(lot_dividers, "check_divider_in_context", fake)
    found = find_lot_dividers(ordered)
    assert len(seen) > 40
    assert {ordered[i][0].name for i in dividers} <= {name for name, _b, _a in seen}
    boundary = ordered[100][0].name
    assert (boundary, ordered[99][0].name, ordered[101][0].name) in seen
    assert {p.name for p in found} == {ordered[i][0].name for i in dividers}
    segments = _segment_batch_lots_by_tape(folder, ordered)
    assert [[int(p.stem.rsplit("-", 1)[1]) for p, _k in seg] for seg in segments] == _expected_segments(
        n, dividers
    )


def test_ambiguous_divider_checks_run_concurrently(tmp_path: Path, monkeypatch) -> None:
    import threading
    import time

    monkeypatch.setattr(settings, "catalog_divider_check_concurrency", 4)
    folder = tmp_path / "batch"
    folder.mkdir()
    ordered = []
    for i in range(12):
        path = folder / f"web-batch-{i:04d}.jpg"
        _busy_thumb(path, i + 3)
        ordered.append((path, path.name, _row(path.name, divider=True)))
    inflight = 0
    peak = 0
    lock = threading.Lock()

    def fake(_path, _before, _after):
        nonlocal inflight, peak
        with lock:
            inflight += 1
            peak = max(peak, inflight)
        time.sleep(0.03)
        with lock:
            inflight -= 1
        return False

    monkeypatch.setattr(lot_dividers, "check_divider_in_context", fake)
    assert find_lot_dividers(ordered) == set()
    assert peak >= 2


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
