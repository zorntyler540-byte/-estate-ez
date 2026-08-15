"""Listing-order sort: group/hero, then items, then details, tape last."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.photo_order import LISTING_ROLE_RANK, ordered_photos_for_listing


def _p(name: str, role: str | None, sort_order: int) -> SimpleNamespace:
    return SimpleNamespace(original_name=name, photo_role=role, sort_order=sort_order)


def test_listing_order_groups_roles_and_keeps_capture_within_role() -> None:
    # Capture order mixed the way employees often shoot: item, tape, group, detail, item…
    photos = [
        _p("mobile-draft-01.jpg", "item_front", 0),
        _p("mobile-draft-02.jpg", "tape", 1),
        _p("mobile-draft-03.jpg", "hero", 2),
        _p("mobile-draft-04.jpg", "angle_only", 3),
        _p("mobile-draft-05.jpg", "item_front", 4),
        _p("mobile-draft-06.jpg", "angle_only", 5),
        _p("mobile-draft-07.jpg", None, 6),
        _p("mobile-draft-08.jpg", "unusable", 7),
    ]
    ordered = ordered_photos_for_listing(photos)
    assert [p.original_name for p in ordered] == [
        "mobile-draft-03.jpg",  # hero
        "mobile-draft-01.jpg",  # item_front (earlier capture)
        "mobile-draft-05.jpg",  # item_front
        "mobile-draft-04.jpg",  # angle_only
        "mobile-draft-06.jpg",  # angle_only
        "mobile-draft-07.jpg",  # unknown
        "mobile-draft-08.jpg",  # unusable
        "mobile-draft-02.jpg",  # tape last
    ]


def test_listing_order_unknown_role_ranks_with_none() -> None:
    photos = [
        _p("z.jpg", "not_a_role", 0),
        _p("a.jpg", "hero", 1),
        _p("b.jpg", None, 2),
    ]
    ordered = ordered_photos_for_listing(photos)
    assert [p.original_name for p in ordered] == ["a.jpg", "z.jpg", "b.jpg"]


def test_listing_order_is_stable_when_already_sorted() -> None:
    photos = [
        _p("hero.jpg", "hero", 0),
        _p("front.jpg", "item_front", 1),
        _p("detail.jpg", "angle_only", 2),
        _p("tape.jpg", "tape", 3),
    ]
    first = ordered_photos_for_listing(photos)
    # Simulate persisted sort_order after first pass.
    for i, p in enumerate(first):
        p.sort_order = i
    second = ordered_photos_for_listing(first)
    assert [p.original_name for p in second] == [p.original_name for p in first]


def test_listing_order_no_roles_keeps_current_sort() -> None:
    photos = [
        _p("1C86C230-A1C1-4433-B64E-5C2957E79027.jpg", None, 2),
        _p("AB297EBB-014B-41F0-BB6D-AC056D05C2CD.jpg", None, 0),
        _p("F619ADAC-9489-46DA-AFF8-F4A6CEFB61CC.jpg", None, 1),
    ]
    ordered = ordered_photos_for_listing(photos)
    assert [p.sort_order for p in ordered] == [0, 1, 2]


def test_role_rank_covers_all_catalog_roles() -> None:
    from app.services.photo_roles import CATALOG_PHOTO_ROLES

    assert CATALOG_PHOTO_ROLES <= set(LISTING_ROLE_RANK) - {None}
    assert LISTING_ROLE_RANK["hero"] < LISTING_ROLE_RANK["item_front"]
    assert LISTING_ROLE_RANK["item_front"] < LISTING_ROLE_RANK["angle_only"]
    assert LISTING_ROLE_RANK["angle_only"] < LISTING_ROLE_RANK[None]
    assert LISTING_ROLE_RANK[None] < LISTING_ROLE_RANK["unusable"]
    assert LISTING_ROLE_RANK["unusable"] < LISTING_ROLE_RANK["tape"]
