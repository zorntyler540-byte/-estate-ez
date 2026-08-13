"""Natural-order key used to repair photo order back to capture order."""

from __future__ import annotations

from app.services.photo_order import natural_photo_name_key


def test_natural_key_sorts_numerically_not_lexically() -> None:
    names = [
        "mobile-abc-10.jpg",
        "mobile-abc-2.jpg",
        "mobile-abc-1.jpg",
        "mobile-abc-21.jpg",
        "mobile-abc-3.jpg",
    ]
    ordered = sorted(names, key=natural_photo_name_key)
    assert ordered == [
        "mobile-abc-1.jpg",
        "mobile-abc-2.jpg",
        "mobile-abc-3.jpg",
        "mobile-abc-10.jpg",
        "mobile-abc-21.jpg",
    ]


def test_zero_padded_mobile_names_sort_in_capture_order() -> None:
    names = [f"mobile-draft-{i:02d}.jpg" for i in (5, 1, 12, 3, 10)]
    ordered = sorted(names, key=natural_photo_name_key)
    assert [n.split("-")[-1] for n in ordered] == ["01.jpg", "03.jpg", "05.jpg", "10.jpg", "12.jpg"]


def test_handles_none_and_empty() -> None:
    # Should not raise; None/empty sort together and before named files.
    ordered = sorted(["b.jpg", None, "a.jpg", ""], key=natural_photo_name_key)
    assert ordered[-2:] == ["a.jpg", "b.jpg"]
