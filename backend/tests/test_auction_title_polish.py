"""Jewelry and mixed-lot auction title polish — no live model calls."""

from __future__ import annotations

from app.core.lotting import (
    LotOutput,
    polish_auction_title,
    prefer_liz_and_co_over_tiffany,
    scrub_padded_auction_title,
)


def test_scrub_drops_collection_statement_ft_and_moves_count() -> None:
    assert scrub_padded_auction_title("Rhinestone & Crystal Statement Jewelry Collection") == (
        "Rhinestone & Crystal Jewelry"
    )
    assert scrub_padded_auction_title(
        "Gold-Tone Statement Necklaces and Knot Earrings Collection"
    ) == "Gold-Tone Necklaces and Knot Earrings"
    assert scrub_padded_auction_title("Three Fashion Necklaces Ft. a 1976 US Penny Design") == (
        "Necklaces with a 1976 US Penny Design (3)"
    )
    assert scrub_padded_auction_title("Paparazzi and Assorted Beaded Costume Necklaces") == (
        "Paparazzi and Beaded Costume Necklaces"
    )


def test_scrub_keeps_leading_assorted_on_findings_lots() -> None:
    assert scrub_padded_auction_title(
        "Assorted jewelry making findings, chains, and craft hardware"
    ) == "Assorted jewelry making findings, chains, and craft hardware"


def test_scrub_keeps_two_tone_compound() -> None:
    assert "Two-Tone" in scrub_padded_auction_title("Gold-Tone Faceted Bead and Two-Tone Stretch Bracelets")


def test_liz_and_co_wins_over_tiffany_when_label_says_liz() -> None:
    title, brands = prefer_liz_and_co_over_tiffany(
        "Tiffany & Co. Brushed Link Necklace and Hoop Earrings Set",
        ["Tiffany & Co"],
        labels_and_tags=["LIZ & CO"],
    )
    assert title.startswith("Liz & Co")
    assert "Tiffany" not in title
    assert brands[0] == "Liz & Co"


def test_tiffany_stays_when_no_liz_evidence() -> None:
    title, brands = prefer_liz_and_co_over_tiffany(
        "Tiffany & Co. Sterling Heart Tag Necklace",
        ["Tiffany & Co"],
        labels_and_tags=["TIFFANY & CO STERLING"],
    )
    assert "Tiffany" in title
    assert brands == ["Tiffany & Co"]


def test_polish_and_finalize_on_jewelry_examples() -> None:
    titled, _brands = polish_auction_title("Monet Gold-Tone Layered Tassel Necklaces (3)", ["Monet"])
    assert titled == "Monet Gold-Tone Layered Tassel Necklaces (3)"

    lot = LotOutput.from_model(
        {
            "item_name": "Tiffany & Co. Brushed Link Necklace and Hoop Earrings Set",
            "brand": "Tiffany & Co",
            "labels_and_tags": ["Liz & Co"],
            "professional_description": "A striking statement of modern design.",
            "specification_lines": ["Item 1: Brushed link necklace", "Item 2: Hoop earrings"],
        }
    )
    assert lot.auction_title.startswith("Liz & Co")
    assert "Tiffany" not in lot.auction_title
    assert "Liz & Co" in lot.brands
    assert "Collection" not in lot.auction_title
    assert "Statement" not in lot.auction_title
