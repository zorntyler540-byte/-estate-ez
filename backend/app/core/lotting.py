from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

_MENS_IN_TITLE_RE = re.compile(r"\bmen(?:'|’)?s\b", re.I)
_WOMENS_IN_TITLE_RE = re.compile(r"\bwomen(?:'|’)?s\b", re.I)
_MENS_EVIDENCE_RE = re.compile(r"\b(men(?:'|’)?s|male|boy(?:'|’)?s|boys)\b", re.I)
_WOMENS_EVIDENCE_RE = re.compile(r"\b(women(?:'|’)?s|female|ladies|girl(?:'|’)?s|girls)\b", re.I)
_UNBRANDED_RE = re.compile(
    r"^(?:unmarked|unbranded|unknown|unidentified|not\s+identified|unknown\s+maker|none|n/?a|null)$",
    re.I,
)


def as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if x is not None and str(x).strip()]
    s = str(value).strip()
    return [s] if s else []


def _placeholder_value(value: str) -> bool:
    token = str(value or "").strip().strip(".,;:!?- ").lower()
    return bool(token) and _UNBRANDED_RE.fullmatch(token) is not None


def _line_has_placeholder_value(line: str) -> bool:
    s = clean_listing_text(line)
    if not s:
        return True
    if ":" in s:
        rhs = s.split(":", 1)[1].strip()
        if rhs and _placeholder_value(rhs):
            return True
    return _placeholder_value(s)


