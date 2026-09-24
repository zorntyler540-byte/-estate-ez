from pydantic import ValidationError
import pytest

from app.schemas import SalePostToCTSellerIn


def test_sale_post_limit_defaults_to_one_lot() -> None:
    body = SalePostToCTSellerIn()
    assert body.limit == 1


def test_sale_post_limit_rejects_old_ten_lot_batches() -> None:
    with pytest.raises(ValidationError):
        SalePostToCTSellerIn(limit=10)
    with pytest.raises(ValidationError):
        SalePostToCTSellerIn(limit=50)
    ok = SalePostToCTSellerIn(limit=5)
    assert ok.limit == 5
