"""Hero background prompt, aspect, and image-part parsing. No network."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.hero_background import (
    closest_aspect_ratio,
    hero_background_prompt,
    image_bytes_from_response,
)
from app.services.plain_background import first_website_photo


def test_first_website_photo_is_the_first_listing_frame() -> None:
    photos = [
        SimpleNamespace(original_name="tape.jpg", photo_role="tape", shows_measuring_scale=False),
        SimpleNamespace(original_name="first.jpg", photo_role="item_front", shows_measuring_scale=False),
        SimpleNamespace(original_name="later-hero.jpg", photo_role="hero", shows_measuring_scale=False),
        SimpleNamespace(original_name="measure.jpg", photo_role="angle_only", shows_measuring_scale=True),
    ]
    picked = first_website_photo(photos)
    assert picked is not None and picked.original_name == "first.jpg"
    assert first_website_photo([]) is None


def test_closest_aspect_keeps_orientation() -> None:
    assert closest_aspect_ratio(1600, 900) == "16:9"
    assert closest_aspect_ratio(600, 800) == "3:4"
    assert closest_aspect_ratio(0, 10) == "1:1"


def test_prompt_names_the_color_and_forbids_a_new_room() -> None:
    prompt = hero_background_prompt("gray")
    assert "light gray" in prompt
    assert "lifestyle setting" in prompt
    assert "same wear" in prompt
    try:
        hero_background_prompt("navy")
    except ValueError:
        pass
    else:
        raise AssertionError("expected an unknown color to fail")


def test_image_bytes_from_response_uses_the_last_image() -> None:
    response = SimpleNamespace(
        parts=[
            SimpleNamespace(inline_data=SimpleNamespace(data=b"first")),
            SimpleNamespace(inline_data=None),
            SimpleNamespace(inline_data=SimpleNamespace(data=b"final")),
        ]
    )
    assert image_bytes_from_response(response) == b"final"
    empty = SimpleNamespace(parts=[], candidates=[])
    assert image_bytes_from_response(empty) is None
