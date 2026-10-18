"""CT Bids listing URLs used in captions and the Push calendar.

Item pages are ``https://ctbids.com/estate-sale/{sale_id}/item/{item_id}``.
The sale index is ``https://ctbids.com/estate-sale/{sale_id}`` (Watcher
``auction_url`` often includes a trailing slash). CT Bids currently has no
working back-link from an item to the sale, so posts include both.
"""

from __future__ import annotations

CTBIDS_SALE_URL = "https://ctbids.com/estate-sale/{sale_id}"
CTBIDS_ITEM_URL = "https://ctbids.com/estate-sale/{sale_id}/item/{item_id}"
SALE_LINK_PREFIX = "See the whole sale: "


def normalize_ctbids_sale_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "/item/" in raw:
        raw = raw.split("/item/", 1)[0]
    return raw.rstrip("/")


def ctbids_sale_url(
    *,
    sale_id: int | None = None,
    auction_url: str = "",
    item_url: str = "",
) -> str:
    from_auction = normalize_ctbids_sale_url(auction_url)
    if from_auction:
        return from_auction
    from_item = normalize_ctbids_sale_url(item_url)
    if from_item:
        return from_item
    if sale_id in (None, ""):
        return ""
    try:
        return CTBIDS_SALE_URL.format(sale_id=int(sale_id))
    except (TypeError, ValueError):
        return ""


def sale_link_line(sale_url: str) -> str:
    url = normalize_ctbids_sale_url(sale_url)
    if not url:
        return ""
    return f"{SALE_LINK_PREFIX}{url}"
