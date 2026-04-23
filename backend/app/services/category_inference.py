from __future__ import annotations

import re
from typing import Iterable

from app.core.lotting import LotOutput

_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Handbags & Wallets", ("handbag", "purse", "wallet", "crossbody", "tote", "satchel", "clutch")),
    ("Shoes", ("shoe", "heel", "loafer", "sneaker", "boot", "sandals", "rockport", "cole haan")),
    ("Women's", ("women", "dress", "blouse", "sweater", "cardigan", "skirt", "knit")),
    ("Men's", ("men", "mens", "necktie", "tie", "fedora", "hat", "akubra")),
    ("Earrings", ("earring", "stud earring", "hoop earring")),
    ("Bracelets", ("bracelet", "bangle", "cuff bracelet", "figaro")),
    ("Rings", ("ring", "band ring", "engagement ring")),
    ("Jewelry Other", ("jewelry", "brooch", "necklace", "pendant", "tie clip", "cufflink")),
    ("Luxury & Designer Items", ("louis vuitton", "gucci", "prada", "michael kors", "coach", "designer")),
    ("Dolls", ("doll", "barbie", "matryoshka")),
    ("Figures & Figurines", ("figurine", "figure", "disney", "collectible figure")),
    ("Paintings", ("painting", "canvas", "acrylic", "oil on", "watercolor", "batik", "signed art")),
    ("Photography", ("camera", "photography", "lens", "nikon", "canon")),
    ("Clocks", ("clock", "timepiece", "mantel clock", "wall clock")),
    ("Kitchen & Home", ("kitchen", "cookware", "dish", "plate", "glassware", "mug", "vase")),
    ("Bedroom Goods", ("bedding", "linen", "bedspread", "comforter", "sheet", "pillowcase")),
    ("Music Other", ("speaker", "stereo", "record player", "turntable", "audio", "music")),
    ("Art Other", ("art", "framed art", "wall art", "print", "decor art")),
    ("Antiques Other", ("antique", "vintage", "heirloom", "collectible antique")),
    ("Accessories", ("belt", "scarf", "hat", "accessory")),
    ("Household Goods Other", ("household", "home decor", "decor", "furniture", "lamp", "ceramic")),
]


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
    best_category = None
    best_score = 0
    for category, keywords in _RULES:
        score = 0
        for kw in keywords:
            if kw in hay:
                score += len(kw)
        if score > best_score:
            best_category = category
            best_score = score
    if best_score < 6:
        return None, None
    return best_category, None

