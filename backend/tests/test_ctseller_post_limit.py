from pydantic import ValidationError
import pytest

from app.schemas import SalePostToCTSellerIn


def test_sale_post_limit_queues_many_lots() -> None:
    body = SalePostToCTSellerIn()
    assert body.limit == 100
    ok = SalePostToCTSellerIn(limit=100)
    assert ok.limit == 100


def test_sale_post_limit_rejects_oversized_queue() -> None:
    with pytest.raises(ValidationError):
        SalePostToCTSellerIn(limit=500)