def clean_listing_text(raw: str) -> str:
    """
    Normalize model-generated prose into plain listing text:
    - strip markdown emphasis/backticks,
    - collapse whitespace,
    - trim noisy leading punctuation.
    """
    s = str(raw or "")
    # Remove markdown emphasis/code markers while preserving text.
    s = s.replace("**", "").replace("__", "").replace("`", "")
    # Long runs of periods are usually spurious model filler (not our single-character ellipsis trim).
    s = re.sub(r"\.{4,}", " ", s)
    # ``...; ....`` style noise between condition clauses.
    s = re.sub(r";\s*\.{2,}", "; ", s)
    s = re.sub(r"\.{2,}\s*;", ";", s)
    # Normalize multiline bullets into a sentence-like flow for CT Seller.
    s = re.sub(r"\s*[\r\n]+\s*", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = s.lstrip("-:;,. ").strip()
    return s


def normalize_brand_list(value: Any) -> list[str]:
    brands = as_str_list(value)
    out: list[str] = []
    seen: set[str] = set()
    for b in brands:
        clean = clean_listing_text(b)
        if not clean:
            continue
        if _UNBRANDED_RE.fullmatch(clean):
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def unwrap_dict_style_spec_line(line: str) -> str:
    """
    Gemini sometimes emits one-line pseudo-dicts: ``{'Form': 'Flared vase'}`` instead of ``Form: …``.
    Normalize to ``Label: value`` so semantic spec bucketing and dedupe apply.
    """
    s = (line or "").strip()
    if not (s.startswith("{") and s.endswith("}")):
        return line
    inner = s[1:-1].strip()
    # Single-pair only; values must not contain the same quote delimiter used for wrapping.
    m = re.match(r"^'([^']{1,120})'\s*:\s*'([^']*)'\s*$", inner)
    if m:
        return f"{m.group(1).strip()}: {m.group(2).strip()}"
    m = re.match(r'^"([^"]{1,120})"\s*:\s*"([^"]*)"\s*$', inner)
    if m:
        return f"{m.group(1).strip()}: {m.group(2).strip()}"
    return line


def normalize_listing_lines(value: Any) -> list[str]:
    """Drop placeholder rows such as 'Unknown' or 'Brand: Unknown'."""
    lines = as_str_list(value)
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        clean = clean_listing_text(line)
        clean = clean_listing_text(unwrap_dict_style_spec_line(clean))
        if not clean or _line_has_placeholder_value(clean):
            continue
        if re.match(r"(?i)^noted brands?\s*:", clean):
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


_LEADING_SPEC_BULLET_RE = re.compile(r"^[\s•\-\*·◇►]+")
_MEASUREMENT_HINT_RE = re.compile(
    r"\d|inch|inches|\bin\.|\bcm\b|\bmm\b|\bft\b|\bfeet\b|ruler|measuring\s*tape|yardstick|"
    r"mil ruler|quarter\s*inch|dimensions?|height|width|depth|diameter|circumf|\boz\b|\blbs?\b|\bkg\b",
    re.I,
)


def _spec_line_comparison_key(line: str) -> str:
    """Normalize for duplicate detection (bullet style and spacing should not create repeats)."""
    s = clean_listing_text(line)
    s = _LEADING_SPEC_BULLET_RE.sub("", s)
    # Collapse parentheticals that are only style notes so "(see photo)" variants dedupe together.
    s = re.sub(r"\([^)]{0,120}\)", " ", s)
    s = s.lower()
    s = s.replace("×", "x")
    s = re.sub(r"\s+", " ", s).strip(" .;:,-")
    return s


# Left-hand labels from ``Label: value`` lines that should collapse to one row per lot when
# Gemini emits near-duplicates across merged angles (same semantic bucket → longest value wins).
_SPEC_LABEL_TO_CANON: dict[str, str] = {
    "base": "spec__base",
    "base type": "spec__base",
    "base shape": "spec__base",
    "style": "spec__style",
    "form": "spec__form",
    "type": "spec__type",
    "era": "spec__era",
    "material": "spec__material",
    "color": "spec__color",
    "colors": "spec__color",
    "colour": "spec__color",
    "colours": "spec__color",
    "primary colors": "spec__color",
    "primary colours": "spec__color",
    "color palette": "spec__color",
    "colour palette": "spec__color",
    "origin": "spec__origin",
    "country of origin": "spec__origin",
    "quantity": "spec__quantity",
    "features": "spec__features",
    "trim": "spec__trim_dec",
    "accents": "spec__trim_dec",
    "accent": "spec__trim_dec",
    "accent color": "spec__trim_dec",
    "accent colour": "spec__trim_dec",
    "decoration": "spec__trim_dec",
    "surface": "spec__trim_dec",
    # Decorative / surface description: model often emits many near-duplicate rows across angles.
    "pattern": "spec__pattern",
    "patterns": "spec__pattern",
    "surface pattern": "spec__pattern",
    "repeat pattern": "spec__pattern",
    "primary pattern": "spec__pattern",
    "motif": "spec__pattern",
    "motifs": "spec__pattern",
    "design": "spec__pattern",
    "surface design": "spec__pattern",
    "visual design": "spec__pattern",
    "decor pattern": "spec__pattern",
    "decorative pattern": "spec__pattern",
    "spatter pattern": "spec__pattern",
    "sponge pattern": "spec__pattern",
    "shape": "spec__form",
    "silhouette": "spec__form",
    "subject": "spec__subject",
    "subjects": "spec__subject",
    "set size": "spec__quantity",
    "pieces in set": "spec__quantity",
    "set count": "spec__quantity",
    "components": "spec__components",
    # Surface sheen / glaze wording — model often emits many Finish lines across angles.
    "finish": "spec__finish",
    "finishes": "spec__finish",
    "surface finish": "spec__finish",
    "glaze finish": "spec__finish",
    "finish type": "spec__finish",
    "sheen": "spec__finish",
    "luster": "spec__finish",
    "lustre": "spec__finish",
    "glaze": "spec__finish",
    "gloss": "spec__finish",
}


def _primary_alt_value(text: str) -> str:
    """Keep first value when models emit alternatives (e.g. ``A / B`` or ``A; B``)."""
    s = clean_listing_text(text)
    if not s:
        return s
    parts = re.split(r"\s*(?:;|\||/|\bor\b)\s*", s, maxsplit=1, flags=re.I)
    return clean_listing_text(parts[0]) if parts else s


def _normalize_subject_form_line_for_bucket(line: str, bucket: str) -> str:
    """Force singular primary value for Subject/Form spec rows."""
    if bucket not in {"spec__subject", "spec__form"}:
        return line
    m = re.match(r"^([^:]+):\s*(.+)$", clean_listing_text(line).strip())
    if not m:
        return line
    label = "Subject" if bucket == "spec__subject" else "Form"
    rhs = _primary_alt_value(m.group(2))
    if not rhs:
        return line
    return f"{label}: {rhs}"


_ITEM_LABEL_RE = re.compile(
    r"(?i)^\s*item\s*#?\s*(\d+)\b(?:\s*\([^)]{0,120}\))?\s*$",
)


def _normalize_item_line_for_bucket(line: str, bucket: str) -> str:
    """Canonical ``Item n:`` label after merge (drops parenthetical position tags on the label)."""
    m_b = re.match(r"^spec__item_(\d+)$", bucket)
    if not m_b:
        return line
    n = int(m_b.group(1))
    m = re.match(r"^([^:]+):\s*(.+)$", clean_listing_text(line).strip())
    if not m:
        return line
    rhs = _primary_alt_value(m.group(2))
    if not rhs:
        return line
    return f"Item {n}: {rhs}"


def _normalize_spec_line_for_bucket(line: str, bucket: str) -> str:
    if bucket.startswith("spec__item_"):
        return _normalize_item_line_for_bucket(line, bucket)
    return _normalize_subject_form_line_for_bucket(line, bucket)


def _semantic_spec_bucket(line: str) -> str | None:
    """
    Map a spec line to a merge bucket, or None when the line should only use exact-string dedupe
    (measurements, size tags, unknown labels, etc.).
    """
    if not line or _line_has_placeholder_value(line):
        return None
    if re.match(r"(?i)^size\s*/\s*tag", line):
        return None
    m = re.match(r"^([^:]+):\s*(.+)$", line.strip())
    if not m:
        return None
    raw_label = m.group(1).strip()
    if len(raw_label) > 56:
        return None
    m_item = _ITEM_LABEL_RE.match(raw_label)
    if m_item:
        # Do **not** bucket ``Item n:`` for semantic collapse: each merged photo often repeats **Item 1:** for a
        # different coin — bucketing kept only the **longest** row and dropped other dates (1906, 1910-S, etc.).
        return None
    # ``Subject (Bells):`` / ``Form (primary):`` — same merge bucket as plain Subject/Form.
    if re.match(r"(?i)^(subject|subjects)\b", raw_label):
        return "spec__subject"
    if re.match(r"(?i)^(form|forms)\b", raw_label):
        return "spec__form"
    label_key = re.sub(r"\s+", " ", raw_label.lower())
    canon = _SPEC_LABEL_TO_CANON.get(label_key)
    if canon is not None:
        return canon
    # Ruler / dimensions lines often contain digits — do not treat ``Era: 1950s``-style rows as measurements.
    if _MEASUREMENT_HINT_RE.search(line):
        return None
    return None


def _normalize_spec_label_for_bucket(raw_label: str) -> str:
    """Lowercase, strip apostrophes, remove spaces so ``Production mark`` → ``productionmark``."""
    s = str(raw_label or "").strip().lower()
    s = re.sub(r"[''`]", "", s)
    return re.sub(r"\s+", "", s)


# Labels whose values are merged into a single ``Markings:`` row (order preserved, deduped).
_MARKINGS_MERGE_LABEL_KEYS: frozenset[str] = frozenset(
    {
        "markings",
        "marking",
        "productionmark",
        "productionmarks",
        "makersmark",
        "makermark",
        "impressedmark",
        "stampmark",
        "stampmarks",
        "bottommark",
        "basemark",
        "factorymark",
        "hallmark",
        "registeredmark",
        "waremark",
        "undersidemark",
        "undersidemarks",
    }
)

# Labels merged into one ``Brand:`` row (distinct values only).
_BRAND_MERGE_LABEL_KEYS: frozenset[str] = frozenset(
    {
        "brand",
        "brands",
        "maker",
        "manufacturer",
        "manufacturers",
        # Origin duplicates the same facts models often put on ``Brand:`` / ``brands[]``.
        "origin",
        "countryoforigin",
    }
)


def _unique_semicolon_phrases(parts: list[str]) -> list[str]:
    """Split each string on ``;``, trim, drop empties, preserve first-seen order (case-insensitive)."""
    out: list[str] = []
    seen: set[str] = set()
    for block in parts:
        for piece in str(block or "").split(";"):
            frag = clean_listing_text(piece).strip()
            if not frag:
                continue
            key = frag.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(frag)
    return out


def _spec_label_value_parts(line: str) -> tuple[str, str] | None:
    """``Label: value`` → (normalized_label_key, rhs value) or None."""
    m = re.match(r"^([^:]+):\s*(.+)$", clean_listing_text(line).strip())
    if not m:
        return None
    raw_l, rhs = m.group(1).strip(), m.group(2).strip()
    if not rhs or _line_has_placeholder_value(line):
        return None
    return _normalize_spec_label_for_bucket(raw_l), rhs


def _mark_rhs_is_brand_or_origin(rhs: str) -> bool:
    """
    ``Mark:`` is ambiguous (maker stamp vs country). Route long stamp prose to markings;
    ``Made in …`` / country-style phrases to the brand bucket.
    """
    v = clean_listing_text(rhs).strip()
    if not v:
        return False
    vl = v.lower()
    if re.fullmatch(r"[\d\s.]+$", v):
        return False
    if "made in" in vl or "manufactured in" in vl:
        return True
    if len(v) <= 8 and re.search(r"\d", v) and not re.search(r"[a-z]{5,}", vl):
        return False
    if len(v) > 40 and any(
        w in vl for w in ("stamp", "stamped", "incised", "impressed", "engraved", "circular", "marking")
    ):
        return False
    return len(v) >= 10


def consolidate_brand_and_marking_spec_lines(lines: list[str]) -> list[str]:
    """
    Merge repeated brand/origin rows (``Brand:`` / ``Brands:`` / duplicate ``Mark:``) into one ``Brand:`` line.

    Rows that would have been merged into ``Markings:`` are **omitted** — detailed stamp transcription is too
    error-prone for catalog specs.
    """
    brand_vals: list[str] = []
    seen_b: set[str] = set()
    roles: list[str | None] = []

    for line in lines:
        parts = _spec_label_value_parts(line)
        if parts is None:
            roles.append(None)
            continue
        lk, val = parts
        role: str | None = None
        if lk in _MARKINGS_MERGE_LABEL_KEYS:
            role = "m"
        elif lk in _BRAND_MERGE_LABEL_KEYS:
            role = "b"
            key = val.lower()
            if key not in seen_b:
                seen_b.add(key)
                brand_vals.append(val)
        elif lk == "mark":
            if _mark_rhs_is_brand_or_origin(val):
                role = "b"
                key = val.lower()
                if key not in seen_b:
                    seen_b.add(key)
                    brand_vals.append(val)
            else:
                role = "m"
        roles.append(role)

    first_b = next((i for i, r in enumerate(roles) if r == "b"), None)

    out: list[str] = []
    for i, line in enumerate(lines):
        r = roles[i]
        if r == "b":
            if i == first_b and brand_vals:
                uniq = _unique_semicolon_phrases(brand_vals)
                out.append("Brand: " + "; ".join(uniq))
            continue
        if r == "m":
            continue
        out.append(line)
    return out


def _scrub_condition_note_rhs(rhs: str) -> str:
    """Drop empty/dot-only fragments and near-empty semicolon pieces from merged condition notes."""
    s = clean_listing_text(rhs).strip(" ;")
    if not s:
        return s
    s = re.sub(r";\s*\.+", "; ", s)
    s = re.sub(r"\.{4,}", " ", s)
    parts: list[str] = []
    seen: set[str] = set()
    for piece in s.split(";"):
        f = clean_listing_text(piece).strip(" .;")
        if len(f) < 5 or re.fullmatch(r"\.+", f):
            continue
        key = f.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(f)
    return "; ".join(parts[:8]).strip(" ;")


def _dedupe_dealer_card_vs_narrative_coin_grade_rhs(rhs: str) -> str:
    """
    When one clause quotes the **dealer card / flip** grade (e.g. Very Good (VG)) and another clause assigns a
    **different** standard letter grade (e.g. Fine), drop the conflicting narrative clause — merged frames often double-grade.
    """
    s = clean_listing_text(rhs).strip()
    if not s or ";" not in s:
        return s
    low = s.lower()
    if not any(k in low for k in ("dealer card", "dealer flip", "on the card", "holder reads", "card reads")):
        return s
    parts = [clean_listing_text(p).strip() for p in s.split(";") if clean_listing_text(p).strip()]
    if len(parts) < 2:
        return s
    ref = next((p for p in parts if any(k in p.lower() for k in ("card", "flip", "holder"))), None)
    if not ref:
        return s
    if not re.search(r"(?i)\b(vg|vf|xf|f|g|ag|au|unc|ms|pr)\b|very good|very\s+fine|extra fine|about unc|uncirculated|fine", ref):
        return s

    def _mentions_vg(t: str) -> bool:
        tl = t.lower()
        return "very good" in tl or re.search(r"(?<![a-z])vg(?![a-z])", tl) is not None or "(vg)" in tl

    def _mentions_fine(t: str) -> bool:
        tl = t.lower()
        if "very fine" in tl:
            return False
        return re.search(r"(?<![a-z])fine(?![a-z])", tl) is not None

    ref_vg = _mentions_vg(ref)
    ref_f = _mentions_fine(ref) and not ref_vg

    out: list[str] = []
    for p in parts:
        pl = p.lower()
        if p == ref:
            out.append(p)
            continue
        if ref_vg and _mentions_fine(p) and not _mentions_vg(p):
            continue
        if ref_f and _mentions_vg(p):
            continue
        out.append(p)
    return "; ".join(out).strip(" ;")


def _collapse_redundant_condition_clauses(rhs: str) -> str:
    """
    When many near-duplicate wear clauses were joined with semicolons (often from merged frames), replace with one
    readable estate sentence.
    """
    s = clean_listing_text(rhs).strip().strip(" .;")
    if not s:
        return s
    parts = [clean_listing_text(p).strip(" .;") for p in s.split(";") if clean_listing_text(p).strip(" .;")]
    if len(parts) < 3:
        return s
    wear_kw = ("oxidation", "tarnish", "patina", "wear", "vintage", "age", "surface", "moderate", "light", "honest")
    if all(any(k in p.lower() for k in wear_kw) for p in parts):
        return "Surface oxidation and tarnish consistent with age"
    coin_kw = ("uncirculated", "mint", "sealed", "quarter", "map", "folder", "sleeve", "circulation", "handling")
    if len(parts) >= 4 and sum(1 for p in parts if any(k in p.lower() for k in coin_kw)) >= 3:
        sealed = next((p for p in parts if "seal" in p.lower() or "uncirculated" in p.lower()), "")
        loose = next((p for p in parts if "circulation" in p.lower() or "loose" in p.lower()), "")
        pack = next((p for p in parts if "map" in p.lower() or "folder" in p.lower() or "packaging" in p.lower()), "")
        bits = [b for b in (sealed, pack, loose) if b]
        if len(bits) >= 2:
            return "; ".join(dict.fromkeys(bits))
    return s


def consolidate_condition_note_spec_lines(lines: list[str]) -> list[str]:
    """
    Merge ``Condition note:`` / ``Condition notes:`` rows into **one** line; collapse long stacks of similar wear text.
    """
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        s = clean_listing_text(line).strip()
        m = re.match(r"(?i)^\s*condition\s*notes?\s*:\s*(.+)$", s)
        if m:
            rhs = m.group(1).strip().strip(" .;")
            if rhs:
                hits.append((i, rhs))
    if not hits:
        return lines
    first_i = min(i for i, _ in hits)
    vals = [rhs.replace("..", ".") for _, rhs in hits]
    if len(vals) >= 2:
        body = "; ".join(_unique_semicolon_phrases(vals))
    else:
        body = vals[0]
    body = clean_listing_text(re.sub(r"\s*;\s*", "; ", body)).strip(" ;")
    body = _scrub_condition_note_rhs(body)
    body = _dedupe_dealer_card_vs_narrative_coin_grade_rhs(body)
    body = _collapse_redundant_condition_clauses(body)
    if len(body) > 260:
        body = _trim_prose_at_limit(body, 260).rstrip(" ,;.")
    if body and not body.endswith((".", "!", "?")):
        body += "."
    skip_idx = {i for i, _ in hits}
    out: list[str] = []
    for i, line in enumerate(lines):
        if i in skip_idx:
            if i == first_i:
                out.append(f"Condition note: {body}")
            continue
        out.append(line)
    return out


def _split_into_sentences(text: str) -> list[str]:
    """Light sentence split for prose cleanup (handles common closers)."""
    s = clean_listing_text(text).strip()
    if not s:
        return []
    parts = re.split(r"(?<=[.!?])\s+", s)
    return [p.strip() for p in parts if p.strip()]


def _drop_prose_sentences_conflicting_with_components(prof: str, specification_lines: list[str]) -> str:
    """
    Remove sentences that obsess over an object **category** (ewer, bell, censer, dragon, …) not supported by the
    **Components:** / Item lines, so multi-piece prose matches the actual lot inventory.
    """
    comp = _components_spec_text(specification_lines)
    if not comp:
        return prof
    cl = comp.lower()
    blob = " ".join(specification_lines).lower()
    comp_cats = _distinct_inventory_categories(cl) | _distinct_inventory_categories(blob)
    if len(comp_cats) < 2:
        return prof
    kept: list[str] = []
    for sent in _split_into_sentences(prof):
        sl = sent.lower()
        sent_cats = _distinct_inventory_categories(sl)
        if sent_cats and not sent_cats <= comp_cats:
            continue
        kept.append(sent)
    out = " ".join(kept).strip()
    if len(out) < 18:
        mat = _material_lead_word_from_title_and_specs(prof, specification_lines)
        return f"{mat} grouping with the pieces listed in Components - a warm display lot."
    return out


def _collapse_semantic_spec_lines(lines: list[str]) -> list[str]:
    """
    After exact dedupe: for common ``Label:`` prefixes (Style, Material, Base, …), keep a single line
    per merged-photo burst — earliest position in the list, **longest** text among duplicates (most detail).
    """
    members: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for i, line in enumerate(lines):
        b = _semantic_spec_bucket(line)
        if b is not None:
            members[b].append((i, line))

    first_idx: dict[str, int] = {}
    winner: dict[str, str] = {}
    for b, pairs in members.items():
        first_idx[b] = min(i for i, _ in pairs)
        # Prefer longer value; tie-breaker keeps the earlier line so merge stays stable.
        winner[b] = max(pairs, key=lambda p: (len(p[1]), -p[0]))[1]

    out: list[str] = []
    for i, line in enumerate(lines):
        b = _semantic_spec_bucket(line)
        if b is None:
            out.append(line)
            continue
        if i == first_idx[b]:
            out.append(_normalize_spec_line_for_bucket(winner[b], b))
        # else: skip duplicate bucket member
    return out


def _prune_subject_form_when_item_bullets_present(lines: list[str]) -> list[str]:
    """
    When the model already enumerated pieces with **three or more** ``Item n:`` bullets, drop **Subject:** / **Form:**
    rows that usually repeat the same inventory under another heading (pairs/small lots keep Form/Subject).
    """
    item_n = 0
    for line in lines:
        s = clean_listing_text(line).strip()
        if re.match(r"(?i)^item\s*#?\s*\d+\s*:", s):
            item_n += 1
    if item_n < 3:
        return lines
    out: list[str] = []
    for line in lines:
        s = clean_listing_text(line).strip()
        m = re.match(r"^([^:]+):", s)
        if m and re.match(r"(?i)^(subject|subjects|form|forms)\b", m.group(1).strip()):
            continue
        out.append(line)
    return out


def _rhs_looks_like_mint_set_inventory(rhs: str) -> bool:
    """True when an ``Item n:`` line summarizes a full mint set / denomination row, not one display quarter."""
    low = rhs.lower()
    if re.search(r"\b(?:two|three|four|five|six|seven|eight|nine|ten|one|\d+)\s+\d{4}\b", low):
        return True
    if any(
        p in low
        for p in (
            "mint sleeve",
            "original mint",
            "uncirculated set",
            "denominations in",
            "standard denominations",
            "kennedy half",
            "sacagawea",
        )
    ):
        return True
    if sum(1 for k in ("dime", "nickel", "cent", "penny", "half dollar") if k in low) >= 2:
        return True
    return False


def _collapse_loose_display_quarter_item_rows(lines: list[str]) -> list[str]:
    """
    Replace several ``Item n:`` rows that each name one commemorative/state quarter with a single summary line
    when the lot clearly includes a map or mint sets (those rows are display/slot noise, not separate SKUs).
    """
    blob = " ".join(lines).lower()
    if not any(
        k in blob for k in ("map", "state quarter", "mint set", "uncirculated", "folder", "collector", "50 state")
    ):
        return lines
    remove_idx: set[int] = set()
    snippets: list[str] = []
    for i, line in enumerate(lines):
        s = clean_listing_text(line).strip()
        m = re.match(r"(?i)^\s*item\s*#?\s*\d+\s*:\s*(.+)$", s)
        if not m:
            continue
        rhs = m.group(1).strip()
        if "quarter" not in rhs.lower():
            continue
        if _rhs_looks_like_mint_set_inventory(rhs):
            continue
        remove_idx.add(i)
        snippets.append(rhs.rstrip("."))
    if len(remove_idx) < 2:
        return lines
    out = [line for i, line in enumerate(lines) if i not in remove_idx]
    summary_rhs = "; ".join(snippets[:8])
    if len(summary_rhs) > 210:
        summary_rhs = summary_rhs[:207] + "…"
    summary = f"Display quarters (examples): {summary_rhs}"
    insert_at = 0
    for j, line in enumerate(out):
        if re.match(r"(?i)^\s*item\s*#?\s*\d+\s*:", clean_listing_text(line).strip()):
            insert_at = j + 1
    out.insert(insert_at, summary)
    return out


def _spec_line_sort_rank(line: str) -> tuple[int, int]:
    """Stable ordering: Components and condition near the top; Brand near the end; ``Item n`` numeric block together."""
    s = clean_listing_text(line).strip()
    m = re.match(r"^([^:]+):", s)
    if not m:
        return (55, 0)
    raw = m.group(1).strip()
    lab = raw.lower()
    if lab.startswith("components"):
        return (0, 0)
    if re.match(r"condition\s*notes?", lab):
        return (5, 0)
    if lab == "style":
        return (10, 0)
    if lab == "finish":
        return (15, 0)
    if lab == "era":
        return (20, 0)
    m_item = re.match(r"(?i)^item\s*#?\s*(\d+)", raw)
    if m_item:
        return (30, int(m_item.group(1)))
    if lab.startswith("display quarter"):
        return (32, 0)
    if "denomination" in lab:
        return (35, 0)
    if lab == "material":
        return (40, 0)
    if lab in ("brand", "maker", "manufacturer"):
        return (90, 0)
    return (50, 0)


def sort_specification_lines_canonical(lines: list[str]) -> list[str]:
    """Reorder ``Label:`` rows into a predictable auction spec order (fixes Brand between Item bullets)."""
    if len(lines) < 2:
        return lines
    indexed = list(enumerate(lines))
    indexed.sort(key=lambda it: (_spec_line_sort_rank(it[1]), it[0]))
    return [line for _, line in indexed]


_ITEM_LINE_BODY_RE = re.compile(r"(?i)^\s*item\s*#?\s*\d+\s*:\s*(.+)$")


def renumber_item_spec_lines_sequentially(lines: list[str]) -> list[str]:
    """
    After merge, many frames may each emit ``Item 1:`` for a different coin. With semantic buckets disabled for
    items, renumber into ``Item 1:`` … ``Item n:`` in list order so the listing reads cleanly.
    """
    out: list[str] = []
    seq = 0
    for line in lines:
        m = _ITEM_LINE_BODY_RE.match(clean_listing_text(line).strip())
        if not m:
            out.append(line)
            continue
        seq += 1
        rhs = m.group(1).strip()
        out.append(f"Item {seq}: {rhs}")
    return out


def _garment_line_signature(text: str) -> str:
    """Stable key for duplicate jacket/cardigan rows across photo angles."""
    s = clean_listing_text(text)
    if not s:
        return ""
    low = s.lower()
    brand = ""
    for pat, name in _KNOWN_LABEL_BRAND_PATTERNS:
        if pat.search(s):
            brand = name.lower()
            break
    if not brand:
        for token in ("tribal", "alfred dunner", "white mountain"):
            if token in low:
                brand = token
                break
    garment = ""
    for pat, label in _APPAREL_GARMENT_HINTS:
        if pat.search(s):
            garment = label
            break
    if brand and garment:
        return f"{brand}|{garment}"
    if brand:
        return brand
    if garment:
        return garment
    return re.sub(r"\s+", " ", low[:72]).strip()


def _pick_better_item_body(a: str, b: str) -> str:
    """Prefer the row that names a label brand; otherwise the shorter clear summary."""
    a_has = any(p.search(a) for p, _ in _KNOWN_LABEL_BRAND_PATTERNS) or re.search(
        r"(?i)\b(tribal|alfred\s+dunner|white\s+mountain)\b", a
    )
    b_has = any(p.search(b) for p, _ in _KNOWN_LABEL_BRAND_PATTERNS) or re.search(
        r"(?i)\b(tribal|alfred\s+dunner|white\s+mountain)\b", b
    )
    if a_has and not b_has:
        return a
    if b_has and not a_has:
        return b
    return a if len(a) <= len(b) * 1.2 else b


def _short_deliverable_phrase(body: str, max_len: int = 110) -> str:
    s = clean_listing_text(body).strip()
    if not s:
        return ""
    head = re.split(r"\s*;\s*", s, maxsplit=1)[0].strip()
    s = head if len(head) >= 16 else s
    if len(s) <= max_len:
        return s
    cut = s[: max_len - 1].rsplit(" ", 1)[0].strip()
    return (cut or s[: max_len - 1]) + "…"


def _unique_deliverable_phrases_from_items_and_components(
    bodies: list[str], lines: list[str]
) -> list[str]:
    """One short phrase per distinct garment (brand + type), not per catalog photo."""
    phrases: list[str] = []
    seen: set[str] = set()
    for line in lines:
        m = _ITEM_LINE_BODY_RE.match(clean_listing_text(line).strip())
        if not m:
            continue
        body = m.group(1).strip()
        sig = _garment_line_signature(body)
        if not sig or sig in seen:
            continue
        seen.add(sig)
        phrases.append(_short_deliverable_phrase(body))
    for block in bodies:
        for piece in re.split(r"[;]|(?:\band\b)", str(block or ""), flags=re.I):
            frag = clean_listing_text(piece).strip()
            if len(frag) < 14:
                continue
            sig = _garment_line_signature(frag)
            if not sig or sig in seen:
                continue
            if not any(p.search(frag) for p, _ in _APPAREL_GARMENT_HINTS):
                continue
            seen.add(sig)
            phrases.append(_short_deliverable_phrase(frag))
    return phrases[:8]


def collapse_duplicate_item_spec_lines(lines: list[str]) -> list[str]:
    """
    Merged apparel lots often repeat ``Item 1:`` on every photo angle. Collapse to one row per garment signature.
    """
    bodies: list[str] = []
    other: list[str] = []
    for line in lines:
        m = _ITEM_LINE_BODY_RE.match(clean_listing_text(line).strip())
        if m:
            bodies.append(m.group(1).strip())
        else:
            other.append(line)
    if len(bodies) <= 1:
        return lines
    by_sig: dict[str, str] = {}
    order: list[str] = []
    for body in bodies:
        sig = _garment_line_signature(body)
        if sig not in by_sig:
            order.append(sig)
            by_sig[sig] = body
        else:
            by_sig[sig] = _pick_better_item_body(by_sig[sig], body)
    if len(order) >= len(bodies):
        return lines
    rebuilt = [f"Item {i + 1}: {by_sig[s]}" for i, s in enumerate(order)]
    out: list[str] = []
    inserted = False
    for line in other:
        out.append(line)
        if not inserted and re.match(
            r"(?i)^\s*components\s*:", clean_listing_text(line).strip()
        ):
            out.extend(rebuilt)
            inserted = True
    if not inserted:
        out.extend(rebuilt)
    return out


def rebuild_components_from_unique_items(lines: list[str]) -> list[str]:
    """Replace bloated multi-frame ``Components:`` with one line built from collapsed ``Item n:`` rows."""
    item_bodies: list[str] = []
    comp_indices: list[int] = []
    for i, line in enumerate(lines):
        m_item = _ITEM_LINE_BODY_RE.match(clean_listing_text(line).strip())
        if m_item:
            item_bodies.append(m_item.group(1).strip())
        m_comp = re.match(
            r"(?i)^\s*components\s*:\s*(.+)$", clean_listing_text(line).strip()
        )
        if m_comp and m_comp.group(1).strip():
            comp_indices.append(i)
    if len(item_bodies) < 2 or not comp_indices:
        return lines
    comp_text = " ".join(
        clean_listing_text(lines[i]).split(":", 1)[-1] for i in comp_indices
    )
    if len(item_bodies) < 4 and len(comp_text) < 380:
        return lines
    phrases = [_short_deliverable_phrase(b) for b in item_bodies]
    merged_rhs = "; ".join(p for p in phrases if p)
    if len(merged_rhs) > 420:
        merged_rhs = merged_rhs[:417] + "…"
    new_line = f"Components: {merged_rhs}"
    skip = set(comp_indices)
    out: list[str] = []
    first_i = min(comp_indices)
    for i, line in enumerate(lines):
        if i in skip:
            if i == first_i:
                out.append(new_line)
            continue
        out.append(line)
    return out


def consolidate_frame_size_tag_lines(
    lines: list[str], labels_and_tags: list[str] | None = None
) -> list[str]:
    """One ``Size / tags:`` line instead of one row per merged photo frame."""
    tag_parts: list[str] = []
    other: list[str] = []
    frame_tag_count = 0
    for line in lines:
        m = _FRAME_SIZE_TAG_RE.match(clean_listing_text(line).strip())
        if m:
            frame_tag_count += 1
            rhs = m.group(1).strip()
            if rhs:
                tag_parts.extend(_unique_semicolon_phrases([rhs]))
        else:
            other.append(line)
    if labels_and_tags:
        for lab in labels_and_tags:
            t = clean_listing_text(lab).strip()
            if t and len(t) <= 120:
                tag_parts.append(t)
    if not tag_parts:
        return lines
    seen: set[str] = set()
    unique: list[str] = []
    for t in tag_parts:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(t)
    needs_merge = frame_tag_count > 1 or len(unique) > 3
    if not needs_merge and labels_and_tags and len(unique) >= 2:
        needs_merge = True
    if not needs_merge:
        return lines
    summary = "; ".join(unique[:8])
    if len(summary) > 320:
        summary = summary[:317] + "…"
    return other + [f"Size / tags: {summary}"]


def consolidate_multi_frame_components_lines(lines: list[str]) -> list[str]:
    """
    Merge several ``Components:`` rows from merged catalog photos into **one** line.

    ``_collapse_semantic_spec_lines`` buckets every ``Components:`` into ``spec__components`` and keeps only the
    **longest** value — so a later frame (e.g. only 1908) can **erase** earlier dates (1904, 1906, 1910-S) from the lot.
    """
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = re.match(r"(?i)^\s*components\s*:\s*(.+)$", clean_listing_text(line).strip())
        if m:
            rhs = m.group(1).strip().rstrip(";.")
            if rhs:
                hits.append((i, rhs))
    if len(hits) <= 1:
        return lines
    bodies = [rhs for _, rhs in hits]
    deliverables = _unique_deliverable_phrases_from_items_and_components(bodies, lines)
    if deliverables:
        merged_rhs = "; ".join(deliverables)
    else:
        merged_rhs = "; ".join(_unique_semicolon_phrases(bodies))
    cap = 420 if deliverables else 900
    if len(merged_rhs) > cap:
        merged_rhs = merged_rhs[: cap - 1] + "…"
    merged_line = f"Components: {merged_rhs}"
    skip = {i for i, _ in hits}
    out: list[str] = []
    first_i = min(i for i, _ in hits)
    for i, line in enumerate(lines):
        if i in skip:
            if i == first_i:
                out.append(merged_line)
            continue
        out.append(line)
    return out


def dedupe_specification_lines_preserve_order(lines: list[str]) -> list[str]:
    """
    Drop repeated spec bullets across merged photo frames.

    Uses a stronger normalization than plain lower() so ``- Foo`` and ``• Foo`` collapse once.
    Lines that look like **measurements or ruler context** (digits, units, ``ruler``, etc.) only dedupe
    when the normalized key matches exactly, so different per-item sizes stay separate.
    Then collapses noisy repeated **Label:** categories (multiple ``Style:`` lines → one).
    """
    seen: set[str] = set()
    first: list[str] = []
    for raw in lines:
        line = clean_listing_text(unwrap_dict_style_spec_line(clean_listing_text(raw)))
        if not line or _line_has_placeholder_value(line):
            continue
        key = _spec_line_comparison_key(line)
        if not key or key in seen:
            continue
        seen.add(key)
        first.append(line)
    first = collapse_duplicate_item_spec_lines(first)
    first = consolidate_multi_frame_components_lines(first)
    first = rebuild_components_from_unique_items(first)
    merged = consolidate_brand_and_marking_spec_lines(_collapse_semantic_spec_lines(first))
    merged = consolidate_condition_note_spec_lines(merged)
    merged = _collapse_loose_display_quarter_item_rows(merged)
    merged = sort_specification_lines_canonical(merged)
    merged = renumber_item_spec_lines_sequentially(merged)
    return _prune_subject_form_when_item_bullets_present(merged)


def _components_spec_text(specification_lines: list[str]) -> str | None:
    for line in specification_lines:
        m = re.match(r"(?i)^\s*components\s*:\s*(.+)$", clean_listing_text(line).strip())
        if m:
            rhs = clean_listing_text(m.group(1)).strip(" .;")
            if rhs:
                return rhs
    return None


def _components_indicate_multi_piece(components_text: str) -> bool:
    """
    True when **Components:** clearly describes several salable units or a true group lot.

    Coin dates (e.g. 1906) must **not** be summed as quantities — ``sum([1906]) >= 2`` wrongly fired
    ``Includes multiple pieces`` for a single cent plus a paper tag.
    """
    raw = components_text or ""
    nums = [int(x) for x in re.findall(r"\b\d+\b", raw)]
    coin_year = frozenset(range(1700, 2040))
    years = [n for n in nums if n in coin_year]
    if len(set(years)) >= 2:
        return True
    countish = [n for n in nums if n not in coin_year and 2 <= n <= 500]
    if countish and sum(countish) >= 2:
        return True
    low = raw.lower()
    return any(
        k in low
        for k in (
            "set",
            "pieces",
            "cups",
            "tumblers",
            "jars",
            "bowls",
            "plates",
            "glasses",
            "ewer",
            "censer",
            "incense",
            "striker",
            "gong",
            "figurine",
            "bell",
            "bells",
            "clapper",
            "folder",
            "folders",
            "album",
            "holders",
            "holder",
            "envelopes",
            "envelope",
            "cards",
            "lace",
            "laces",
            "runner",
            "runners",
            "textile",
            "textiles",
            "doily",
            "doilies",
            "valance",
            "valances",
            "embroidery",
            "embroidered",
            "crochet",
            "needlework",
            "openwork",
            "dresser scarf",
            "table runner",
            "linen",
            "tapestry",
        )
    )


def _dimension_spec_line_count(specification_lines: list[str]) -> int:
    return sum(
        1
        for line in specification_lines
        if re.match(r"(?i)^\s*dimensions?\s*:", clean_listing_text(line).strip())
    )


def _spec_lines_indicate_multi_textile_piece(specification_lines: list[str]) -> bool:
    """Several measuring-tape spans on lace/linen/textile lots usually mean **distinct fabric pieces** in one SKU."""
    blob = " ".join(specification_lines).lower()
    if not any(
        k in blob
        for k in (
            "lace",
            "textile",
            "textiles",
            "runner",
            "linen",
            "embroider",
            "crochet",
            "needlework",
            "openwork",
            "doily",
            "valance",
            "dresser scarf",
            "table runner",
            "tapestry",
        )
    ):
        return False
    if _dimension_spec_line_count(specification_lines) >= 2:
        return True
    return len(re.findall(r"\b\d{1,3}\s*[\"']?\s*(?:in|inch|inches)\b", blob, re.I)) >= 2


_INVENTORY_TITLE_CATEGORY_MAP: tuple[tuple[str, str], ...] = (
    ("bells", "bell"),
    ("clapper", "bell"),
    ("bell", "bell"),
    ("dallah", "ewer"),
    ("aftaba", "ewer"),
    ("flagon", "ewer"),
    ("pitcher", "ewer"),
    ("ewer", "ewer"),
    ("censers", "censer"),
    ("censer", "censer"),
    ("incense", "censer"),
    ("burner", "censer"),
    ("serpent", "dragon"),
    ("dragon", "dragon"),
    ("striker", "striker"),
    ("gong", "gong"),
    ("lace", "lace"),
    ("openwork", "lace"),
    ("crochet", "lace"),
    ("runner", "textile_runner"),
    ("runners", "textile_runner"),
    ("textile", "textile"),
    ("textiles", "textile"),
    ("doily", "textile"),
    ("valance", "textile"),
    ("linen", "textile"),
    ("tapestry", "textile"),
    ("embroidery", "textile"),
    ("needlework", "textile"),
)


def _distinct_inventory_categories(text_low: str) -> set[str]:
    cats: set[str] = set()
    for needle, cat in _INVENTORY_TITLE_CATEGORY_MAP:
        if needle in text_low:
            cats.add(cat)
    return cats


def _title_narrows_multi_component_lot(title: str, specification_lines: list[str]) -> bool:
    """
    True when **Components:** clearly lists several distinct object types but the auction title only signals one
    (e.g. \"Two-Piece Incense Censer\" while Components also list ewer, bell, dragon stand).
    """
    comp = _components_spec_text(specification_lines)
    blob = " ".join(specification_lines).lower()
    tl = (title or "").lower()
    multi_dim_textile = _spec_lines_indicate_multi_textile_piece(specification_lines)
    if multi_dim_textile and not any(
        w in tl for w in ("lot", "group", "collection", "pieces", "multiple", "assorted", "set")
    ):
        return True
    if not (comp and _components_indicate_multi_piece(comp)) and not _spec_lines_indicate_multi_textile_piece(
        specification_lines
    ):
        return False
    cl = (comp or "").lower()
    c_cats = _distinct_inventory_categories(cl) | _distinct_inventory_categories(blob)
    t_cats = _distinct_inventory_categories(tl)
    if len(c_cats) < 2:
        return False
    missing = c_cats - t_cats
    if len(t_cats) <= 1 and len(missing) >= 1:
        return True
    if len(missing) >= 2:
        return True
    return False


def _material_lead_word_from_title_and_specs(title: str, specification_lines: list[str]) -> str:
    blob = f"{title} {' '.join(specification_lines)}".lower()
    if "bronze" in blob:
        return "Bronze"
    if "pewter" in blob or "zinn" in blob:
        return "Pewter"
    if "sterling" in blob or "925" in blob:
        return "Sterling silver"
    if "silver plate" in blob or "epns" in blob:
        return "Silver plate"
    if "ceramic" in blob or "porcelain" in blob:
        return "Ceramic"
    if "glass" in blob:
        return "Glass"
    return "Brass"


def _broaden_auction_title_for_components(title: str, specification_lines: list[str]) -> str:
    """
    Replace an under-inclusive title with a short lot-style title when **Components:** proves multiple distinct forms.
    """
    t = (title or "").strip()
    if not t or not _title_narrows_multi_component_lot(t, specification_lines):
        return t
    blob = " ".join(specification_lines).lower()
    if _spec_lines_indicate_multi_textile_piece(specification_lines):
        fiber = "Cotton"
        if "linen" in blob and "cotton" not in blob:
            fiber = "Linen"
        elif "silk" in blob:
            fiber = "Silk"
        elif "wool" in blob:
            fiber = "Wool"
        return f"{fiber} lace and textile lot - multiple pieces; dimensions vary by photo (refer to tape reads)"[:500]
    mat = _material_lead_word_from_title_and_specs(t, specification_lines)
    out = f"{mat} decorative table lot - multiple pieces (see Components)"
    return out[:500]


def _coin_years_in_text(text: str) -> set[int]:
    """Collect plausible U.S. coin dates from text (e.g. 1904, 1910-S → 1910)."""
    years: set[int] = set()
    for m in re.finditer(r"\b((?:17|18|19|20)\d{2})(?:\s*[-–]?\s*[A-Za-z]+)?\b", text or ""):
        y = int(m.group(1))
        if 1700 <= y <= 2039:
            years.add(y)
    return years


_INVENTORY_SPEC_FOR_DATES_RE = re.compile(
    r"(?i)^\s*(components|item\s*#?\s*\d+|display\s+quarter)",
)

_COIN_DATE_CONTEXT_RE = re.compile(
    r"(?i)(cent|penny|pennies|cents|lincoln|wheat|indian|littleton|coin|holder|holders|"
    r"card|flip|dime|nickel|quarter|head\s+cent)",
)


def _inventory_chunks_for_coin_dates(specification_lines: list[str]) -> str:
    """
    Text used to infer **which** years belong to coins in the lot.

    Scanning every spec line pulled in stray years (e.g. folder copyright **1975**) from **Era** / **Style** / other rows.
    """
    parts: list[str] = []
    for line in specification_lines:
        s = clean_listing_text(line).strip()
        if _INVENTORY_SPEC_FOR_DATES_RE.match(s):
            parts.append(s)
    return " ".join(parts)


def _inventory_coin_years(specification_lines: list[str]) -> set[int]:
    """
    Years inferred only from **Components** clauses that mention coins/holders and from **Item** / **Display** lines,
    so folder copyright dates do not enter the title or synthesized lot prose.
    """
    years: set[int] = set()
    for line in specification_lines:
        s = clean_listing_text(line).strip()
        m_comp = re.match(r"(?i)^\s*components\s*:\s*(.+)$", s)
        if m_comp:
            for piece in m_comp.group(1).split(";"):
                frag = piece.strip()
                if not frag or not _COIN_DATE_CONTEXT_RE.search(frag):
                    continue
                years |= _coin_years_in_text(frag)
            continue
        if re.match(r"(?i)^\s*item\s*#?\s*\d+\s*:", s):
            years |= _coin_years_in_text(s)
            continue
        if re.match(r"(?i)^\s*display\s+quarter", s):
            years |= _coin_years_in_text(s)
    return years


def _format_cent_year_tokens_chronological(specification_lines: list[str], years: set[int]) -> str:
    """``1904, 1906, 1908, 1910-S`` — use mint suffix from inventory text when present (e.g. ``1910-S``)."""
    if not years:
        return ""
    chunk = _inventory_chunks_for_coin_dates(specification_lines)
    mint_by_year: dict[int, str] = {}
    for m in re.finditer(r"\b((?:18|19|20)\d{2})\s*[-–]?\s*([SDOP])\b", chunk, re.I):
        y = int(m.group(1))
        if y in years:
            mint_by_year[y] = f"{m.group(1)}-{m.group(2).upper()}"
    return ", ".join(mint_by_year.get(y, str(y)) for y in sorted(years))


def _broaden_auction_title_for_numismatic_multi_year(
    title: str, specification_lines: list[str]
) -> str:
    """
    After **merge_detail**, merged **Components** / **Item** lines often list **several** cent dates while **item_name**
    stayed on the **first** frame (e.g. only 1904). Replace with an inclusive lot-style title when multiple years are
    visible in specs but the title still signals a single date.
    """
    t = (title or "").strip()
    if not t:
        return t
    blob = " ".join(specification_lines).lower()
    coinish = any(
        k in blob
        for k in (
            " cent",
            "cents",
            "penny",
            "pennies",
            "lincoln",
            "indian head",
            "littleton",
            "wheat",
            " holder",
            "flip",
            "dealer card",
        )
    ) or any(k in t.lower() for k in (" cent", "penny", "lincoln", "indian head"))
    if not coinish:
        return t
    years = _inventory_coin_years(specification_lines)
    if len(years) < 2:
        return t
    title_years = _coin_years_in_text(t)
    if years <= title_years:
        return t
    bits: list[str] = []
    if "lincoln" in blob or "wheat" in blob:
        bits.append("Lincoln")
    if "indian head" in blob:
        bits.append("Indian Head")
    series = " / ".join(bits) if bits else "U.S."
    y_disp = _format_cent_year_tokens_chronological(specification_lines, years)
    if len(years) > 6:
        y_disp = f"{min(years)}–{max(years)} ({len(years)} dates; verify in photos)"
    has_holder = "littleton" in blob or "holder" in blob or "flip" in blob or "dealer card" in blob
    has_folder = "folder" in blob or "album" in blob
    pack_parts: list[str] = []
    if has_holder:
        pack_parts.append("Littleton-style holders")
    if has_folder:
        pack_parts.append("folders")
    pack = " + ".join(pack_parts) if pack_parts else "as shown in photos"
    mat = _material_lead_word_from_title_and_specs(t, specification_lines)
    lead = mat if mat and mat != "Brass" else "Vintage"
    if lead == "Vintage":
        out = f"Vintage {series} coin lot ({y_disp}) - {pack}"
    else:
        out = f"{lead} {series} coin lot ({y_disp}) - {pack}"
    return out[:500]


MERGED_PRO_DESCRIPTION_MAX_CHARS = 1000
"""Hard char backstop after merge; primary length control is ``PROFESSIONAL_DESCRIPTION_MAX_WORDS``."""

PROFESSIONAL_DESCRIPTION_MAX_WORDS = 50
"""Estate prose stays scannable (~half prior ~95-word guidance); inventory belongs in specification_lines."""


def _synthesize_numismatic_multi_cent_prose(
    description: str, specification_lines: list[str], photo_count: int
) -> str:
    """
    After ``merge_detail``, replace stitched single-coin paragraphs with one **lot-level** overview that names
    **all** cent dates found in specs when several photos were merged.
    """
    if photo_count < 2:
        return description
    full = " ".join(specification_lines)
    years = _inventory_coin_years(specification_lines)
    if len(years) < 2:
        return description
    blob = full.lower()
    if not any(
        k in blob
        for k in (
            " cent",
            "cents",
            "penny",
            "pennies",
            "indian head",
            "lincoln",
            "littleton",
            "wheat",
        )
    ):
        return description
    y_disp = _format_cent_year_tokens_chronological(specification_lines, years)
    bits: list[str] = []
    if "folder" in blob or "album" in blob:
        bits.append("collector folders (Lincoln and/or Indian Head themes)")
    if any(
        k in blob
        for k in ("littleton", "flip", "envelope", "dealer card", " holder", "holders", "collector's envelope")
    ):
        bits.append("Littleton-style dealer cards or holders")
    pack = "; ".join(bits) if bits else "items as shown in the photos"
    out = (
        f"This estate U.S. coin lot spans {y_disp}, together with {pack}. "
        "Refer to the listing photos as the most accurate way to confirm each coin's date, mint mark, and detail."
    )
    return _trim_prose_at_limit(out.strip(), MERGED_PRO_DESCRIPTION_MAX_CHARS)


def _synthesize_textile_measurement_lot_prose(
    description: str, specification_lines: list[str], photo_count: int
) -> str:
    """
    After ``merge_detail``, when several photos show **different** lace/textile spans on the tape but prose still
    reads like **one** rectangle, replace with a short honest lot overview.
    """
    if photo_count < 2:
        return description
    if not _spec_lines_indicate_multi_textile_piece(specification_lines):
        return description
    if len(_inventory_coin_years(specification_lines)) >= 2:
        return description
    low = (description or "").lower()
    if "this estate u.s. coin lot" in low or "coin lot spans" in low:
        return description
    out = (
        "This lot brings together several vintage lace and cotton textile pieces—not one single swatch. "
        "Measuring tapes in the photos often show different spans for different panels; rely on the images for the clearest look at size, holes, fraying, and edge condition."
    )
    return _trim_prose_at_limit(out.strip(), MERGED_PRO_DESCRIPTION_MAX_CHARS)


def _trim_prose_at_limit(text: str, max_chars: int) -> str:
    """Trim at sentence boundary when possible; otherwise at word boundary."""
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    budget = max_chars - 1
    cut = t[:budget].rstrip()
    for sep in (".\n\n", ".\n", ". ", "?", "!"):
        idx = cut.rfind(sep)
        if idx >= int(max_chars * 0.45):
            return cut[: idx + len(sep)].strip()
    sp = cut.rfind(" ")
    if sp >= int(max_chars * 0.35):
        return cut[:sp].strip() + "…"
    return cut.strip() + "…"


def _drop_incomplete_sentence_after_last_period(text: str) -> str:
    """
    When the model or word-cap leaves a dangling tail (e.g. ``… see Components. This official U.S.``),
    keep text through the last complete sentence-ending clause.
    """
    t = clean_listing_text(text).strip()
    if not t:
        return t
    # ``U.S.`` sets ``endswith('.')`` true even when the sentence is an obvious truncation stub.
    t = re.sub(r"\s+This official(?:\s+U\.?S\.?)?$", "", t, flags=re.I).strip()
    if t.endswith((".", "!", "?")):
        return t
    best = -1
    for needle in (". ", "? ", "! ", ".\n"):
        pos = 0
        while True:
            j = t.find(needle, pos)
            if j == -1:
                break
            if j >= int(len(t) * 0.28):
                best = max(best, j + len(needle.rstrip("\n")))
            pos = j + 1
    if best > 0:
        return t[:best].strip()
    return t


def _trim_prose_to_max_words(text: str, max_words: int) -> str:
    """Keep the opening of the description; prefer cutting at a sentence boundary."""
    t = clean_listing_text(text or "").strip()
    if not t:
        return t
    words = t.split()
    if len(words) <= max_words:
        return _drop_incomplete_sentence_after_last_period(t)
    chunk = " ".join(words[:max_words])
    for sep in (". ", "? ", "! ", ".\n"):
        idx = chunk.rfind(sep)
        if idx >= int(len(chunk) * 0.45):
            candidate = chunk[: idx + len(sep)].strip()
            if len(candidate.split()) >= max(6, int(max_words * 0.28)):
                return _drop_incomplete_sentence_after_last_period(candidate)
    trimmed = chunk.rstrip(",;:").strip()
    trimmed = _drop_incomplete_sentence_after_last_period(trimmed)
    if not trimmed.endswith((".", "!", "?")):
        trimmed += "…"
    return trimmed


def _trim_block_for_merge_append(block: str, max_chars: int = 480) -> str:
    """Each merged frame contributes at most a short addendum; details belong in specification_lines."""
    return _trim_prose_at_limit(block.strip(), max_chars)


def _prose_substantially_new(existing: str, new_block: str) -> bool:
    """True when ``new_block`` is worth appending (not a near-duplicate of ``existing``)."""
    e, n = (existing or "").strip().lower(), (new_block or "").strip().lower()
    if not n or len(n) < 25:
        return False
    if n in e:
        return False
    head = n[:280]
    if head and head in e:
        return False
    # Rephrased repeat paragraphs (same objects/adjectives) share most long tokens.
    new_words = set(re.findall(r"[a-z]{4,}", n))
    old_words = set(re.findall(r"[a-z]{4,}", e))
    if len(new_words) >= 14:
        inter = len(new_words & old_words)
        if inter / len(new_words) >= 0.58:
            return False
    return True


_DETAIL_ONLY_TITLE_RE = re.compile(
    r"(?i)\b(detail with|close[- ]?up|macro view|partial view|fragment of|showing the)\b"
)


def _is_low_value_detail_title(title: str) -> bool:
    """Macro/feature titles without a maker — poor CTBids search keywords."""
    t = clean_listing_text(title).strip()
    if not t:
        return False
    if _DETAIL_ONLY_TITLE_RE.search(t):
        return True
    low = t.lower()
    if " with " in low and not re.search(r"(?i)\b(men's|women's|st\.?\s*john|levi|gap|chico|lands' end)\b", t):
        feature_tail = low.split(" with ", 1)[-1]
        if any(
            k in feature_tail
            for k in ("zippered", "zipper", "pocket", "cuff", "collar", "hem", "closure", "welt")
        ):
            return True
    return False


def _spec_value_for_label(specification_lines: list[str], label_key: str) -> str:
    for line in specification_lines:
        parts = _spec_label_value_parts(line)
        if parts and parts[0] == label_key:
            return clean_listing_text(parts[1]).strip()
    return ""


def _garment_phrase_from_title(title: str) -> str:
    low = re.sub(r"(?i)\b(detail with|close[- ]?up of|partial|view of|showing|woven|matte|secure)\b", " ", title)
    low = clean_listing_text(low).lower()
    phrases = (
        ("zip-up jacket", "Zip-Up Jacket"),
        ("utility jacket", "Utility Jacket"),
        ("windbreaker", "Windbreaker"),
        ("harrington jacket", "Harrington Jacket"),
        ("jacket", "Jacket"),
        ("coat", "Coat"),
        ("blazer", "Blazer"),
        ("shirt", "Shirt"),
        ("dress", "Dress"),
        ("pants", "Pants"),
        ("trousers", "Trousers"),
    )
    for needle, label in phrases:
        if needle in low:
            return label
    return ""


def _ensure_branded_auction_title(
    title: str,
    brands: list[str],
    specification_lines: list[str],
) -> str:
    """
    When a resolved maker exists but the title is a generic detail line, rebuild a shopper-facing title.
    """
    t = clean_listing_text(title).strip()
    primary = brands[0] if brands else ""
    if not primary:
        inferred = infer_brands_from_label_context([], specification_lines, t, "")
        primary = inferred[0] if inferred else ""
    if not primary:
        return t
    if brand_appears_in_prose(primary, t) and not _is_low_value_detail_title(t):
        if t.lower().startswith(primary.lower()[: min(10, len(primary))]):
            return t
        return f"{primary} {t}"[:500]

    material = _spec_value_for_label(specification_lines, "material")
    style = _spec_value_for_label(specification_lines, "style").split("/")[0].strip()
    era = _spec_value_for_label(specification_lines, "era")
    garment = _garment_phrase_from_title(t)

    parts: list[str] = [primary]
    era_low = era.lower()
    if era and era_low not in ("contemporary", "modern", "current"):
        parts.append(era)
    if material and material.lower() not in ("", "unknown"):
        parts.append(material)
    if garment:
        parts.append(garment)
    elif style:
        parts.append(style)
    else:
        parts.append("Apparel")
    out = " ".join(p for p in parts if p).strip()
    return out[:500] if out else t


def _merge_auction_title_for_lot(
    old: str,
    new: str,
    specification_lines: list[str],
    *,
    brands: list[str] | None = None,
) -> str:
    """
    When later frames describe a broader inventory than the first, prefer a more inclusive title.
    First frame often latches onto the most ornate single object (e.g. dragon stand) and omits the rest.
    Keeps a branded title when a later frame only adds a macro/detail line without the maker.
    """
    o, n = (old or "").strip(), (new or "").strip()
    if not n:
        return o or ""
    if not o:
        return n
    merged_brands = normalize_brand_list((brands or []) + _brands_from_spec_lines(specification_lines))
    primary = merged_brands[0] if merged_brands else ""
    if primary:
        old_has_brand = brand_appears_in_prose(primary, o)
        new_has_brand = brand_appears_in_prose(primary, n)
        new_is_detail = _is_low_value_detail_title(n)
        old_is_detail = _is_low_value_detail_title(o)
        if old_has_brand and (new_is_detail or not new_has_brand):
            return o
        if new_has_brand and old_is_detail:
            return n
        if new_is_detail and not old_is_detail:
            return o
    multi_kw = (
        "collection",
        "group",
        "mixed",
        "pieces",
        "piece ",
        "-piece",
        "set",
        "lot includes",
        "including",
        "assorted",
        "various",
        "brass lot",
        "metal lot",
        "group lot",
    )
    old_m = any(k in o.lower() for k in multi_kw)
    new_m = any(k in n.lower() for k in multi_kw)
    if new_m and not old_m:
        return n
    spec_blob = " ".join(specification_lines).lower()
    if spec_blob.count("components:") >= 1 and len(n) > len(o) + 12:
        return n
    if n.count(",") >= o.count(",") + 1 and len(n) > len(o):
        return n
    if len(n) > len(o) + 45:
        return n
    if _title_narrows_multi_component_lot(o, specification_lines) and not _title_narrows_multi_component_lot(
        n, specification_lines
    ):
        return n
    return o


def _listing_should_have_multi_piece_cue(specification_lines: list[str]) -> bool:
    comp = _components_spec_text(specification_lines) or ""
    return bool(comp and _components_indicate_multi_piece(comp)) or _spec_lines_indicate_multi_textile_piece(
        specification_lines
    )


def _ensure_multi_piece_description(description: str, specification_lines: list[str]) -> str:
    if not _listing_should_have_multi_piece_cue(specification_lines):
        return description
    comp = _components_spec_text(specification_lines) or ""
    desc = clean_listing_text(description)
    low = desc.lower()
    if (
        "this lot includes multiple pieces" in low
        or "includes multiple pieces" in low
        or "see components" in low
        or "several distinct pieces" in low
        or "several vintage lace" in low
        or (comp and comp.lower() in low)
    ):
        return desc
    # Keep prose short — inventory detail belongs in **specification_lines**, not a second essay here.
    extra = "Several distinct pieces in this lot—refer to the photos for layout, size, and condition."
    if not desc:
        return extra
    joiner = " " if desc.endswith((".", "!", "?")) else ". "
    return f"{desc}{joiner}{extra}"


def _strip_noted_brand_clauses(text: str) -> str:
    """Remove every ``Noted brand: …`` / ``Noted brands: …`` clause (models often chain several with periods)."""
    s = text.strip()
    while True:
        m = re.search(r"(?i)\bnoted brands?\s*:\s*", s)
        if not m:
            break
        start = m.start()
        rest = s[m.end() :]
        endm = re.search(r"[.!?](?:\s+|$)", rest)
        if endm:
            s = (s[:start] + rest[endm.end() :]).strip()
        else:
            s = s[:start].strip()
            break
    return s


def sanitize_description_unknowns(text: str) -> str:
    """Remove placeholder brand notes and placeholder clauses from prose."""
    s = clean_listing_text(text)
    if not s:
        return s
    s = _strip_noted_brand_clauses(s)
    s = clean_listing_text(s)
    # Remove generic placeholder clauses that leak into prose.
    s = re.sub(r"(?i)\bnoted brand:\s*(unknown|unbranded|unmarked|none|n/?a|null)\.?", "", s)
    s = re.sub(r"(?i)\bbrand(?:\(s\))?\s*(?:is|are|:)?\s*(unknown|unbranded|unmarked|none|n/?a|null)\.?", "", s)
    s = re.sub(r"(?i)\b(?:unknown|unbranded|unmarked|none|n/?a|null)\s+brand\b\.?", "", s)
    s = re.sub(r"(?i)\b(?:unknown|unbranded|unmarked|none|n/?a|null)\b(?=\s*(?:,|/|;|\.|$))", "", s)
    # Normalize punctuation/spacing after removals.
    s = re.sub(r"\s*([,;/])\s*", r"\1 ", s)
    s = re.sub(r"\s+\.", ".", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,;/")
    return s


def _gender_evidence_blob(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "ProfessionalDescription",
        "Description",
        "professional_description",
        "Condition",
        "condition_notes",
        "AuctionTitle",
        "Title",
        "item_name",
    ):
        val = payload.get(key)
        if val is not None:
            parts.append(str(val))
    for key in ("SpecificationLines", "specification_lines", "LabelsAndTags", "labels_and_tags"):
        vals = payload.get(key)
        if isinstance(vals, list):
            parts.extend(str(x) for x in vals if x is not None)
        elif vals is not None:
            parts.append(str(vals))
    return " ".join(parts)


def _sanitize_gendered_title(title: str, payload: dict[str, Any]) -> str:
    """
    Avoid incorrect gender labels in auction titles when evidence is weak.
    If "men's" or "women's" appears in title but not in the broader model evidence,
    remove only the gender token and keep the rest of the title.
    """
    out = title.strip()
    if not out:
        return out
    blob = _gender_evidence_blob(payload)
    has_mens_evidence = _MENS_EVIDENCE_RE.search(blob) is not None
    has_womens_evidence = _WOMENS_EVIDENCE_RE.search(blob) is not None
    if _MENS_IN_TITLE_RE.search(out) and not has_mens_evidence:
        out = _MENS_IN_TITLE_RE.sub("", out)
    if _WOMENS_IN_TITLE_RE.search(out) and not has_womens_evidence:
        out = _WOMENS_IN_TITLE_RE.sub("", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" -,:")
    return out


def tape_marking_canonical(raw: str | None) -> str | None:
    """
    Normalize a handwritten colored estate-tape lot code for storage and comparison.

    Supports:
    - Digits-only after ``#`` or plain digit runs (2–6 digits), via ``normalize_tape_sku_input``.
    - Letter prefix + digits on tape (e.g. ``SR006``, ``SA004``, ``SA 003``) → uppercase letters + digits, no space.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("null", "none", "n/a", "unknown", "undefined", ""):
        return None
    compact = re.sub(r"\s+", "", s)
    if "#" in compact:
        tail = compact.rsplit("#", 1)[-1]
        m = re.search(r"([A-Za-z]{1,4})(\d{2,6})", tail)
        if m:
            return f"{m.group(1).upper()}{m.group(2)}"
        return normalize_tape_sku_input(tail)
    m = re.match(r"^([A-Za-z]{1,4})(\d{2,6})$", compact)
    if m:
        return f"{m.group(1).upper()}{m.group(2)}"
    return normalize_tape_sku_input(s)


def tape_sku_opens_new_lot(sku: Any) -> bool:
    return tape_marking_canonical(str(sku).strip() if sku is not None else None) is not None


def tape_sku_same_marking(a: str | None, b: str | None) -> bool:
    """True when two tape sku strings refer to the same marking (letters + digits or digits-only)."""
    ca = tape_marking_canonical(a)
    cb = tape_marking_canonical(b)
    if ca is not None and cb is not None and ca == cb:
        return True
    da = sku_digits(a)
    db = sku_digits(b)
    # Same numeric tail when one frame is digits-only and another is letter+digit (e.g. 006 vs SR006).
    return len(da) >= 2 and len(db) >= 2 and da == db


def sku_digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or "").strip())


def folder_token_as_inherited_sku(folder_batch_key: str | None) -> str | None:
    """
    When the first path segment under a job upload looks like a lot folder (035, 006, 1420), use it as SKU digits.

    Only 2–6 plain digits are accepted (no ``Lot-`` prefix, etc.).
    """
    if folder_batch_key is None:
        return None
    t = str(folder_batch_key).strip()
    if not t:
        return None
    if re.fullmatch(r"\d{2,6}", t):
        return t
    return None


def payload_suggests_sku_review(payload: dict[str, Any]) -> bool:
    """True when the model omitted a tape SKU or was unconfident — worth human verification after the job."""
    conf = str(payload.get("SkuConfidence") or payload.get("sku_read_confidence") or "").strip().lower()
    raw = payload.get("SKU")
    mark = tape_marking_canonical(str(raw).strip() if raw is not None and str(raw).strip() else None)
    sku_clean = sku_digits(raw) if raw not in (None, "") else None
    if not mark and not sku_clean:
        return True
    return conf in ("low", "unsure")


def normalize_tape_sku_input(raw: str | None) -> str | None:
    """
    Prefer digits after the last ``#`` (estate-tape convention). Take the first 2–6 digit run after ``#``,
    or the leading 2–6 digit run when there is no ``#``, without concatenating unrelated long strings.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("null", "none", ""):
        return None
    if "#" in s:
        tail = s.rsplit("#", 1)[-1]
        m = re.search(r"(\d{2,6})", tail)
        d = m.group(1) if m else re.sub(r"\D", "", tail)
    else:
        d = re.sub(r"\D", "", s)
        m = re.match(r"^(\d{2,6})", d)
        if m:
            d = m.group(1)
    return d or None


# Often hallucinated from zipper pulls / generic hardware — not sewn-in garment labels.
_HARDWARE_MISREAD_BRANDS: frozenset[str] = frozenset(
    {
        "prada",
        "gucci",
        "louis vuitton",
        "chanel",
        "hermès",
        "hermes",
        "versace",
        "dolce & gabbana",
        "dior",
        "fendi",
        "burberry",
        "armani",
        "balenciaga",
        "london fog",
    }
)

_GARMENT_TAG_SIGNAL_RE = re.compile(
    r"(?i)\b(authentic|established\s+\d{4}|made in|hecho en|size\s*:|"
    r"rn\s*#|wpl\s*\d|traditional fit|neck label|care label|woven label|"
    r"printed label|interior label|size tag)\b"
)

_APPAREL_GARMENT_RE = re.compile(
    r"(?i)\b(jacket|coat|windbreaker|blazer|shirt|dress|blouse|pants|trousers|"
    r"skirt|vest|hoodie|sweater|cardigan|garment|apparel)\b"
)

_COLLECTIBLE_CONDITION_RE = re.compile(r"(?i)\b(oxidation|tarnish|verdigris|patina layers)\b")


def _lot_listing_blob(
    *,
    labels_and_tags: list[str],
    specification_lines: list[str],
    auction_title: str,
    professional_description: str,
) -> str:
    return clean_listing_text(
        " ".join(
            [
                auction_title,
                professional_description,
                " ".join(labels_and_tags),
                " ".join(specification_lines),
            ]
        )
    ).lower()


def _brand_token_key(brand: str) -> str:
    return re.sub(r"\s+", " ", clean_listing_text(brand)).strip().lower()


def _brand_appears_in_blob(brand: str, blob: str) -> bool:
    if not brand or not blob:
        return False
    key = _brand_token_key(brand)
    if key and key in blob:
        return True
    if "st. john" in key or "st john" in key:
        return "st. john" in blob or "st john" in blob
    return brand_appears_in_prose(brand, blob)


def _is_hardware_misread_brand(brand: str, blob: str) -> bool:
    key = _brand_token_key(brand)
    if key not in _HARDWARE_MISREAD_BRANDS:
        return False
    return not _brand_appears_in_blob(brand, blob)


def _brands_from_spec_lines(specification_lines: list[str]) -> list[str]:
    found: list[str] = []
    for line in specification_lines:
        parts = _spec_label_value_parts(line)
        if parts and parts[0] in _BRAND_MERGE_LABEL_KEYS:
            found.extend(_unique_semicolon_phrases([parts[1]]))
    return normalize_brand_list(found)


_AUTHENTIC_MAKER_RE = re.compile(
    r"(?i)\bAUTHENTIC\s+([A-Za-z][A-Za-z0-9'&.\-\s]{2,56}?)"
    r"(?:\s+(?:ESTABLISHED|SINCE|TRADITIONAL|TM|®)\b|\s*$|[,.;])"
)

# Common estate-sale apparel makers (label text often partial).
_KNOWN_LABEL_BRAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bst\.?\s*john'?s?\s*bay\b"), "St. John's Bay"),
    (re.compile(r"(?i)\bchico'?s\b"), "Chico's"),
    (re.compile(r"(?i)\btalbots\b"), "Talbots"),
    (re.compile(r"(?i)\bliz\s+claiborne\b"), "Liz Claiborne"),
    (re.compile(r"(?i)\banne\s+klein\b"), "Anne Klein"),
    (re.compile(r"(?i)\blands'?s?\s+end\b"), "Lands' End"),
    (re.compile(r"(?i)\bcolumbia\b"), "Columbia"),
    (re.compile(r"(?i)\bcarhartt\b"), "Carhartt"),
    (re.compile(r"(?i)\beddie\s+bauer\b"), "Eddie Bauer"),
    (re.compile(r"(?i)\blondon\s+fog\b"), "London Fog"),
    (re.compile(r"(?i)\blevi'?s\b"), "Levi's"),
    (re.compile(r"(?i)\bspartina\s+449\b"), "Spartina 449"),
    (re.compile(r"(?i)\btravelers\b"), "Travelers"),
    (re.compile(r"(?i)\bcoldwater\s+creek\b"), "Coldwater Creek"),
    (re.compile(r"(?i)\btribal\b"), "Tribal"),
    (re.compile(r"(?i)\balfred\s+dunner\b"), "Alfred Dunner"),
    (re.compile(r"(?i)\bwhite\s+mountain\b"), "White Mountain"),
)

# Apparel garment tokens for collapsing per-photo ``Item n:`` rows across merged frames.
_APPAREL_GARMENT_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(cardigan|sweater|knit\s+top|knit\s+layer)\b"), "cardigan"),
    (re.compile(r"(?i)\b(jacket|coat|blazer|utility\s+jacket|trucker)\b"), "jacket"),
    (re.compile(r"(?i)\b(trouser|pants?|slacks)\b"), "trousers"),
    (re.compile(r"(?i)\b(wedge|mule|shoe|sandal|heel|footwear)\b"), "shoes"),
    (re.compile(r"(?i)\b(shirt|blouse|top)\b"), "top"),
)

