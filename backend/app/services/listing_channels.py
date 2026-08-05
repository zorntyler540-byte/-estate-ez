"""Catalog listing channels (CT Seller vs Facebook Marketplace)."""

from __future__ import annotations

LISTING_CHANNEL_CTSELLER = "ctseller"
LISTING_CHANNEL_FACEBOOK = "facebook"

VALID_LISTING_CHANNELS = frozenset({LISTING_CHANNEL_CTSELLER, LISTING_CHANNEL_FACEBOOK})


def normalize_listing_channel(raw: object | None) -> str:
    """Return a known listing channel; default CT Seller."""
    channel = str(raw or "").strip().lower()
    if channel in VALID_LISTING_CHANNELS:
        return channel
    return LISTING_CHANNEL_CTSELLER


def is_facebook_listing_channel(raw: object | None) -> bool:
    return normalize_listing_channel(raw) == LISTING_CHANNEL_FACEBOOK
