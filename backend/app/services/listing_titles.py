"""Auction title order: category and style first, brand first only when shoppers search it.

The listing model is told this in the prompt. ``place_brand_for_search`` applies the
same rule after generation, so a house brand written at the front does not stay there.
"""

from __future__ import annotations

import re

# Makers a bidder would type. A name that is not here goes at the end of the title.
# Extend this tuple when a maker is itself the search (Louis Vuitton, Lodge, Pyrex).
SHOPPER_BRANDS: tuple[str, ...] = (
    "Louis Vuitton",
    "Gucci",
    "Chanel",
    "Hermes",
    "Prada",
    "Coach",
    "Kate Spade",
    "Tiffany",
    "Tiffany & Co",
    "Cartier",
    "Rolex",
    "Omega",
    "Ray-Ban",
    "Levi's",
    "Levi Strauss",
    "Wrangler",
    "Lee",
    "St. John's Bay",
    "Liz Claiborne",
    "Liz & Co",
    "Chico's",
    "Talbots",
    "Pendleton",
    "Carhartt",
    "Dickies",
    "Nike",
    "Adidas",
    "L.L. Bean",
    "Lands' End",
    "Gap",
    "Ralph Lauren",
    "Polo Ralph Lauren",
    "Tommy Hilfiger",
    "Columbia",
    "Patagonia",
    "The North Face",
    "Jessica Simpson",
    "Betty Boop",
    "Pyrex",
    "Corning",
    "CorningWare",
    "Fiesta",
    "Homer Laughlin",
    "Lodge",
    "Le Creuset",
    "KitchenAid",
    "Cuisinart",
    "Anchor Hocking",
    "Fire-King",
    "McCoy",
    "Roseville",
    "Lenox",
    "Noritake",
    "Mikasa",
    "Wedgwood",
    "Spode",
    "Royal Doulton",
    "Hummel",
    "Avon",
    "Pedersoli",
    "Winchester",
    "Remington",
    "Browning",
    "Colt",
    "Smith & Wesson",
    "Ruger",
    "Marlin",
    "Mossberg",
    "Savage",
    "Napier",
    "Monet",
    "Trifari",
    "Coro",
    "Sarah Coventry",
    "Miriam Haskell",
    "Swarovski",
    "Weiss",
    "Eisenberg",
    "Apple",
    "Sony",
    "Bose",
    "Nintendo",
    "Razer",
    "Samsung",
    "Logitech",
    "Canon",
    "Nikon",
    "Kodak",
    "Polaroid",
    "Pioneer",
    "Timex",
    "Seiko",
    "Citizen",
    "Bulova",
    "Hamilton",
    "Gruen",
    "Elgin",
    "Fossil",
    "Fisher-Price",
    "Hot Wheels",
    "Barbie",
    "Lego",
    "Hallmark",
    "Bradford Exchange",
    "Paparazzi",
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")
_MAKER_FIRST_TITLE_RE = re.compile(
    r"\b(?:muskets?|rifles?|pistols?|shotguns?|revolvers?|flintlocks?|handguns?|carbines?|derringers?)\b",
    re.I,
)


def _token_key(token: str) -> str:
    return re.sub(r"[^a-z0-9]", "", token.lower().replace("'", ""))


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(match.group(0), match.start(), match.end()) for match in _TOKEN_RE.finditer(text or "")]


def _brand_keys(brand: str) -> list[str]:
    return [key for key in (_token_key(token) for token, _start, _end in _tokens(brand)) if key]


def _shopper_keys() -> frozenset[str]:
    return frozenset(" ".join(_brand_keys(name)) for name in SHOPPER_BRANDS)


_SHOPPER_KEYS = _shopper_keys()