_FRAME_SIZE_TAG_RE = re.compile(
    r"(?i)^\s*size\s*/\s*tags?\s*(?:\([^)]{0,80}\))?\s*:\s*(.+)$"
)


def _normalize_inferred_brand_name(raw: str) -> str:
    s = clean_listing_text(raw).strip(" .,-;")
    s = re.sub(r"(?i)\s+(?:established|since|traditional|fit|tm|®).*$", "", s).strip()
    if len(s) < 3 or _placeholder_value(s):
        return ""
    return s


def infer_brands_from_label_context(
    labels_and_tags: list[str],
    specification_lines: list[str],
    auction_title: str = "",
    professional_description: str = "",
) -> list[str]:
    """
    Recover maker names Gemini left in ``labels_and_tags`` but omitted from ``brand`` / ``Brands``.
    """
    blob = clean_listing_text(
        " ".join(
            [
                " ".join(labels_and_tags),
                " ".join(specification_lines),
                auction_title,
                professional_description,
            ]
        )
    )
    if not blob:
        return []

    found: list[str] = []
    for pat, canonical in _KNOWN_LABEL_BRAND_PATTERNS:
        if pat.search(blob):
            found.append(canonical)
    if found:
        return normalize_brand_list(found)
    for m in _AUTHENTIC_MAKER_RE.finditer(blob):
        name = _normalize_inferred_brand_name(m.group(1))
        if name:
            found.append(name)
    return normalize_brand_list(found)


