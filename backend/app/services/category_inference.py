from __future__ import annotations

import re
from typing import Iterable

from app.core.lotting import LotOutput

# Order matters for ties: more specific CT Seller categories first; Household Goods Other last.
# Names must match values CT Seller accepts (see extension CTSELLER_ALLOWED_CATEGORIES).
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Handbags & Wallets", ("handbag", "purse", "wallet", "crossbody", "tote", "satchel", "clutch")),
    ("Shoes", ("shoe", "heel", "loafer", "sneaker", "boot", "sandals", "rockport", "cole haan")),
    ("Women's", ("women", "dress", "blouse", "sweater", "cardigan", "skirt", "knit")),
    ("Men's", ("men", "mens", "necktie", "tie", "fedora", "hat", "akubra")),
    ("Earrings", ("earring", "stud earring", "hoop earring")),
    ("Bracelets", ("bracelet", "bangle", "cuff bracelet", "figaro")),
    ("Rings", ("ring", "band ring", "engagement ring")),
    ("Jewelry Other", ("jewelry", "brooch", "necklace", "pendant", "tie clip", "cufflink")),
    ("Costume Jewelry", ("rhinestone", "costume jewelry", "clip-on")),
    ("Luxury & Designer Items", ("louis vuitton", "gucci", "prada", "michael kors", "coach", "designer")),
    ("Dolls", ("doll", "barbie", "matryoshka")),
    ("Figures & Figurines", ("figurine", "figure", "disney", "collectible figure")),
    (
        "Art",
        (
            "colored pencil",
            "colour pencil",
            "prismacolor",
            "sketchpad",
            "sketch pad",
            "sketchbook",
            "drawing pencil",
            "watercolor pencil",
            "art supplies",
            "art supply",
            "moleskine",
            "charcoal pencil",
            "pastel pencil",
        ),
    ),
    ("Paintings", ("painting", "canvas", "acrylic", "oil on", "watercolor", "batik", "signed art")),
    ("Photography", ("camera", "photography", "lens", "nikon", "canon")),
    ("Clocks", ("clock", "timepiece", "mantel clock", "wall clock")),
    ("Coins", ("coin", "coins", "peso", "mint", "bullion", "numismatic")),
    (
        "Crystal",
        (
            "cut crystal",
            "lead crystal",
            "crystal glass",
            "crystal stemware",
            "crystal vase",
            "waterford",
            "baccarat",
        ),
    ),
    (
        "Glassware",
        (
            "glassware",
            "art glass",
            "glass vase",
            "stained glass",
            "pressed glass",
            "depression glass",
            "stemware",
            "wine glass",
            "champagne flute",
            "highball",
            "tumbler",
            "goblet",
            "decanter",
            "pitcher glass",
            "drinking glass",
            "barware",
            "crystal",
            "glass plate",
            "glass bowl",
            "glass dish",
        ),
    ),
    (
        "Dining Room",
        (
            "dining",
            "china set",
            "dinnerware",
            "place setting",
            "tableware",
            "serving platter",
            "charger plate",
            "salad plate",
            "soup bowl",
            "teacup",
            "saucer",
            "flatware",
            "silverware",
            "portmeirion",
            "spode",
            "wedgwood",
            "lenox",
            "noritake",
            "mikasa",
            "fitz and floyd",
            "botanic garden",
            "pomona",
        ),
    ),
    (
        "Kitchen & Home",
        (
            "kitchen",
            "cookware",
            "bakeware",
            "baking dish",
            "casserole",
            "ramekin",
            "pie dish",
            "roasting pan",
            "dutch oven",
            "dish",
            "plate",
            "platter",
            "bowl",
            "mug",
            "teacup",
            "teapot",
            "coffee mug",
            "ceramic",
            "porcelain",
            "stoneware",
            "earthenware",
            "china",
            "pottery",
            "oven-safe",
            "oven safe",
            "vase",
            "portmeirion",
            "pyrex",
            "corningware",
            "fiestaware",
        ),
    ),
    ("Bedroom Goods", ("bedding", "linen", "bedspread", "comforter", "sheet", "pillowcase")),
    ("Music Other", ("speaker", "stereo", "record player", "turntable", "audio", "music")),
    ("Art Other", ("framed art", "wall art", "print", "decor art", "fine art")),
    ("Decorative Collectibles", ("collectible", "decorative collectible", "display piece", "novelty")),
    ("Decor", ("home decor", "wall decor", "shelf decor", "centerpiece")),
    ("Antiques Other", ("antique", "heirloom", "collectible antique")),
    ("Accessories", ("belt", "scarf", "hat", "accessory")),
    # Catch-all only — keep keywords narrow so specific categories win.
    ("Household Goods Other", ("household", "home goods", "furniture", "lamp", "nightstand", "dresser")),
]

# Prefer these over Household Goods Other when scores are close.
_SPECIFIC_OVER_HOUSEHOLD = frozenset(
    {
        "Glassware",
        "Crystal",
        "Dining Room",
        "Kitchen & Home",
        "Decor",
        "Decorative Collectibles",
        "Art",
        "Art Other",
        "Paintings",
        "Figures & Figurines",
    }
)


def ct_category_names() -> list[str]:
    """CT Seller top-level categories used for appraiser structured output."""
    return [name for name, _ in _RULES]


def _text_parts(lot: LotOutput) -> Iterable[str]:
    yield lot.auction_title or ""
    yield lot.professional_description or ""
    yield lot.condition or ""
    yield " ".join(lot.specification_lines or [])
    yield " ".join(lot.brands or [])
    yield " ".join(lot.labels_and_tags or [])


def infer_ct_category(lot: LotOutput) -> tuple[str | None, str | None]:
    hay = " ".join(_text_parts(lot)).lower()
    hay = re.sub(r"\s+", " ", hay).strip()
    scored: list[tuple[str, int]] = []
    for category, keywords in _RULES:
        score = 0
        for kw in keywords:
            # Token-aware matching so short keywords do not match inside unrelated words
            # (e.g. "tie" should not match "light").
            if re.search(r"\b" + re.escape(kw) + r"\b", hay):
                score += len(kw)
        if score > 0:
            scored.append((category, score))
    if not scored:
        return None, None
    scored.sort(key=lambda x: x[1], reverse=True)
    best_category, best_score = scored[0]
    if best_score < 6:
        return None, None
    # If Household Goods Other barely wins, prefer a specific kitchen/glass/dining hit.
    if best_category == "Household Goods Other" and len(scored) > 1:
        for category, score in scored[1:]:
            if category in _SPECIFIC_OVER_HOUSEHOLD and score >= 5:
                return category, None
    return best_category, None
