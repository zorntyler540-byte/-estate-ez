"""Tests for listing_channel helpers (CT Seller vs Facebook)."""

from __future__ import annotations

from app.services.listing_channels import (
    LISTING_CHANNEL_CTSELLER,
    LISTING_CHANNEL_FACEBOOK,
    is_facebook_listing_channel,
    normalize_listing_channel,
)


def test_normalize_listing_channel() -> None:
    assert normalize_listing_channel("facebook") == LISTING_CHANNEL_FACEBOOK
    assert normalize_listing_channel("FACEBOOK") == LISTING_CHANNEL_FACEBOOK
    assert normalize_listing_channel("ctseller") == LISTING_CHANNEL_CTSELLER
    assert normalize_listing_channel(None) == LISTING_CHANNEL_CTSELLER
    assert normalize_listing_channel("nope") == LISTING_CHANNEL_CTSELLER


def test_is_facebook_listing_channel() -> None:
    assert is_facebook_listing_channel("facebook") is True
    assert is_facebook_listing_channel("ctseller") is False
    assert is_facebook_listing_channel(None) is False