def _pick_primary_brand(candidates: list[str], auction_title: str, blob: str) -> str:
    if not candidates:
        return ""
    for c in candidates:
        if brand_appears_in_prose(c, auction_title):
            return c
    evidenced = [c for c in candidates if _brand_appears_in_blob(c, blob)]
    if len(evidenced) == 1:
        return evidenced[0]
    if evidenced:
        return max(evidenced, key=len)
    return candidates[0]


def resolve_lot_brands(
    brands: list[str],
    labels_and_tags: list[str],
    specification_lines: list[str],
    auction_title: str,
    professional_description: str,
) -> tuple[list[str], bool]:
    """
    Label-first brand resolution: drop hardware-only luxury guesses when garment tag evidence exists.
    Returns at most one primary brand and whether staff should review (conflict was dropped).
    """
    merged = normalize_brand_list(
        brands
        + _brands_from_spec_lines(specification_lines)
        + infer_brands_from_label_context(
            labels_and_tags,
            specification_lines,
            auction_title,
            professional_description,
        )
    )
    if not merged:
        return [], False

    blob = _lot_listing_blob(
        labels_and_tags=labels_and_tags,
        specification_lines=specification_lines,
        auction_title=auction_title,
        professional_description=professional_description,
    )
    garment_context = bool(_GARMENT_TAG_SIGNAL_RE.search(blob)) or bool(_APPAREL_GARMENT_RE.search(blob))

    filtered = (
        [b for b in merged if not (_is_hardware_misread_brand(b, blob) and garment_context)]
        if garment_context
        else list(merged)
    )
    dropped = len(filtered) < len(merged)

    if len(filtered) <= 1:
        return filtered, dropped

    primary = _pick_primary_brand(filtered, auction_title, blob)
    return ([primary] if primary else []), True


