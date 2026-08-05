"""Tests for Facebook Marketplace inventory PDF export."""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.facebook_inventory_pdf import build_facebook_inventory_pdf


def test_build_facebook_inventory_pdf_empty() -> None:
    pdf = build_facebook_inventory_pdf(
        [],
        sale_name="Aunt's Estate",
        generated_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
    )
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 200


def test_build_facebook_inventory_pdf_with_lots() -> None:
    lots = [
        SimpleNamespace(
            auction_title="Oak dresser",
            starting_price=125.0,
            condition_text="Good",
            professional_description="Solid oak six-drawer dresser.",
            photo_count=4,
        ),
        SimpleNamespace(
            auction_title="Mid-century chair <accent>",
            starting_price=None,
            condition_text="Fair & worn",
            professional_description="A" * 400,
            photo_count=2,
        ),
    ]
    pdf = build_facebook_inventory_pdf(
        lots,  # type: ignore[arg-type]
        sale_name="Aunt's Estate",
        generated_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
    )
    assert pdf[:4] == b"%PDF"
    assert b"%%EOF" in pdf[-32:] or pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 800
