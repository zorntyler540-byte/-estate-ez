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


def _meta(
    name: str,
    *,
    role: str | None,
    sort_order: int,
    item: str | None = None,
    primary: bool = False,
    whole: bool = False,
    measure: bool = False,
    label: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        original_name=name,
        photo_role=role,
        sort_order=sort_order,
        distinct_item_id=item,
        is_primary_front=primary,
        shows_whole_lot=whole,
        shows_measuring_scale=measure,
        label_legibility=label,
    )


def test_listing_order_chapters_match_hand_sorted_figurine_lot() -> None:
    """Capture order mixed; listing order is group → fish → eagle → seal → sticker → measure."""
    photos = [
        _meta("01-group.jpg", role="hero", sort_order=0, whole=True),
        _meta("02-eagle-detail.jpg", role="angle_only", sort_order=1, item="EAGLE"),
        _meta("03-sticker.jpg", role="angle_only", sort_order=2, item="SEAL", label="clear"),
        _meta("04-fish-wrong-group.jpg", role="item_front", sort_order=3, item="FISH", primary=True),
        _meta("05-group-b.jpg", role="hero", sort_order=4, whole=True),
        _meta("06-fish-side.jpg", role="angle_only", sort_order=5, item="FISH"),
        _meta("07-eagle-front.jpg", role="item_front", sort_order=6, item="EAGLE", primary=True),
        _meta("08-seal-front.jpg", role="item_front", sort_order=7, item="SEAL", primary=True),
        _meta("09-seal-angle.jpg", role="angle_only", sort_order=8, item="SEAL"),
        _meta("10-measure-eagle.jpg", role="angle_only", sort_order=9, item="EAGLE", measure=True),
        _meta("11-measure-fish.jpg", role="angle_only", sort_order=10, item="FISH", measure=True),
        _meta("12-sku-tape.jpg", role="tape", sort_order=11),
    ]
    ordered = ordered_photos_for_listing(photos)
    assert [p.original_name for p in ordered] == [
        "01-group.jpg",
        "05-group-b.jpg",
        "04-fish-wrong-group.jpg",
        "06-fish-side.jpg",
        "07-eagle-front.jpg",
        "02-eagle-detail.jpg",
        "08-seal-front.jpg",
        "09-seal-angle.jpg",
        "03-sticker.jpg",
        "10-measure-eagle.jpg",
        "11-measure-fish.jpg",
        "12-sku-tape.jpg",
    ]


def test_listing_order_without_meta_keeps_role_buckets() -> None:
    photos = [
        _p("mobile-draft-01.jpg", "item_front", 0),
        _p("mobile-draft-02.jpg", "tape", 1),
        _p("mobile-draft-03.jpg", "hero", 2),
    ]
    ordered = ordered_photos_for_listing(photos)
    assert [p.original_name for p in ordered] == [
        "mobile-draft-03.jpg",
        "mobile-draft-01.jpg",
        "mobile-draft-02.jpg",
    ]
