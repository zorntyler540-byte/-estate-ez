"""CT Bids search tags for seller.ctbids.com item ``tag`` (not apparel size tags)."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

MAX_SEARCH_TAGS = 12
MAX_TAG_CHARS = 40
MAX_JOINED_CHARS = 400

# Words that never help a shopper find this piece.
_STOP = frozenset(
    {
        "a",
        "an",
        "and",
        "the",
        "of",
        "or",
        "for",
        "to",
        "in",
        "on",
        "with",
        "set",
        "pair",
        "lot",
        "item",
        "items",
        "estate",
        "grouping",
        "other",
        "assorted",
        "nice",
        "great",
        "beautiful",
        "handsome",
        "lovely",
        "good",
        "very",
        "like",
        "new",
        "used",
        "condition",
        "this",
        "that",
        "from",
        "into",
        "plus",
        "etc",
    }
)

# Luxury / celebrity bait some sellers stuff into tags. Keep only when the listing itself names them.
_SPAM_UNLESS_EVIDENCED = frozenset(
    {
        "rolex",
        "porsche",
        "bentley",
        "mercedes",
        "ferrari",
        "lamborghini",
        "sothebys",
        "sotheby's",
        "christies",
        "christie's",
        "neimanmarcus",
        "neiman marcus",
        "baccarat",
        "trump",
        "queen",
        "king",
        "royalfamily",
        "royal family",
        "louisvuitton",
        "louis vuitton",
        "gucci",
        "chanel",
        "hermes",
        "hermès",
        "prada",
        "dior",
        "fendi",
        "cartier",
        "tiffany",
        "rolexoyster",
        "croton",
    }
)

_SPEC_LABELS = (
    "brand",
    "material",
    "era",
    "model",
    "pattern",
    "maker",
    "style",
)

_SYNONYMS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"\bmid[-\s]?century\b", re.I), ("MCM", "mid century")),
    (re.compile(r"\bmcm\b", re.I), ("mid century", "MCM")),
    (re.compile(r"\bart deco\b", re.I), ("Art Deco",)),
    (re.compile(r"\bart nouveau\b", re.I), ("Art Nouveau",)),
    (re.compile(r"\bpyrex\b", re.I), ("Pyrex", "glass")),
    (re.compile(r"\bjadeite\b", re.I), ("jadeite", "Fire-King")),
    (re.compile(r"\bfire[-\s]?king\b", re.I), ("Fire-King", "jadeite")),
    (re.compile(r"\bsterling\b", re.I), ("sterling silver",)),
    (re.compile(r"\bcast iron\b", re.I), ("cast iron",)),
    (re.compile(r"\bteak\b", re.I), ("teak", "mid century")),
    (re.compile(r"\bdepression glass\b", re.I), ("depression glass",)),
)

_SKU_LIKE = re.compile(r"^(?:#?\d{2,}[a-z0-9]*|[a-z]{1,3}\d{3,}|\d{3,}[a-z]{1,3})$", re.I)
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.&'/-]{0,38}")


def _norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _title_tag(s: str) -> str:
    raw = re.sub(r"\s+", " ", (s or "").strip())
    if not raw:
        return ""
    if raw.isupper() and len(raw) <= 6:
        return raw
    if raw.upper() in {"MCM", "TV", "DVD", "USB", "LED"}:
        return raw.upper()
    return raw


def _evidence_blob(*parts: Any) -> str:
    chunks: list[str] = []
    for p in parts:
        if p is None:
            continue
        if isinstance(p, (list, tuple)):
            chunks.extend(str(x) for x in p if x)
        else:
            chunks.append(str(p))
    return " ".join(chunks).lower()


def _is_spam(tag: str, evidence: str) -> bool:
    key = _norm_key(tag)
    if not key:
        return False
    for banned in _SPAM_UNLESS_EVIDENCED:
        bkey = _norm_key(banned)
        if key == bkey or bkey in key:
            return _norm_key(banned) not in _norm_key(evidence)
    return False


def sanitize_search_tags(
    tags: Iterable[str] | None,
    *,
    evidence: str = "",
    max_tags: int = MAX_SEARCH_TAGS,
) -> list[str]:
    """Keep honest, searchable tags. Drop SKU codes and unaffiliated luxury bait."""
    out: list[str] = []
    seen: set[str] = set()
    ev = (evidence or "").lower()
    for raw in tags or []:
        t = _title_tag(str(raw or "").replace(",", " "))
        if not t or len(t) > MAX_TAG_CHARS:
            continue
        key = _norm_key(t)
        if not key or key in seen:
            continue
        if t.lower() in _STOP:
            continue
        if _SKU_LIKE.match(t.replace(" ", "")):
            continue
        if _is_spam(t, ev):
            continue
        words = [w for w in re.split(r"\s+", t.lower()) if w]
        if words and all(w in _STOP for w in words):
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= max_tags:
            break
    return out


def _spec_value(lines: Iterable[str] | None, label: str) -> str:
    prefix = f"{label}:"
    for line in lines or []:
        s = str(line or "").strip()
        if s.lower().startswith(prefix):
            return s.split(":", 1)[-1].strip()
    return ""


def _title_phrases(title: str) -> list[str]:
    """Keep distinctive 1–3 word chunks from the auction title."""
    words = _WORD.findall(title or "")
    kept: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        if w.lower() in _STOP:
            i += 1
            continue
        # Prefer two-word proper-ish phrases (Herman Miller, George Nelson).
        if i + 1 < len(words) and words[i + 1].lower() not in _STOP:
            pair = f"{w} {words[i + 1]}"
            if not _SKU_LIKE.match(_norm_key(pair)):
                kept.append(pair)
                i += 2
                continue
        kept.append(w)
        i += 1
    return kept[:8]


def suggest_search_tags(
    *,
    title: str = "",
    description: str = "",
    brands: Iterable[str] | None = None,
    specification_lines: Iterable[str] | None = None,
    labels_and_tags: Iterable[str] | None = None,
    category: str | None = None,
    extra: Iterable[str] | None = None,
) -> list[str]:
    """Build tags from listing fields when AI omitted them or staff clicks Fill."""
    specs = list(specification_lines or [])
    brand_list = [str(b).strip() for b in (brands or []) if str(b).strip()]
    candidates: list[str] = []
    candidates.extend(brand_list)
    candidates.extend(str(x).strip() for x in (extra or []) if str(x).strip())
    for label in _SPEC_LABELS:
        val = _spec_value(specs, label)
        if val:
            candidates.append(val)
    cat = (category or "").strip()
    if cat and "other" not in cat.lower():
        leaf = cat.split("#*#")[-1].strip()
        if leaf and "other" not in leaf.lower():
            candidates.append(leaf)
    blob = _evidence_blob(title, description, brand_list, specs, labels_and_tags, category)
    for pat, syns in _SYNONYMS:
        if pat.search(blob):
            candidates.extend(syns)
    candidates.extend(_title_phrases(title))
    return sanitize_search_tags(candidates, evidence=blob)


def merge_search_tags(
    proposed: Iterable[str] | None,
    *,
    title: str = "",
    description: str = "",
    brands: Iterable[str] | None = None,
    specification_lines: Iterable[str] | None = None,
    labels_and_tags: Iterable[str] | None = None,
    category: str | None = None,
) -> list[str]:
    """Prefer model tags, then fill from the listing so every lot has usable search words."""
    evidence = _evidence_blob(title, description, brands, specification_lines, labels_and_tags, category)
    cleaned = sanitize_search_tags(proposed, evidence=evidence)
    if len(cleaned) >= 4:
        return cleaned[:MAX_SEARCH_TAGS]
    filled = suggest_search_tags(
        title=title,
        description=description,
        brands=brands,
        specification_lines=specification_lines,
        labels_and_tags=labels_and_tags,
        category=category,
        extra=cleaned,
    )
    return filled or cleaned


def format_ctseller_tag_field(tags: Iterable[str] | None, *, evidence: str = "") -> str:
    """Comma-separated string CT Seller stores on ``item.tag``."""
    cleaned = sanitize_search_tags(tags, evidence=evidence)
    joined = ",".join(cleaned)
    if len(joined) <= MAX_JOINED_CHARS:
        return joined
    kept: list[str] = []
    for t in cleaned:
        trial = ",".join(kept + [t])
        if len(trial) > MAX_JOINED_CHARS:
            break
        kept.append(t)
    return ",".join(kept)


def parse_search_tags_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return sanitize_search_tags(str(x) for x in data)


def search_tags_for_lot_fields(
    *,
    stored: Iterable[str] | None,
    title: str = "",
    description: str = "",
    brands: Iterable[str] | None = None,
    specification_lines: Iterable[str] | None = None,
    labels_and_tags: Iterable[str] | None = None,
    category: str | None = None,
) -> list[str]:
    stored_list = [str(x).strip() for x in (stored or []) if str(x).strip()]
    if stored_list:
        evidence = _evidence_blob(title, description, brands, specification_lines, labels_and_tags, category)
        cleaned = sanitize_search_tags(stored_list, evidence=evidence)
        if cleaned:
            return cleaned
    return suggest_search_tags(
        title=title,
        description=description,
        brands=brands,
        specification_lines=specification_lines,
        labels_and_tags=labels_and_tags,
        category=category,
    )