def brand_leads_search(brand: str) -> bool:
    """True when this maker is a name bidders search, so it belongs at the front."""
    keys = _brand_keys(brand)
    if not keys:
        return False
    folded = " ".join(keys)
    if folded in _SHOPPER_KEYS:
        return True
    # "Tiffany & Co" matches the listed "Tiffany"; "Levi Strauss" matches "Levi's" only as its own entry.
    return any(folded == listed or folded.startswith(listed + " ") for listed in _SHOPPER_KEYS)


def maker_stays_first(title: str) -> bool:
    """Firearms keep maker + model at the front even for a small maker."""
    return bool(_MAKER_FIRST_TITLE_RE.search(title or ""))


def _brand_token_span(title: str, brand: str) -> tuple[int, int] | None:
    keys = _brand_keys(brand)
    tokens = _tokens(title)
    if not keys or len(tokens) < len(keys):
        return None
    folded = [_token_key(token) for token, _start, _end in tokens]
    width = len(keys)
    for index in range(len(tokens) - width + 1):
        if folded[index : index + width] == keys:
            return index, index + width
    return None


def title_contains_brand(title: str, brand: str) -> bool:
    return _brand_token_span(title, brand) is not None


def _rest_after_leading_brand(title: str, brand: str) -> str | None:
    tokens = _tokens(title)
    span = _brand_token_span(title, brand)
    if span is None or span[0] != 0 or span[1] > len(tokens):
        return None
    rest = title[tokens[span[1] - 1][2] :].lstrip(" .,;:-–—")
    return rest.strip() or None


def _rest_before_trailing_brand(title: str, brand: str) -> str | None:
    tokens = _tokens(title)
    span = _brand_token_span(title, brand)
    if span is None or span[1] != len(tokens):
        return None
    head = title[: tokens[span[0]][1]].rstrip()
    head = re.sub(r"(?i)[,\s]+by$", "", head).rstrip(" ,;:-–—")
    return head.strip() or None


def place_brand_for_search(title: str, brand: str) -> str:
    """Category and style first. A searched maker leads; any other maker ends the title.

    A brand already in the middle of the title is left where the model put it.
    A title that is only the brand is left unchanged.
    """
    text = (title or "").strip()
    maker = (brand or "").strip()
    if not text or not maker or not title_contains_brand(text, maker):
        return text
    leads = brand_leads_search(maker) or maker_stays_first(text)
    if leads:
        if _rest_after_leading_brand(text, maker) is not None:
            return text
        head = _rest_before_trailing_brand(text, maker)
        if head and not title_contains_brand(head, maker):
            return f"{maker} {head}"
        return text
    rest = _rest_after_leading_brand(text, maker)
    if rest and not title_contains_brand(rest, maker):
        return f"{rest}, {maker}"
    return text


def auction_title_order_clause() -> str:
    """Shared listing-author rule for where the maker sits in the title."""
    return (
        "Title for search: put the item category and the look first "
        "(style, material, or form, then the object). "
        "Put a house brand at the end, as ', Brand' or 'by Brand'. "
        "Good: 'Primitive Wrought Iron Double-Arm Table Lamp, Gatherings Inc.' "
        "Lead with the brand only when that name is what a bidder would type "
        "(Louis Vuitton, Tiffany, Levi's, Pyrex, Lodge, Napier, Pedersoli, St. John's Bay, and names of that kind). "
        "Good: 'Louis Vuitton Monogram Shoulder Bag', 'Pedersoli Brown Bess Flintlock Musket', "
        "'St. John's Bay Cotton-Blend Zip-Up Jacket'. "
        "Firearms still lead with maker and model. Do not open with a house brand nobody searches, "
        "and do not add the filler word Style."
    )


def auction_title_schema_hint() -> str:
    """Short field hint. The full examples live in the system prompt."""
    return (
        "Auction title, title case, visible subjects only, no 'lot'. "
        "Category and style before the object. Maker at the end unless bidders search that brand "
        "(Louis Vuitton, Tiffany, Levi's, Pyrex, Lodge, Napier, Pedersoli)."
    )