def sync_brand_spec_lines_to_resolved_brands(lines: list[str], brands: list[str]) -> list[str]:
    """Replace merged ``Brand:`` rows with a single resolved maker."""
    out: list[str] = []
    for line in lines:
        parts = _spec_label_value_parts(line)
        if parts and parts[0] in _BRAND_MERGE_LABEL_KEYS:
            continue
        out.append(line)
    if brands:
        out.append(f"Brand: {brands[0]}")
    return consolidate_brand_and_marking_spec_lines(out)


def _prose_paragraphs_similar(a: str, b: str) -> bool:
    al, bl = a.lower().strip(), b.lower().strip()
    if not al or not bl:
        return False
    if al in bl or bl in al:
        return True
    aw = set(re.findall(r"[a-z]{5,}", al))
    bw = set(re.findall(r"[a-z]{5,}", bl))
    if len(aw) >= 8 and len(bw) >= 8:
        return len(aw & bw) / max(len(aw), 1) >= 0.55
    return False


def _drop_truncated_prose_tail(text: str) -> str:
    t = _drop_incomplete_sentence_after_last_period(text)
    t = re.sub(r"(?i)\s+this classic(?:\s+st\.?)?\s*$", "", t).strip()
    return t


def _collapse_repeated_prose_paragraphs(text: str) -> str:
    """Multi-frame merge often repeats the same opening paragraph — keep one complete overview."""
    t = clean_listing_text(text).strip()
    if not t:
        return t
    parts = [p.strip() for p in re.split(r"\n\n+", t) if p.strip()]
    if len(parts) <= 1:
        return _drop_truncated_prose_tail(t)
    kept: list[str] = []
    for p in parts:
        if any(_prose_paragraphs_similar(p, k) for k in kept):
            continue
        kept.append(p)
    if not kept:
        kept = [parts[0]]
    best = max(kept, key=lambda p: (len(p.split()), len(p)))
    return _drop_truncated_prose_tail(best)


