"""Batch upload lot segmentation by estate tape SKU (no live Gemini)."""

from __future__ import annotations

from pathlib import Path

from app.services.processing import _segment_batch_lots_by_tape


def _row(filename: str, marking: str | None = None) -> dict:
    return {
        "filename": filename,
        "photo_role": "tape" if marking else "hero",
        "lot_tape_marking": marking,
        "label_legibility": "clear" if marking else "none",
        "quality": "sharp",
        "confidence": "high",
    }


def test_segment_splits_on_new_tape_marking(tmp_path: Path) -> None:
    folder = tmp_path / "batch"
    folder.mkdir()
    paths = [folder / f"IMG_{i:04d}.jpg" for i in range(5)]
    ordered = [
        (paths[0], paths[0].name, _row(paths[0].name)),
        (paths[1], paths[1].name, _row(paths[1].name, "100")),
        (paths[2], paths[2].name, _row(paths[2].name)),
        (paths[3], paths[3].name, _row(paths[3].name, "200")),
        (paths[4], paths[4].name, _row(paths[4].name)),
    ]
    segments = _segment_batch_lots_by_tape(folder, ordered)
    assert len(segments) == 3
    assert [p.name for p, _ in segments[0]] == ["IMG_0000.jpg"]
    assert [p.name for p, _ in segments[1]] == ["IMG_0001.jpg", "IMG_0002.jpg"]
    assert [p.name for p, _ in segments[2]] == ["IMG_0003.jpg", "IMG_0004.jpg"]


def test_first_tape_after_items_starts_the_next_lot(tmp_path: Path) -> None:
    """Masks, then the tape photo, then the jewelry: the tape starts the jewelry lot."""
    folder = tmp_path / "batch"
    folder.mkdir()
    paths = [folder / f"IMG_{i:04d}.jpg" for i in range(3832, 3842)]
    ordered = []
    for path in paths:
        marking = "DK3001RG" if path.name == "IMG_3839.jpg" else None
        ordered.append((path, path.name, _row(path.name, marking)))
    segments = _segment_batch_lots_by_tape(folder, ordered)
    assert [p.name for p, _ in segments[0]] == [f"IMG_{i:04d}.jpg" for i in range(3832, 3839)]
    assert [p.name for p, _ in segments[1]] == [f"IMG_{i:04d}.jpg" for i in range(3839, 3842)]


def test_tape_photo_splits_even_when_the_number_matches(tmp_path: Path) -> None:
    """A hero already read DK3001RG; the dedicated tape photo still starts the next lot."""
    folder = tmp_path / "batch"
    folder.mkdir()
    paths = [folder / name for name in ("mask.jpg", "mask2.jpg", "tape.jpg", "jewel.jpg", "tape2.jpg")]
    hero = _row("mask2.jpg", "DK3001RG")
    hero["photo_role"] = "hero"
    tape = _row("tape.jpg", "DK3001RG")
    again = _row("tape2.jpg", "DK3001RG")
    ordered = [
        (paths[0], paths[0].name, _row(paths[0].name)),
        (paths[1], paths[1].name, hero),
        (paths[2], paths[2].name, tape),
        (paths[3], paths[3].name, _row(paths[3].name)),
        (paths[4], paths[4].name, again),
    ]
    segments = _segment_batch_lots_by_tape(folder, ordered)
    assert [p.name for p, _ in segments[0]] == ["mask.jpg", "mask2.jpg"]
    assert [p.name for p, _ in segments[1]] == ["tape.jpg", "jewel.jpg", "tape2.jpg"]


def test_tape_frame_without_a_readable_number_still_divides(tmp_path: Path) -> None:
    folder = tmp_path / "batch"
    folder.mkdir()
    paths = [folder / f"p{i}.jpg" for i in range(3)]
    tape = _row(paths[1].name)
    tape["photo_role"] = "tape"
    ordered = [
        (paths[0], paths[0].name, _row(paths[0].name)),
        (paths[1], paths[1].name, tape),
        (paths[2], paths[2].name, _row(paths[2].name)),
    ]
    segments = _segment_batch_lots_by_tape(folder, ordered)
    assert len(segments) == 2
    assert [p.name for p, _ in segments[0]] == ["p0.jpg"]
    assert [p.name for p, _ in segments[1]] == ["p1.jpg", "p2.jpg"]


def test_segment_keeps_same_tape_in_one_lot(tmp_path: Path) -> None:
    folder = tmp_path / "batch"
    folder.mkdir()
    paths = [folder / f"a{i}.jpg" for i in range(3)]
    ordered = [
        (paths[0], paths[0].name, _row(paths[0].name, "MS530")),
        (paths[1], paths[1].name, _row(paths[1].name)),
        (paths[2], paths[2].name, _row(paths[2].name, "MS530")),
    ]
    segments = _segment_batch_lots_by_tape(folder, ordered)
    assert len(segments) == 1
    assert len(segments[0]) == 3


def test_segment_splits_on_subfolder(tmp_path: Path) -> None:
    folder = tmp_path / "batch"
    sub_a = folder / "035"
    sub_b = folder / "036"
    sub_a.mkdir(parents=True)
    sub_b.mkdir(parents=True)
    pa = sub_a / "hero.jpg"
    pb = sub_b / "hero.jpg"
    ordered = [
        (pa, pa.name, _row(pa.name, "100")),
        (pb, pb.name, _row(pb.name, "100")),
    ]
    segments = _segment_batch_lots_by_tape(folder, ordered)
    assert len(segments) == 2