def _scrub_misapplied_collectible_specs_for_apparel(lines: list[str], blob: str) -> list[str]:
    """Drop coin/metal condition language from apparel lots (common per-frame bleed)."""
    if not _APPAREL_GARMENT_RE.search(blob):
        return lines
    out: list[str] = []
    for line in lines:
        parts = _spec_label_value_parts(line)
        lk = parts[0] if parts else ""
        if lk == "conditionnote" and _COLLECTIBLE_CONDITION_RE.search(line):
            continue
        if lk == "components" and re.search(r"(?i)\bslip pocket\b", line) and "interior" in line.lower():
            continue
        out.append(line)
    return out


def merge_unique_case_insensitive(base: list[str], incoming: list[str]) -> tuple[list[str], list[str]]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in base:
        low = item.lower()
        if low not in seen:
            seen.add(low)
            merged.append(item)
    added: list[str] = []
    for item in incoming:
        low = item.lower()
        if low not in seen:
            seen.add(low)
            merged.append(item)
            added.append(item)
    return merged, added


def move_unmentioned_brands_to_specs(
    brands: list[str],
    professional_description: str,
    specification_lines: list[str],
) -> None:
    """
    For each known maker in ``brands[]`` that is not already named in the description or an existing spec line,
    append one ``Brand: …`` specification row. Call after ``sanitize_description_unknowns`` so prose stays clean.
    """
    if not brands:
        return
    desc = professional_description
    for b in brands:
        if not b:
            continue
        if brand_appears_in_prose(b, desc):
            continue
        if any(brand_appears_in_prose(b, clean_listing_text(sl)) for sl in specification_lines):
            continue
        specification_lines.append(f"Brand: {b}")


def brand_appears_in_prose(brand: str, prose: str) -> bool:
    if not brand or not prose:
        return False
    if f"**{brand}**".lower() in prose.lower():
        return True
    try:
        return re.search(r"\b" + re.escape(brand) + r"\b", prose, re.I) is not None
    except re.error:
        return False


def weave_brands_into_description(brands: list[str], description: str) -> str:
    """
    Keep listing prose clean for CT Seller / dashboard.

    Brands and makers are carried in ``brands[]`` and optional spec lines; do **not** append
    ``Noted brand:`` sentences to ``professional_description`` (users found them noisy / duplicative).
    """
    _ = brands
    return sanitize_description_unknowns(description)


@dataclass
class LotOutput:
    sku: str | None
    auction_title: str
    professional_description: str
    specification_lines: list[str] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)
    labels_and_tags: list[str] = field(default_factory=list)
    condition: str = ""
    starting_price: float | None = None
    photo_count: int = 1
    sku_needs_review: bool = False
    brand_needs_review: bool = False
    lot_boundary_blank_page: bool = False

    def _finalize_listing_quality(self) -> None:
        """Label-first brands, apparel spec cleanup, and single-paragraph prose after merge."""
        blob = _lot_listing_blob(
            labels_and_tags=self.labels_and_tags,
            specification_lines=self.specification_lines,
            auction_title=self.auction_title,
            professional_description=self.professional_description,
        )
        self.professional_description = _collapse_repeated_prose_paragraphs(self.professional_description)
        self.specification_lines = _scrub_misapplied_collectible_specs_for_apparel(
            self.specification_lines, blob
        )
        label_inferred = infer_brands_from_label_context(
            self.labels_and_tags,
            self.specification_lines,
            self.auction_title,
            self.professional_description,
        )
        if label_inferred:
            self.brands = normalize_brand_list(self.brands + label_inferred)
        resolved, brand_review = resolve_lot_brands(
            self.brands,
            self.labels_and_tags,
            self.specification_lines,
            self.auction_title,
            self.professional_description,
        )
        if brand_review:
            self.brand_needs_review = True
            self.sku_needs_review = True
        self.brands = resolved
        self.specification_lines = sync_brand_spec_lines_to_resolved_brands(
            self.specification_lines, self.brands
        )
        self.professional_description = sanitize_description_unknowns(self.professional_description)
        move_unmentioned_brands_to_specs(self.brands, self.professional_description, self.specification_lines)
        self.specification_lines = dedupe_specification_lines_preserve_order(self.specification_lines)
        self.specification_lines = consolidate_frame_size_tag_lines(
            self.specification_lines, self.labels_and_tags
        )
        self.auction_title = _broaden_auction_title_for_components(self.auction_title, self.specification_lines)
        self.auction_title = _broaden_auction_title_for_numismatic_multi_year(
            self.auction_title, self.specification_lines
        )
        self.auction_title = _ensure_branded_auction_title(
            self.auction_title, self.brands, self.specification_lines
        )
        self.professional_description = _drop_prose_sentences_conflicting_with_components(
            self.professional_description, self.specification_lines
        )
        self.professional_description = _trim_prose_to_max_words(
            self.professional_description, max(1, PROFESSIONAL_DESCRIPTION_MAX_WORDS - 8)
        )
        self.professional_description = _ensure_multi_piece_description(
            self.professional_description, self.specification_lines
        )
        self.professional_description = _trim_prose_to_max_words(
            self.professional_description, PROFESSIONAL_DESCRIPTION_MAX_WORDS
        )
        self.professional_description = _trim_prose_at_limit(
            self.professional_description, MERGED_PRO_DESCRIPTION_MAX_CHARS
        )

    @classmethod
    def from_model(cls, payload: dict[str, Any]) -> "LotOutput":
        # Accept canonical ``inventory_table_entry_to_lot_payload`` keys *or* raw REST/SDK JSON
        # (snake_case) when ``_analyze_photo_rest_from_bytes`` returns ``coerced`` without validation.
        price_raw = payload.get("StartingPrice")
        if price_raw is None:
            price_raw = payload.get("starting_price_usd")
        price = None
        if price_raw is not None and str(price_raw).strip():
            try:
                price = float(str(price_raw).replace("$", "").strip())
            except ValueError:
                price = None
        raw_sku = payload.get("SKU")
        if raw_sku is None:
            raw_sku = payload.get("sku")
        sku_normalized = tape_marking_canonical(
            str(raw_sku).strip() if raw_sku is not None and str(raw_sku).strip() else None
        )
        title = (
            str(
                payload.get("AuctionTitle")
                or payload.get("Title")
                or payload.get("item_name")
                or ""
            ).strip()
        )
        title = _sanitize_gendered_title(title, payload)
        spec_lines = dedupe_specification_lines_preserve_order(
            normalize_listing_lines(
                payload.get("SpecificationLines") or payload.get("specification_lines")
            )
        )
        brands = normalize_brand_list(payload.get("Brands") or payload.get("brand") or payload.get("brands"))
        labels = normalize_listing_lines(payload.get("LabelsAndTags") or payload.get("labels_and_tags"))
        condition = str(payload.get("Condition") or payload.get("condition_notes") or "").strip()
        prof = sanitize_description_unknowns(
            str(
                payload.get("ProfessionalDescription")
                or payload.get("Description")
                or payload.get("professional_description")
                or ""
            )
        )
        lot = cls(
            sku=sku_normalized or None,
            auction_title=title,
            professional_description=prof,
            specification_lines=spec_lines,
            brands=brands,
            labels_and_tags=labels,
            condition=condition,
            starting_price=price,
            sku_needs_review=payload_suggests_sku_review(payload),
            lot_boundary_blank_page=False,
        )
        lot._finalize_listing_quality()
        if payload_suggests_sku_review(payload):
            lot.sku_needs_review = True
        return lot

    def merge_detail(self, detail_payload: dict[str, Any]) -> None:
        detail_brands = normalize_brand_list(
            detail_payload.get("Brands") or detail_payload.get("brand") or detail_payload.get("brands")
        )
        detail_labels = normalize_listing_lines(
            detail_payload.get("LabelsAndTags") or detail_payload.get("labels_and_tags")
        )
        detail_specs = normalize_listing_lines(
            detail_payload.get("SpecificationLines") or detail_payload.get("specification_lines")
        )

        self.brands, added_brands = merge_unique_case_insensitive(self.brands, detail_brands)
        self.labels_and_tags, added_labels = merge_unique_case_insensitive(
            self.labels_and_tags, detail_labels
        )
        self.specification_lines, _ = merge_unique_case_insensitive(
            self.specification_lines, detail_specs
        )
        self.specification_lines = dedupe_specification_lines_preserve_order(self.specification_lines)

        new_prof = sanitize_description_unknowns(
            str(
                detail_payload.get("ProfessionalDescription")
                or detail_payload.get("Description")
                or detail_payload.get("professional_description")
                or ""
            )
        ).strip()
        if new_prof:
            new_prof = _trim_block_for_merge_append(new_prof)
        if new_prof and _prose_substantially_new(self.professional_description, new_prof):
            cur = (self.professional_description or "").strip()
            joined = f"{cur}\n\n{new_prof}".strip()
            self.professional_description = _trim_prose_at_limit(joined, MERGED_PRO_DESCRIPTION_MAX_CHARS)

        new_title_raw = (
            str(
                detail_payload.get("AuctionTitle")
                or detail_payload.get("Title")
                or detail_payload.get("item_name")
                or ""
            ).strip()
        )
        if new_title_raw:
            merged = _merge_auction_title_for_lot(
                self.auction_title,
                new_title_raw,
                self.specification_lines,
                brands=self.brands,
            )
            self.auction_title = _sanitize_gendered_title(merged, detail_payload)

        self.photo_count += 1
        self.professional_description = _synthesize_numismatic_multi_cent_prose(
            self.professional_description, self.specification_lines, self.photo_count
        )
        self.professional_description = _synthesize_textile_measurement_lot_prose(
            self.professional_description, self.specification_lines, self.photo_count
        )
        self._finalize_listing_quality()
        if payload_suggests_sku_review(detail_payload):
            self.sku_needs_review = True

