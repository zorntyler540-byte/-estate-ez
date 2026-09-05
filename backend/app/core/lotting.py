from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

# Field labels that appear on printed/handwritten estate tags. Several of these leaking into an
# auction title means the model transcribed the tag form instead of composing a listing title.
_LISTING_TAG_LABELS = frozenset(
    {"no", "size", "price", "style", "color", "colour", "qty", "quantity", "cost", "mfg", "sku", "cat", "dept", "pcs"}
)
_GROUPING_PLACEHOLDER_RE = re.compile(r"^\s*estate grouping\s*(\(\s*\d+\s*items?\s*\))?\s*$", re.I)
_PLACEHOLDER_LISTING_TITLES = frozenset(
    {"", "estate lot", "estate grouping", "estate catalog item", "estate item"}
)


def listing_quality_issue(
    title: str | None,
    description: str | None = None,
    specification_lines: list[str] | None = None,
) -> str | None:
    """
    Heuristic quality gate for a finished listing. Returns a short reason code when the listing
    looks unusable (placeholder or garbled), else None. Cheap + deterministic so it can run at
    read time to flag both freshly-cataloged and already-stored lots for review.
    """
    t = (title or "").strip()
    d = (description or "").strip()
    tl = t.lower()

    if (
        not t
        or tl in _PLACEHOLDER_LISTING_TITLES
        or tl.startswith("estate item (")
        or tl.startswith("estate item #")
        or _GROUPING_PLACEHOLDER_RE.match(t)
    ):
        return "placeholder_title"

    # A real listing should have real prose; the pipeline's generic grouping fallback is not usable.
    if not d or _GROUPING_PLACEHOLDER_RE.match(d):
        return "placeholder_description"

    # Garbled title: raw tag OCR (field labels) dumped into the title. Repeated labels or several
    # distinct ones is a strong signal (a normal title won't say "STYLE ... STYLE ... PRICE ... PRICE").
    words = re.findall(r"[A-Za-z]+", t)
    label_counts = Counter(w.lower() for w in words if w.lower() in _LISTING_TAG_LABELS)
    if any(c >= 2 for c in label_counts.values()) or sum(label_counts.values()) >= 4:
        return "garbled_title"

    return None

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


CONDITION_NOTE_MAX_CHARS = 320


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
        f = re.sub(r"\.{2,}$", "", f).strip(" .;")
        if len(f) < 10 or re.fullmatch(r"\.+", f) or re.fullmatch(r"[A-Za-z]+", f):
            continue
        key = f.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(f)
    return "; ".join(parts[:5]).strip(" ;")


def _drop_incomplete_trailing_condition_clause(body: str) -> str:
    """Remove dangling stub clauses after merge/scrub (e.g. ``Heavy`` or ``Heavy....``)."""
    parts = [
        clean_listing_text(p).strip(" .;")
        for p in body.split(";")
        if clean_listing_text(p).strip(" .;")
    ]
    while parts:
        last = parts[-1]
        if len(last) < 10 or re.fullmatch(r"[A-Za-z]+", last) or re.search(r"\.{2,}$", last):
            parts.pop()
            continue
        break
    return "; ".join(parts).strip(" ;")


def _trim_condition_note_body(text: str, max_chars: int = CONDITION_NOTE_MAX_CHARS) -> str:
    """Trim at semicolon clause boundaries when possible; avoid mid-clause word chops."""
    t = clean_listing_text(text).strip().strip(" .;")
    if len(t) <= max_chars:
        return t
    parts = [
        clean_listing_text(p).strip(" .;")
        for p in t.split(";")
        if clean_listing_text(p).strip(" .;")
    ]
    if len(parts) > 1:
        kept: list[str] = []
        for part in parts:
            candidate = "; ".join(kept + [part]) if kept else part
            if len(candidate) <= max_chars:
                kept.append(part)
            else:
                break
        if kept:
            return "; ".join(kept).rstrip(" ,;.")
    return _trim_prose_at_limit(t, max_chars).rstrip(" ,;.…")


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
    if len(parts) < 2:
        return s
    pub_kw = (
        "page",
        "cover",
        "spine",
        "leather",
        "paper",
        "toning",
        "binding",
        "foxing",
        "publication",
        "book",
    )
    if len(parts) >= 2 and sum(1 for p in parts if any(k in p.lower() for k in pub_kw)) >= 2:
        return "Minor page toning and cover wear consistent with age"
    if len(parts) < 3:
        return s
    wear_kw = ("oxidation", "tarnish", "patina", "wear", "vintage", "age", "surface", "moderate", "light", "honest")
    if all(any(k in p.lower() for k in wear_kw) for p in parts):
        metal_kw = ("oxidation", "tarnish", "patina", "silver", "brass", "copper", "metal")
        if any(any(k in p.lower() for k in metal_kw) for p in parts):
            return "Surface oxidation and tarnish consistent with age"
        return "Wear consistent with age"
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
    body = _drop_incomplete_trailing_condition_clause(body)
    if len(body) > CONDITION_NOTE_MAX_CHARS:
        body = _trim_condition_note_body(body, CONDITION_NOTE_MAX_CHARS)
    body = _drop_incomplete_trailing_condition_clause(body)
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
    **Item** lines (or legacy **Components:**), so multi-piece prose matches the actual lot inventory.
    """
    comp = _inventory_text_from_specification_lines(specification_lines)
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
        return f"{mat} grouping with the pieces listed—a warm display set."
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
    if re.search(r"(?i)\b(left|right)\s+shoe\b", low):
        return "footwear|shoe_fragment"
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
    if not garment and re.search(
        r"(?i)\b(utility\s+jacket|button[- ]?front\s+.*jacket|jacket\s+with)\b", low
    ):
        garment = "jacket"
    if not garment and re.search(r"(?i)\bmatching\s+.*\b(trouser|pants)\b", low):
        garment = "trousers"
    if brand and garment:
        return f"{brand}|{garment}"
    if brand:
        return brand
    if garment:
        return garment
    return re.sub(r"\s+", " ", low[:72]).strip()


def _refine_wardrobe_item_signatures(bodies: list[str]) -> list[str]:
    """
    Map photo-angle duplicates onto one signature per salable unit (pair of shoes = one row).
    """
    raw = [_garment_line_signature(b) for b in bodies]
    branded: dict[str, str] = {}
    for sig in raw:
        if "|" not in sig:
            continue
        garment = sig.split("|", 1)[1]
        if garment == "shoe_fragment":
            continue
        if garment not in branded or len(sig) > len(branded[garment]):
            branded[garment] = sig

    has_clogs = any(
        re.search(r"(?i)\b(clogs?|mules?|wedge|white\s+mountain|perforated)", b)
        for b in bodies
    )
    refined: list[str] = []
    for body, sig in zip(bodies, raw):
        low = body.lower()
        if (
            sig == "footwear|shoe_fragment"
            or sig.endswith("|footwear")
            or re.search(r"(?i)\b(left|right)\s+shoe\b", low)
        ):
            if has_clogs:
                refined.append(
                    branded.get("footwear", "footwear|clogs")
                    if "footwear" in branded
                    else "footwear|clogs"
                )
            else:
                refined.append(branded.get("footwear", "footwear|shoes"))
            continue
        garment = sig.split("|", 1)[1] if "|" in sig else sig
        if "|" not in sig and garment in branded:
            refined.append(branded[garment])
            continue
        if "|" not in sig and garment == "jacket" and "jacket" in branded:
            refined.append(branded["jacket"])
            continue
        if "|" not in sig and garment == "cardigan" and "cardigan" in branded:
            refined.append(branded["cardigan"])
            continue
        if "|" not in sig and garment == "trousers" and "trousers" in branded:
            refined.append(branded["trousers"])
            continue
        refined.append(sig)
    return refined


def _item_body_quality_score(body: str) -> tuple[int, int, int]:
    """Higher sorts first when picking the keeper row for a signature."""
    is_fragment = bool(
        re.search(r"(?i)\b(left|right)\s+shoe\b", body)
        or re.fullmatch(r"(?i)(left|right)\s+shoe[^.]{0,40}", clean_listing_text(body).strip())
    )
    has_brand = bool(
        any(p.search(body) for p, _ in _KNOWN_LABEL_BRAND_PATTERNS)
        or re.search(r"(?i)\b(tribal|alfred\s+dunner|white\s+mountain)\b", body)
    )
    return (0 if is_fragment else 1, 1 if has_brand else 0, -len(body))


def _pick_better_item_body(a: str, b: str) -> str:
    """Prefer branded full-piece rows over angle fragments (e.g. left shoe only)."""
    return a if _item_body_quality_score(a) >= _item_body_quality_score(b) else b


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
    signatures = _refine_wardrobe_item_signatures(bodies)
    by_sig: dict[str, str] = {}
    order: list[str] = []
    for body, sig in zip(bodies, signatures):
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


def _count_item_spec_lines(lines: list[str]) -> int:
    return sum(
        1
        for line in lines
        if _ITEM_LINE_BODY_RE.match(clean_listing_text(line).strip())
    )


def _item_spec_bodies(lines: list[str]) -> list[str]:
    bodies: list[str] = []
    for line in lines:
        m = _ITEM_LINE_BODY_RE.match(clean_listing_text(line).strip())
        if m:
            bodies.append(m.group(1).strip())
    return bodies


def _count_unique_wardrobe_items(lines: list[str]) -> int:
    bodies = _item_spec_bodies(lines)
    if not bodies:
        return 0
    return len(set(_refine_wardrobe_item_signatures(bodies)))


def _spec_lines_indicate_wardrobe_apparel(lines: list[str], blob: str) -> bool:
    """Clothing/shoes lots that should use ``Item n:`` inventory instead of ``Components:``."""
    if _APPAREL_GARMENT_RE.search(blob):
        return True
    item_bodies = [
        _ITEM_LINE_BODY_RE.match(clean_listing_text(line).strip()).group(1)
        for line in lines
        if _ITEM_LINE_BODY_RE.match(clean_listing_text(line).strip())
    ]
    if len(item_bodies) >= 2 and any(
        any(p.search(body) for p, _ in _APPAREL_GARMENT_HINTS) for body in item_bodies
    ):
        return True
    return bool(item_bodies) and _OPEN_TOE_FOOTWEAR_RE.search(blob)


def drop_redundant_apparel_size_lines(lines: list[str], blob: str = "") -> list[str]:
    """
    Wardrobe lots carry sizes on **Item** rows and **Size / tags:** — drop stray per-frame ``Size:`` bullets.
    """
    inventory_blob = blob or " ".join(lines).lower()
    if not _spec_lines_indicate_wardrobe_apparel(lines, inventory_blob):
        return lines
    if _count_item_spec_lines(lines) < 2:
        return lines
    size_count = sum(
        1
        for line in lines
        if re.match(r"(?i)^\s*size\s*:\s*", clean_listing_text(line).strip())
    )
    if size_count == 0:
        return lines
    return [
        line
        for line in lines
        if not re.match(r"(?i)^\s*size\s*:\s*", clean_listing_text(line).strip())
    ]


def drop_components_spec_lines(lines: list[str]) -> list[str]:
    """Listing UI omits **Components:** — inventory belongs in **Item n:** rows only."""
    return [
        line
        for line in lines
        if not re.match(r"(?i)^\s*components\s*:", clean_listing_text(line).strip())
    ]


_STYLE_FINISH_SPEC_LABEL_RE = re.compile(
    r"(?i)^\s*(style|finish|finishes|surface\s+finish|glaze\s+finish|finish\s+type|"
    r"sheen|luster|lustre|glaze|gloss)\s*:"
)


def drop_style_and_finish_spec_lines(lines: list[str]) -> list[str]:
    """Listing UI omits **Style:** / **Finish:** — detail lives in Item rows and description."""
    return [
        line
        for line in lines
        if not _STYLE_FINISH_SPEC_LABEL_RE.match(clean_listing_text(line).strip())
    ]


def drop_components_lines_for_apparel_inventory(
    lines: list[str], blob: str = ""
) -> list[str]:
    """Deprecated alias — all lots drop ``Components:`` via :func:`drop_components_spec_lines`."""
    _ = blob
    return drop_components_spec_lines(lines)


def _split_components_inventory_fragments(components_text: str) -> list[str]:
    """Turn legacy ``Components:`` inventory into short Item-row bodies before the Components row is hidden."""
    text = clean_listing_text(components_text).strip(" ;.")
    if not text:
        return []
    pieces = [p.strip(" ;,.") for p in text.split(";") if p.strip(" ;,.")]
    if len(pieces) <= 1 and re.search(
        r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b[^,]+,\s*",
        text,
        re.I,
    ):
        pieces = [p.strip(" ;,.") for p in text.split(",") if p.strip(" ;,.")]
    if len(pieces) <= 1:
        pieces = [text]
    out: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        body = _short_deliverable_phrase(piece, max_len=140)
        key = body.lower()
        if not body or key in seen:
            continue
        seen.add(key)
        out.append(body)
    return out[:12]


def synthesize_item_lines_from_components_when_missing(lines: list[str]) -> list[str]:
    """Preserve Components-only inventory as ``Item n:`` rows before dropping the legacy Components label."""
    if _count_item_spec_lines(lines) > 0:
        return lines
    comp = _components_spec_text(lines)
    if not comp or not _components_indicate_multi_piece(comp):
        return lines
    bodies = _split_components_inventory_fragments(comp)
    if not bodies:
        return lines
    items = [f"Item {i + 1}: {body}" for i, body in enumerate(bodies)]
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and re.match(r"(?i)^\s*components\s*:", clean_listing_text(line).strip()):
            out.extend(items)
            inserted = True
    if not inserted:
        out.extend(items)
    return out


def _inventory_text_from_specification_lines(specification_lines: list[str]) -> str:
    """Merged inventory for title/prose alignment (Items first, then legacy Components if any)."""
    items = _item_spec_bodies(specification_lines)
    if items:
        return "; ".join(items)
    comp = _components_spec_text(specification_lines)
    if comp:
        return comp
    return " ".join(specification_lines)


def synthesize_wardrobe_item_lines_when_sparse(lines: list[str], blob: str = "") -> list[str]:
    """
    When many photos merged without ``Item n:`` rows, infer one line per distinct garment (e.g. jacket + vest).
    """
    inventory_blob = blob or " ".join(lines).lower()
    if not _spec_lines_indicate_wardrobe_apparel(lines, inventory_blob):
        return lines
    if _count_unique_wardrobe_items(lines) >= 2:
        return lines
    phrases: list[str] = []
    size_m = re.search(r"(?i)\bsize\s*:?\s*(xl|xxl|l|m|s|xs)\b", inventory_blob)
    size_suffix = f", size {size_m.group(1).upper()}" if size_m else ""
    if re.search(r"(?i)\bcolumbia\b", inventory_blob) and re.search(
        r"(?i)\b(jacket|softshell|fleece|outerwear)\b", inventory_blob
    ):
        style = (
            "softshell jacket"
            if "softshell" in inventory_blob
            else "fleece jacket"
            if "fleece" in inventory_blob and "softshell" not in inventory_blob
            else "jacket"
        )
        phrases.append(f"Columbia {style}{size_suffix}")
    elif re.search(r"(?i)\b(jacket|softshell|fleece)\b", inventory_blob):
        phrases.append(f"Outerwear jacket{size_suffix}")
    if re.search(r"(?i)\b(vest|gilet|sleeveless|quilted\s+liner)\b", inventory_blob):
        vest = "green quilted vest" if "quilt" in inventory_blob else "sleeveless vest"
        if "green" in inventory_blob:
            vest = "green quilted vest" if "quilt" in inventory_blob else "green sleeveless vest"
        phrases.append(vest.capitalize())
    if len(phrases) < 2:
        return lines
    other = [
        line
        for line in lines
        if not _ITEM_LINE_BODY_RE.match(clean_listing_text(line).strip())
        and not re.match(r"(?i)^\s*components\s*:", clean_listing_text(line).strip())
    ]
    return other + [f"Item {i + 1}: {p}" for i, p in enumerate(phrases[:4])]


def rebuild_components_from_unique_items(lines: list[str]) -> list[str]:
    """Replace bloated multi-frame ``Components:`` with one line built from collapsed ``Item n:`` rows."""
    blob = " ".join(lines).lower()
    if _spec_lines_indicate_wardrobe_apparel(lines, blob) and _count_item_spec_lines(lines) >= 2:
        return lines
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


_NON_APPAREL_SUPPLY_RE = re.compile(
    r"(?i)\b(sketch\s*pad|sketchbook|watercolor|colored\s+pencil|colour\s+pencil|"
    r"art\s+suppl|prismacolor|moleskine|easel|canvas\s+pad|"
    r"acid[- ]?free|cold\s+press|linen[- ]?bound|expandable\s+inner\s+pocket|"
    r"double[- ]?sided|drawing\s+pencil|ink\s+bottle|marker\s+set)\b"
)

_WARDROBE_ACCESSORY_FOR_SIZE_TAGS_RE = re.compile(
    r"(?i)\b(handbag|purse|wallet|crossbody|tote|satchel|clutch)\b"
)

_SIZE_TAG_TEXT_RE = re.compile(
    r"(?i)\b("
    r"size\s*[:\s]?|petite|\bps\b|\bxl\b|\bxxl\b|\bxs\b|\bl/g\b|\bg/l\b|"
    r"\bwomen'?s\b|\bmen'?s\b|\b\d+\s*[mxlsp]\b|"
    r"made in|fait en|hecho en|ramie|cotton\s*\d|rn\s*#|"
    r"traditional fit|established\s+\d{4}|authentic\s+"
    r")\b"
)

_PRODUCT_TAG_NOISE_RE = re.compile(
    r"(?i)\b(cold\s+press|acid[- ]?free|double[- ]?sided|linen[- ]?bound|"
    r"expandable\s+inner\s+pocket|sketch\s*pad|watercolor|drawing\s+pencil)\b"
)


def lot_accepts_size_tags(
    *,
    labels_and_tags: list[str] | None = None,
    specification_lines: list[str] | None = None,
    auction_title: str = "",
    professional_description: str = "",
) -> bool:
    """Size / tags and ``labels_and_tags`` apply to apparel, footwear, and tagged accessories only."""
    blob = _lot_listing_blob(
        labels_and_tags=list(labels_and_tags or []),
        specification_lines=list(specification_lines or []),
        auction_title=auction_title,
        professional_description=professional_description,
    )
    if _spec_lines_indicate_wardrobe_apparel(list(specification_lines or []), blob):
        return True
    if _NON_APPAREL_SUPPLY_RE.search(blob):
        return False
    if _APPAREL_GARMENT_RE.search(blob):
        return True
    if _WARDROBE_ACCESSORY_FOR_SIZE_TAGS_RE.search(blob) and _GARMENT_TAG_SIGNAL_RE.search(blob):
        return True
    return False


def filter_labels_and_tags_for_lot(
    labels_and_tags: list[str],
    specification_lines: list[str],
    auction_title: str = "",
    professional_description: str = "",
) -> list[str]:
    """Drop size/tag strings for non-garment lots; trim product-marketing OCR on apparel."""
    if not lot_accepts_size_tags(
        labels_and_tags=labels_and_tags,
        specification_lines=specification_lines,
        auction_title=auction_title,
        professional_description=professional_description,
    ):
        return []
    kept: list[str] = []
    for lab in labels_and_tags:
        t = clean_listing_text(lab).strip()
        if not t or len(t) > 120:
            continue
        if _PRODUCT_TAG_NOISE_RE.search(t) and not _SIZE_TAG_TEXT_RE.search(t):
            continue
        if len(t) > 90 and ";" in t and not _SIZE_TAG_TEXT_RE.search(t) and not _GARMENT_TAG_SIGNAL_RE.search(t):
            continue
        kept.append(t)
    return kept


def consolidate_frame_size_tag_lines(
    lines: list[str], labels_and_tags: list[str] | None = None
) -> list[str]:
    """One ``Size / tags:`` line instead of one row per merged photo frame (apparel lots only)."""
    blob = " ".join(lines).lower()
    if labels_and_tags:
        blob = f"{blob} {' '.join(labels_and_tags).lower()}"
    if not lot_accepts_size_tags(
        labels_and_tags=list(labels_and_tags or []),
        specification_lines=lines,
        auction_title="",
        professional_description=blob,
    ):
        return [
            line
            for line in lines
            if not _FRAME_SIZE_TAG_RE.match(clean_listing_text(line).strip())
        ]
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
    if _spec_lines_indicate_wardrobe_apparel(lines, " ".join(lines).lower()):
        return lines
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
    first = synthesize_wardrobe_item_lines_when_sparse(first, " ".join(first).lower())
    first = synthesize_item_lines_from_components_when_missing(first)
    merged = consolidate_brand_and_marking_spec_lines(_collapse_semantic_spec_lines(first))
    merged = consolidate_condition_note_spec_lines(merged)
    merged = _collapse_loose_display_quarter_item_rows(merged)
    merged = sort_specification_lines_canonical(merged)
    merged = renumber_item_spec_lines_sequentially(merged)
    merged = drop_components_spec_lines(merged)
    merged = drop_style_and_finish_spec_lines(merged)
    merged = drop_redundant_apparel_size_lines(merged, " ".join(merged).lower())
    merged = _prune_subject_form_when_item_bullets_present(merged)
    return _retain_minimal_specification_lines(merged)


def _retain_minimal_specification_lines(lines: list[str]) -> list[str]:
    """
    Keep spec lines intentionally minimal for speed and consistency:
    - Brand:
    - Condition note:
    - Size / tags:
    - Dimensions / Measurements:
    - Weight:
    """
    kept: list[str] = []
    for line in lines:
        clean = clean_listing_text(line).strip()
        if not clean:
            continue
        lower = clean.lower()
        if lower.startswith("brand:"):
            kept.append(clean)
            continue
        if lower.startswith("condition note:"):
            kept.append(clean)
            continue
        if re.match(r"(?i)^size\s*/\s*tags?\s*:", clean):
            kept.append(clean)
            continue
        if re.match(r"(?i)^dimensions?\s*:", clean):
            kept.append(clean)
            continue
        if re.match(r"(?i)^measurements?\s*:", clean):
            kept.append(clean)
            continue
        if re.match(r"(?i)^weights?\s*:", clean):
            kept.append(clean)
            continue
    return kept


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
    low = raw.lower()
    if _APPAREL_GARMENT_RE.search(low) and raw.count(",") >= 3:
        if not re.search(r"(?i)\b\d+\s*(?:piece|item|garment)s?\b", raw):
            return False
    nums: list[int] = []
    for m in re.finditer(r"\b(\d+)\b", raw):
        n = int(m.group(1))
        ctx = low[max(0, m.start() - 10) : m.end() + 14]
        if re.search(r"(?:tone|quarter|way|ply|panel|mm|inch|in\b)", ctx):
            continue
        nums.append(n)
    coin_year = frozenset(range(1700, 2040))
    years = [n for n in nums if n in coin_year]
    if len(set(years)) >= 2:
        return True
    countish = [n for n in nums if n not in coin_year and 2 <= n <= 500]
    if countish and sum(countish) >= 2:
        return True
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
    comp = _inventory_text_from_specification_lines(specification_lines)
    blob = " ".join(specification_lines).lower()
    tl = (title or "").lower()
    multi_dim_textile = _spec_lines_indicate_multi_textile_piece(specification_lines)
    if multi_dim_textile and not any(
        w in tl for w in ("lot", "group", "collection", "pieces", "multiple", "assorted", "set")
    ):
        return True
    if _count_item_spec_lines(specification_lines) >= 2:
        item_blob = " ".join(_item_spec_bodies(specification_lines)).lower()
        item_cats = _distinct_inventory_categories(item_blob)
        t_cats = _distinct_inventory_categories(tl)
        if len(item_cats) >= 2 and (len(t_cats) <= 1 or len(item_cats - t_cats) >= 1):
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


_ACCESSORY_ITEM_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("scarf", "scarf"),
    ("pashmina", "scarf"),
    ("shawl", "scarf"),
    ("headband", "headband"),
    ("earmuff", "earmuffs"),
    ("beanie", "hat"),
    ("winter hat", "hat"),
    ("fleece hat", "hat"),
    ("knit hat", "hat"),
    ("glove", "gloves"),
    ("mitten", "mittens"),
    ("handbag", "handbag"),
    ("purse", "handbag"),
)


def _accessory_types_from_item_bodies(bodies: list[str]) -> list[str]:
    """Short garment/accessory nouns from ``Item n:`` bodies for lot-style titles."""
    found: list[str] = []
    seen: set[str] = set()
    for body in bodies:
        low = body.lower()
        for needle, label in _ACCESSORY_ITEM_KEYWORDS:
            if needle in low and label not in seen:
                seen.add(label)
                found.append(label)
                break
    return found


def _broaden_auction_title_for_components(title: str, specification_lines: list[str]) -> str:
    """
    Replace an under-inclusive title with a short lot-style title when **Components:** proves multiple distinct forms.
    """
    t = (title or "").strip()
    if not t or not _title_narrows_multi_component_lot(t, specification_lines):
        return t
    blob = " ".join(specification_lines).lower()
    item_bodies = _item_spec_bodies(specification_lines)
    if len(item_bodies) >= 2:
        accessory_types = _accessory_types_from_item_bodies(item_bodies)
        if len(accessory_types) >= 2 or (
            len(accessory_types) >= 1
            and _spec_lines_indicate_wardrobe_apparel(specification_lines, blob)
        ):
            if len(accessory_types) >= 2:
                joined = ", ".join(accessory_types[:4])
                return f"Fashion accessories: {joined}"[:500]
            return f"Fashion accessories ({len(item_bodies)} pieces)"[:500]
    if _spec_lines_indicate_multi_textile_piece(specification_lines):
        fiber = "Cotton"
        if "linen" in blob and "cotton" not in blob:
            fiber = "Linen"
        elif "silk" in blob:
            fiber = "Silk"
        elif "wool" in blob:
            fiber = "Wool"
        return f"{fiber} lace and textiles - multiple pieces; dimensions vary by photo (refer to tape reads)"[:500]
    mat = _material_lead_word_from_title_and_specs(t, specification_lines)
    out = f"{mat} decorative table pieces - multiple forms"
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
    lead = mat if mat and mat != "Brass" else ""
    if lead:
        out = f"{lead} {series} U.S. coins ({y_disp}) - {pack}"
    else:
        out = f"{series} U.S. coins ({y_disp}) - {pack}"
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
        f"These estate U.S. coins span {y_disp}, together with {pack}. "
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
    if "these estate u.s. coins" in low or "coins span" in low:
        return description
    out = (
        "These lace and cotton textile pieces come together—not one single swatch. "
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


_TITLE_CASE_MINOR_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "but",
        "or",
        "nor",
        "for",
        "yet",
        "so",
        "as",
        "at",
        "by",
        "in",
        "of",
        "on",
        "to",
        "up",
        "via",
        "with",
        "from",
        "into",
        "over",
    }
)


def _split_title_word_parts(word: str) -> tuple[str, str, str]:
    m = re.match(r"^([^A-Za-z0-9']*)([A-Za-z0-9']+?)([^A-Za-z0-9']*)$", word)
    if not m:
        return "", word, ""
    return m.group(1), m.group(2), m.group(3)


def _capitalize_title_core(core: str, *, force_lower: bool) -> str:
    if not core:
        return core
    if core.isdigit():
        return core
    if core.isupper() and 1 < len(core) <= 5:
        return core
    if force_lower:
        return core.lower()
    if "-" in core:
        return "-".join(_capitalize_title_core(part, force_lower=False) for part in core.split("-"))
    return core.lower().capitalize()


def format_auction_title_case(title: str, brands: list[str] | None = None) -> str:
    """Apply catalog title case: major words capped; short prepositions/conjunctions lowercased mid-title."""
    t = clean_listing_text(title).strip()
    if not t:
        return t
    words = t.split()
    if not words:
        return t
    n = len(words)
    cased: list[str] = []
    for i, raw in enumerate(words):
        prefix, core, suffix = _split_title_word_parts(raw)
        lower_core = core.lower() if core else ""
        force_lower = bool(core) and i not in (0, n - 1) and lower_core in _TITLE_CASE_MINOR_WORDS
        core_out = _capitalize_title_core(core, force_lower=force_lower) if core else core
        cased.append(prefix + core_out + suffix)
    out = " ".join(cased)
    for brand in brands or []:
        brand_clean = clean_listing_text(brand).strip()
        if not brand_clean:
            continue
        try:
            out = re.sub(re.escape(brand_clean), brand_clean, out, flags=re.IGNORECASE)
        except re.error:
            continue
    return out[:500]


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
    blob = " ".join(specification_lines).lower()
    if _spec_lines_indicate_wardrobe_apparel(specification_lines, blob):
        return _count_unique_wardrobe_items(specification_lines) >= 3
    if _count_item_spec_lines(specification_lines) >= 3:
        return True
    comp = _components_spec_text(specification_lines) or ""
    return bool(comp and _components_indicate_multi_piece(comp)) or _spec_lines_indicate_multi_textile_piece(
        specification_lines
    )


def _collapse_conflicting_apparel_prose(
    description: str, specification_lines: list[str]
) -> str:
    """One short paragraph; drop per-photo jacket variants and bogus 'several pieces' lines."""
    desc = clean_listing_text(description)
    if not desc:
        return desc
    desc = re.sub(
        r"(?i)\s*several distinct pieces[^.!?]*[.!?]?\s*",
        " ",
        desc,
    ).strip()
    desc = re.sub(
        r"(?i)\s*refer to the photos for layout[^.!?]*[.!?]?\s*",
        " ",
        desc,
    ).strip()
    blob = " ".join(specification_lines).lower()
    items_blob = " ".join(_item_spec_bodies(specification_lines)).lower()
    inv = items_blob or blob
    sents = _split_into_sentences(desc)
    if len(sents) <= 1:
        return desc
    kept: list[str] = []
    has_jacket = bool(re.search(r"(?i)\b(jacket|softshell|fleece)\b", inv))
    has_vest = bool(re.search(r"(?i)\b(vest|sleeveless|gilet)\b", inv))
    for sent in sents:
        sl = sent.lower()
        if "several distinct" in sl or "multiple pieces" in sl:
            continue
        if has_jacket and has_vest and len(kept) >= 2:
            break
        if has_jacket and "fleece" in sl and "softshell" in inv and "fleece" not in inv:
            continue
        if has_jacket and "softshell" in sl and "fleece" in inv and "softshell" not in inv:
            continue
        kept.append(sent)
    out = " ".join(kept[:2]).strip()
    return out or desc


def _ensure_multi_piece_description(description: str, specification_lines: list[str]) -> str:
    blob = " ".join(specification_lines).lower()
    desc = _collapse_conflicting_apparel_prose(description, specification_lines)
    if _spec_lines_indicate_wardrobe_apparel(specification_lines, blob):
        n = _count_unique_wardrobe_items(specification_lines)
        if n == 2:
            low = desc.lower()
            if "two-piece" in low or "2-piece" in low or "two piece" in low:
                return desc
            if not desc:
                return "Two-piece outerwear set—see Item lines for each garment."
            joiner = " " if desc.endswith((".", "!", "?")) else ". "
            return f"{desc}{joiner}Two-piece set—see Item lines for each garment."
        if n < 3:
            return desc
    if not _listing_should_have_multi_piece_cue(specification_lines):
        return desc
    comp = _components_spec_text(specification_lines) or ""
    low = desc.lower()
    if (
        "includes multiple pieces" in low
        or "these pieces" in low
        or "see components" in low
        or "see item" in low
        or "several distinct pieces" in low
        or "several vintage lace" in low
        or (comp and comp.lower() in low)
    ):
        return desc
    extra = "Several distinct pieces—refer to the photos for layout, size, and condition."
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
    - Optional letter suffix after the digits (e.g. ``JH603SK``, ``MS6000RG``).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("null", "none", "n/a", "unknown", "undefined", ""):
        return None
    compact = re.sub(r"\s+", "", s)
    if "#" in compact:
        tail = compact.rsplit("#", 1)[-1]
        m = re.search(r"([A-Za-z]{1,4})(\d{2,6})([A-Za-z]{0,4})", tail)
        if m:
            return f"{m.group(1).upper()}{m.group(2)}{m.group(3).upper()}"
        return normalize_tape_sku_input(tail)
    m = re.match(r"^([A-Za-z]{1,4})(\d{2,6})([A-Za-z]{0,4})$", compact)
    if m:
        return f"{m.group(1).upper()}{m.group(2)}{m.group(3).upper()}"
    return normalize_tape_sku_input(s)


def normalize_manual_sku_input(raw: str | None) -> str | None:
    """
    Preserve a user-typed lot code from mobile/web (do not strip letter prefixes/suffixes).

    Keeps A–Z / 0–9 only, uppercased, after removing spaces and punctuation.
    Requires 2–12 digits somewhere in the code so empty/garbage entries are rejected.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("null", "none", "n/a", "unknown", "undefined", ""):
        return None
    compact = re.sub(r"[^A-Za-z0-9]+", "", s).upper()
    if not compact:
        return None
    digits = re.sub(r"\D", "", compact)
    if len(digits) < 2 or len(digits) > 12:
        return None
    return compact[:40]


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


def strip_product_box_misread_as_tape_sku(lot: LotOutput, raw_payload: dict | None = None) -> None:
    """Clear digits-only SKUs that match product packaging text (ITEM 7209) rather than estate tape."""
    if not lot.sku:
        return
    compact = re.sub(r"\s+", "", str(lot.sku).strip()).upper()
    if re.fullmatch(r"[A-Z]{1,4}\d{2,6}", compact):
        return
    digits = sku_digits(lot.sku)
    if len(digits) < 2:
        return
    parts = [
        lot.auction_title or "",
        lot.professional_description or "",
        " ".join(lot.specification_lines or []),
    ]
    if raw_payload:
        parts.extend(
            str(raw_payload.get(k) or "")
            for k in ("AuctionTitle", "Title", "item_name", "ProfessionalDescription", "professional_description")
        )
    blob = " ".join(parts).lower()
    if re.search(rf"(?:\bitem\b|\basst\b|model\s*#?|catalog\s*#)\s*[-#:]?\s*{re.escape(digits)}\b", blob):
        lot.sku = None
        lot.sku_needs_review = False


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
    r"skirt|vest|gilet|sleeveless|hoodie|sweater|cardigan|garment|apparel|clog|"
    r"mule|wedge\s+shoe|footwear|sandal|softshell|fleece)\b"
)

_MARY_JANE_RE = re.compile(r"(?i)\bmary\s*jane\b")
_OPEN_TOE_FOOTWEAR_RE = re.compile(
    r"(?i)\b(open[- ]?toe|peep[- ]?toe|mules?|clogs?|slides?|slip[- ]?on)\b"
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


def label_evidence_blob(
    labels_and_tags: list[str],
    specification_lines: list[str] | None = None,
) -> str:
    """
    Text that came from readable tags/labels (OCR), not model-generated title or description.
    Used to reject hallucinated makers that only appear in listing prose.
    """
    parts: list[str] = list(labels_and_tags or [])
    for line in specification_lines or []:
        clean = clean_listing_text(line).strip()
        m = _FRAME_SIZE_TAG_RE.match(clean)
        if m and m.group(1).strip():
            parts.append(m.group(1).strip())
    return clean_listing_text(" ".join(parts)).lower()


def brand_has_label_evidence(
    brand: str,
    labels_and_tags: list[str],
    specification_lines: list[str] | None = None,
) -> bool:
    if not (brand or "").strip():
        return False
    blob = label_evidence_blob(labels_and_tags, specification_lines)
    return _brand_appears_in_blob(brand, blob)


def filter_brands_to_label_evidence(
    brands: list[str],
    labels_and_tags: list[str],
    specification_lines: list[str] | None = None,
) -> list[str]:
    """Keep makers only when legible tag/label OCR supports them — never title-only guesses."""
    kept: list[str] = []
    for brand in normalize_brand_list(brands):
        if brand_has_label_evidence(brand, labels_and_tags, specification_lines):
            kept.append(brand)
    return normalize_brand_list(kept)


def strip_leading_brands_from_text(text: str, brands: list[str]) -> str:
    """Remove a hallucinated leading maker prefix from title/description after brand drop."""
    t = clean_listing_text(text).strip()
    if not t or not brands:
        return t
    for brand in sorted(brands, key=len, reverse=True):
        b = (brand or "").strip()
        if not b:
            continue
        t = re.sub(r"^" + re.escape(b) + r"[\s,–—-]+", "", t, count=1, flags=re.I).strip()
    return t


_VINTAGE_ERA_VAGUE = frozenset({"vintage", "antique", "retro", "old", "used"})
_VINTAGE_DATED_EVIDENCE_RE = re.compile(
    r"(?i)\b("
    r"19\d{2}|20[0-1]\d|18\d{2}|"
    r"19\d0s|18\d0s|"
    r"mid[- ]century|art deco|victorian|edwardian|"
    r"antique\b"
    r")"
)


def lot_has_dated_era_evidence(
    labels_and_tags: list[str],
    specification_lines: list[str],
) -> bool:
    """True when tags or Era spec cite a concrete period — not model-default 'Vintage' alone."""
    blob = label_evidence_blob(labels_and_tags, specification_lines)
    if _VINTAGE_DATED_EVIDENCE_RE.search(blob):
        return True
    for line in specification_lines or []:
        parts = _spec_label_value_parts(line)
        if not parts or parts[0].lower() != "era":
            continue
        val = clean_listing_text(parts[1]).strip().lower()
        if not val or val in ("contemporary", "modern", "current"):
            continue
        if val in _VINTAGE_ERA_VAGUE:
            continue
        if _VINTAGE_DATED_EVIDENCE_RE.search(val) or re.search(r"\d{4}", val):
            return True
    return False


def _remove_vintage_tokens(text: str) -> str:
    t = re.sub(r"(?i)\bvintage\b", "", text or "")
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"^\s*[,\-–—]\s*", "", t)
    t = re.sub(r"\s+[,\-–—]\s+", " ", t)
    return clean_listing_text(t).strip(" ,-–—")


def _rephrase_lot_filler(text: str) -> str:
    """Replace estate-sale jargon ``lot`` with shopper-facing wording in titles and prose."""
    t = (text or "").strip()
    if not t:
        return t
    replacements: list[tuple[str, str]] = [
        (r"(?i)\bthis\s+estate\s+u\.?s\.?\s+coin\s+lot\b", "These estate U.S. coins"),
        (r"(?i)\bcoin\s+lot\s+spans\b", "coin collection spans"),
        (r"(?i)\bthis\s+lot\s+brings\s+together\b", "These pieces bring together"),
        (r"(?i)\bthis\s+lot\s+includes\b", "This grouping includes"),
        (r"(?i)\bseveral\s+distinct\s+pieces\s+in\s+this\s+lot\b", "Several distinct pieces"),
        (r"(?i)\bin\s+this\s+lot\b", "here"),
        (r"(?i)\bthe\s+whole\s+visible\s+lot\b", "the full spread"),
        (r"(?i)\bwhole\s+visible\s+lot\b", "full spread"),
        (r"(?i)\bthe\s+whole\s+lot\b", "everything shown"),
        (r"(?i)\bwhole\s+lot\b", "full grouping"),
        (r"(?i)\bwith\s+the\s+pieces\s+listed\s+in\s+the\s+lot\b", "with the pieces listed"),
        (r"(?i)\ba\s+warm\s+display\s+lot\b", "a warm display set"),
        (r"(?i)\bgroup\s+lot\b", "grouping"),
        (r"(?i)\bdecorative\s+table\s+lot\b", "decorative table pieces"),
        (r"(?i)\bdecorative\s+lot\b", "decorative pieces"),
        (r"(?i)\bfashion\s+accessory\s+lot\b", "fashion accessories"),
        (r"(?i)\blace\s+and\s+textile\s+lot\b", "lace and textiles"),
        (r"(?i)\btextile\s+lot\b", "textiles"),
        (r"(?i)\bouterwear\s+lot\b", "outerwear set"),
        (r"(?i)\btwo-piece\s+lot\b", "two-piece set"),
        (r"(?i)\bcoin\s+lot\b", "coin collection"),
        (r"(?i)\bestate\s+lot\b", "estate find"),
        (r"(?i)\bthe\s+lot\b", "the grouping"),
        (r"(?i)\bthis\s+lot\b", "these pieces"),
        (r"(?i)\b(\w+(?:\s+\w+){0,3})\s+lot\s*:\s*", r"\1 pieces: "),
        (r"(?i)\b(\w+(?:\s+\w+){0,3})\s+lot\s*-\s*", r"\1 pieces - "),
        (r"(?i)\b(\w+(?:\s+\w+){0,3})\s+lot\s*\(", r"\1 set ("),
    ]
    for pattern, repl in replacements:
        t = re.sub(pattern, repl, t)
    t = re.sub(r"(?i)\s+lot\b", "", t)
    t = re.sub(r"(?i)\blot\s+", "", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"^\s*[,\-–—]\s*", "", t)
    t = re.sub(r"\s+[,\-–—]\s+", " ", t)
    return clean_listing_text(t).strip(" ,-–—")


def scrub_gratuitous_lot_from_listing(
    auction_title: str,
    professional_description: str,
) -> tuple[str, str]:
    """Minimize filler ``lot`` in shopper-facing title and description."""
    return _rephrase_lot_filler(auction_title), _rephrase_lot_filler(professional_description)


_PLURAL_NOUN_RE = re.compile(
    r"\b(?!(?:glass|brass|cross|ss|us|is\b))(\w+(?:ies|ves|oes|ses|xes|zes|ches|shes|s))\b",
    re.I,
)


def _listing_has_plural_object_noun(text: str) -> bool:
    """True when prose names a plural object type (Sculptures, Knives, Lamps, …)."""
    return bool(_PLURAL_NOUN_RE.search(text or ""))


def scrub_redundant_pair_from_text(text: str) -> str:
    """Drop leading or inline ``pair of`` when plural nouns already imply quantity."""
    t = clean_listing_text(text or "").strip()
    if not t or not _listing_has_plural_object_noun(t):
        return t
    t = re.sub(r"(?i)^pair of\s+", "", t)
    t = re.sub(r"(?i)\b(?:a|an|this|the|delightful|charming|lovely|beautiful|wonderful)\s+pair of\s+", "", t)
    t = re.sub(r"(?i)\bpair of\s+", "", t)
    return re.sub(r"\s{2,}", " ", t).strip(" ,-–—")


def scrub_redundant_pair_from_listing(
    auction_title: str,
    professional_description: str,
) -> tuple[str, str]:
    return scrub_redundant_pair_from_text(auction_title), scrub_redundant_pair_from_text(professional_description)


_COMPLIMENT_ALTERNATIVES = (
    "sharp-looking",
    "classic",
    "solid",
    "striking",
    "appealing",
    "well-made",
    "display-ready",
    "crisp",
    "timeless",
    "inviting",
    "polished",
)


def vary_overused_compliment_adjectives(text: str) -> str:
    """Replace overused generic praise (especially 'handsome') with varied alternatives."""
    t = (text or "").strip()
    if not t or not re.search(r"(?i)\bhandsome\b", t):
        return t
    seed = sum(ord(c) for c in t[:96]) % len(_COMPLIMENT_ALTERNATIVES)
    i = 0

    def _repl(_match: re.Match[str]) -> str:
        nonlocal i
        word = _COMPLIMENT_ALTERNATIVES[(seed + i) % len(_COMPLIMENT_ALTERNATIVES)]
        i += 1
        return word

    return re.sub(r"(?i)\bhandsome\b", _repl, t)


def apply_title_hook_casing_to_description(auction_title: str, professional_description: str) -> str:
    """
    When description restates the title hook in lowercase, apply the title's Title Case to that phrase.
    """
    title = clean_listing_text(auction_title).strip()
    desc = (professional_description or "").strip()
    if not title or not desc:
        return professional_description or ""

    words = title.split()
    if len(words) < 2:
        return desc

    phrases: list[str] = []
    for i in range(len(words)):
        for j in range(len(words), i, -1):
            phrase = " ".join(words[i:j])
            if j - i < 2 and len(phrase) < 12:
                continue
            phrases.append(phrase)
    phrases.sort(key=len, reverse=True)

    result = desc
    for phrase in phrases:
        match = re.search(re.escape(phrase), result, flags=re.IGNORECASE)
        if not match or match.group(0) == phrase:
            continue
        result = result[: match.start()] + phrase + result[match.end() :]
    return result


def scrub_gratuitous_vintage_from_listing(
    auction_title: str,
    professional_description: str,
    specification_lines: list[str],
    labels_and_tags: list[str],
) -> tuple[str, str, list[str]]:
    """
    Drop filler ``vintage`` unless the lot has dated era evidence on tags or specs.
    When evidence exists, allow at most one ``vintage`` in title + description combined.
    """
    title = auction_title or ""
    desc = professional_description or ""
    specs = list(specification_lines or [])
    has_dated = lot_has_dated_era_evidence(labels_and_tags, specs)

    cleaned_specs: list[str] = []
    for line in specs:
        parts = _spec_label_value_parts(line)
        if parts and parts[0].lower() == "era" and parts[1].strip().lower() in _VINTAGE_ERA_VAGUE:
            continue
        cleaned_specs.append(line)
    specs = cleaned_specs

    if not has_dated:
        return _remove_vintage_tokens(title), _remove_vintage_tokens(desc), specs

    combined = f"{title} {desc}"
    if len(re.findall(r"(?i)\bvintage\b", combined)) <= 1:
        return title, desc, specs

    desc = _remove_vintage_tokens(desc)
    if len(re.findall(r"(?i)\bvintage\b", f"{title} {desc}")) > 1:
        title = _remove_vintage_tokens(title)
    return title, desc, specs


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


def _brands_from_item_spec_lines(specification_lines: list[str]) -> list[str]:
    """Makers named on ``Item n:`` rows (multi-brand wardrobe lots)."""
    found: list[str] = []
    for line in specification_lines:
        m = _ITEM_LINE_BODY_RE.match(clean_listing_text(line).strip())
        if not m:
            continue
        body = m.group(1)
        for pat, name in _KNOWN_LABEL_BRAND_PATTERNS:
            if pat.search(body):
                found.append(name)
    return normalize_brand_list(found)


def _is_multi_brand_wardrobe_lot(specification_lines: list[str], blob: str) -> bool:
    """Coordinated outfit with several legible makers — keep all in ``Brand:`` / ``brands[]``."""
    if not _spec_lines_indicate_wardrobe_apparel(specification_lines, blob):
        return False
    item_brands = _brands_from_item_spec_lines(specification_lines)
    if len(item_brands) >= 2:
        return True
    evidenced = [
        name
        for pat, name in _KNOWN_LABEL_BRAND_PATTERNS
        if pat.search(blob)
    ]
    return len(normalize_brand_list(evidenced)) >= 2


_AUTHENTIC_MAKER_RE = re.compile(
    r"(?i)\bAUTHENTIC\s+([A-Za-z][A-Za-z0-9'&.\-\s]{2,56}?)"
    r"(?:\s+(?:ESTABLISHED|SINCE|TRADITIONAL|TM|®)\b|\s*$|[,.;])"
)
_FASHION_BY_MAKER_RE = re.compile(
    r"(?i)\bfashion(?:\s+accessory)?\s+by\s+([A-Za-z][A-Za-z0-9'&.\-\s]{2,40})"
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
    (re.compile(r"(?i)\bmirabeau\b"), "Mirabeau"),
    (re.compile(r"(?i)\bshiboyuan\b"), "Shiboyuan"),
)

# Apparel garment tokens for collapsing per-photo ``Item n:`` rows across merged frames.
_APPAREL_GARMENT_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(cardigan|sweater|knit\s+top|knit\s+layer)\b"), "cardigan"),
    (re.compile(r"(?i)\b(vest|gilet|sleeveless)\b"), "vest"),
    (re.compile(r"(?i)\b(jacket|coat|blazer|utility\s+jacket|trucker|softshell|fleece)\b"), "jacket"),
    (re.compile(r"(?i)\b(trouser|pants?|slacks)\b"), "trousers"),
    (re.compile(r"(?i)\b(clogs?|mules?|wedge|shoe|sandal|heel|footwear)\b"), "footwear"),
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
    *,
    include_prose: bool = False,
) -> list[str]:
    """
    Recover maker names from legible tag OCR in ``labels_and_tags`` (and size/tag spec lines).

    By default does **not** scan title or description — those are model prose and can hallucinate makers.
    """
    parts = [" ".join(labels_and_tags), " ".join(specification_lines)]
    if include_prose:
        parts.extend([auction_title, professional_description])
    blob = clean_listing_text(" ".join(parts))
    if not blob:
        return []

    found: list[str] = []
    for pat, canonical in _KNOWN_LABEL_BRAND_PATTERNS:
        if pat.search(blob):
            found.append(canonical)
    if found:
        return normalize_brand_list(found)
    for m in _FASHION_BY_MAKER_RE.finditer(blob):
        name = _normalize_inferred_brand_name(m.group(1))
        if name:
            found.append(name)
    for m in _AUTHENTIC_MAKER_RE.finditer(blob):
        name = _normalize_inferred_brand_name(m.group(1))
        if name:
            found.append(name)
    return normalize_brand_list(found)


def _pick_primary_brand(
    candidates: list[str],
    auction_title: str,
    blob: str,
    *,
    label_blob: str = "",
) -> str:
    if not candidates:
        return ""
    evidence = label_blob or blob
    evidenced = [c for c in candidates if _brand_appears_in_blob(c, evidence)]
    if len(evidenced) == 1:
        return evidenced[0]
    if evidenced:
        return max(evidenced, key=len)
    for c in candidates:
        if brand_appears_in_prose(c, auction_title) and _brand_appears_in_blob(c, evidence):
            return c
    return ""


def resolve_lot_brands(
    brands: list[str],
    labels_and_tags: list[str],
    specification_lines: list[str],
    auction_title: str,
    professional_description: str,
) -> tuple[list[str], bool]:
    """
    Label-first brand resolution: drop hardware-only luxury guesses when garment tag evidence exists.

    Single-maker lots return one brand. Multi-brand wardrobe/coordinated outfit lots keep every maker
    evidenced on items, labels, title, or description.
    """
    merged = normalize_brand_list(
        brands
        + _brands_from_spec_lines(specification_lines)
        + _brands_from_item_spec_lines(specification_lines)
        + infer_brands_from_label_context(labels_and_tags, specification_lines)
    )
    if not merged:
        return [], False

    label_blob = label_evidence_blob(labels_and_tags, specification_lines)
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
    # Drop makers that only appear in AI title/description — require tag OCR support.
    label_evidenced = filter_brands_to_label_evidence(filtered, labels_and_tags, specification_lines)
    dropped = len(label_evidenced) < len(merged)

    if len(label_evidenced) <= 1:
        return label_evidenced, dropped

    if _is_multi_brand_wardrobe_lot(specification_lines, blob):
        keep = normalize_brand_list(label_evidenced)
        return keep, dropped

    if _count_item_spec_lines(specification_lines) >= 2 and len(label_evidenced) >= 2:
        return normalize_brand_list(label_evidenced), dropped

    primary = _pick_primary_brand(filtered, auction_title, blob, label_blob=label_blob)
    return ([primary] if primary else []), dropped or bool(primary and primary not in label_evidenced)


def sync_brand_spec_lines_to_resolved_brands(lines: list[str], brands: list[str]) -> list[str]:
    """Replace merged ``Brand:`` rows with resolved maker(s)."""
    out: list[str] = []
    for line in lines:
        parts = _spec_label_value_parts(line)
        if parts and parts[0] in _BRAND_MERGE_LABEL_KEYS:
            continue
        out.append(line)
    if brands:
        out.append(f"Brand: {'; '.join(brands)}")
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


def _preferred_open_footwear_term(blob: str) -> str:
    if re.search(r"(?i)\bclogs?\b", blob):
        return "clogs"
    if re.search(r"(?i)\bmules?\b", blob):
        return "mules"
    if re.search(r"(?i)\bwedge", blob):
        return "wedge clogs"
    if re.search(r"(?i)\bslip[- ]?on", blob):
        return "slip-on shoes"
    return "shoes"


def _scrub_misapplied_mary_jane_phrasing(text: str, blob: str) -> str:
    """
    Drop ``Mary Jane`` when inventory describes open-toe clogs/mules/slip-ons (closed strap ≠ Mary Jane).
    """
    if not text or not _MARY_JANE_RE.search(text):
        return text
    if not _OPEN_TOE_FOOTWEAR_RE.search(blob):
        return text
    term = _preferred_open_footwear_term(blob)
    s = text
    for pat, repl in (
        (re.compile(r"(?i)\bpeep-toe\s+mary\s*jane\s+wedge\s+mules?\b"), f"peep-toe wedge {term}"),
        (re.compile(r"(?i)\bmary\s*jane\s+wedge\s+clogs?\b"), "wedge clogs"),
        (re.compile(r"(?i)\bmary\s*jane\s+wedge\s+mules?\b"), "wedge mules"),
        (re.compile(r"(?i)\bmary\s*jane\s+wedge\b"), "wedge"),
        (re.compile(r"(?i)\btan\s+mary\s*jane\b"), f"tan {term}"),
        (_MARY_JANE_RE, term),
    ):
        s = pat.sub(repl, s)
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s+,", ",", s)
    s = re.sub(r"\s+\.", ".", s)
    return clean_listing_text(s)


def _scrub_misapplied_mary_jane_in_listing(
    professional_description: str,
    specification_lines: list[str],
    blob: str,
) -> tuple[str, list[str]]:
    desc = _scrub_misapplied_mary_jane_phrasing(professional_description, blob)
    out: list[str] = []
    for line in specification_lines:
        parts = _spec_label_value_parts(line)
        if parts and parts[0] == "style":
            label = line.split(":", 1)[0]
            rhs = _scrub_misapplied_mary_jane_phrasing(parts[1], blob)
            out.append(f"{label}: {rhs}" if rhs else line)
            continue
        out.append(line)
    return desc, out


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
    search_tags: list[str] = field(default_factory=list)
    condition: str = ""
    starting_price: float | None = None
    photo_count: int = 1
    sku_needs_review: bool = False
    brand_needs_review: bool = False
    lot_boundary_blank_page: bool = False
    ct_category: str | None = None

    def _finalize_listing_quality(self) -> None:
        """Label-first brands, apparel spec cleanup, and single-paragraph prose after merge."""
        blob = _lot_listing_blob(
            labels_and_tags=self.labels_and_tags,
            specification_lines=self.specification_lines,
            auction_title=self.auction_title,
            professional_description=self.professional_description,
        )
        self.professional_description = _collapse_repeated_prose_paragraphs(self.professional_description)
        self.professional_description, self.specification_lines = _scrub_misapplied_mary_jane_in_listing(
            self.professional_description, self.specification_lines, blob
        )
        self.specification_lines = _scrub_misapplied_collectible_specs_for_apparel(
            self.specification_lines, blob
        )
        pre_brands = normalize_brand_list(self.brands)
        label_inferred = infer_brands_from_label_context(
            self.labels_and_tags,
            self.specification_lines,
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
        dropped_brands = [b for b in pre_brands if b not in resolved]
        if dropped_brands:
            self.auction_title = strip_leading_brands_from_text(self.auction_title, dropped_brands)
            self.professional_description = strip_leading_brands_from_text(
                self.professional_description, dropped_brands
            )
        if brand_review or dropped_brands:
            self.brand_needs_review = True
            self.sku_needs_review = True
        self.brands = resolved
        self.specification_lines = sync_brand_spec_lines_to_resolved_brands(
            self.specification_lines, self.brands
        )
        self.professional_description = sanitize_description_unknowns(self.professional_description)
        move_unmentioned_brands_to_specs(self.brands, self.professional_description, self.specification_lines)
        self.specification_lines = dedupe_specification_lines_preserve_order(self.specification_lines)
        self.labels_and_tags = filter_labels_and_tags_for_lot(
            self.labels_and_tags,
            self.specification_lines,
            self.auction_title,
            self.professional_description,
        )
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
        self.auction_title, self.professional_description, self.specification_lines = (
            scrub_gratuitous_vintage_from_listing(
                self.auction_title,
                self.professional_description,
                self.specification_lines,
                self.labels_and_tags,
            )
        )
        self.auction_title, self.professional_description = scrub_gratuitous_lot_from_listing(
            self.auction_title,
            self.professional_description,
        )
        self.auction_title, self.professional_description = scrub_redundant_pair_from_listing(
            self.auction_title,
            self.professional_description,
        )
        self.professional_description = vary_overused_compliment_adjectives(self.professional_description)
        self.auction_title = format_auction_title_case(self.auction_title, self.brands)
        self.professional_description = apply_title_hook_casing_to_description(
            self.auction_title,
            self.professional_description,
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
        from app.services.ctseller_conditions import canonicalize_ct_condition
        from app.services.search_tags import merge_search_tags

        self.condition = canonicalize_ct_condition(self.condition)
        self.search_tags = merge_search_tags(
            self.search_tags,
            title=self.auction_title,
            description=self.professional_description,
            brands=self.brands,
            specification_lines=self.specification_lines,
            labels_and_tags=self.labels_and_tags,
            category=self.ct_category,
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
        search_tags = normalize_listing_lines(payload.get("search_tags") or payload.get("SearchTags"))
        condition = str(payload.get("Condition") or payload.get("condition_notes") or "").strip()
        from app.services.ctseller_conditions import canonicalize_ct_condition

        condition = canonicalize_ct_condition(condition)
        prof = sanitize_description_unknowns(
            str(
                payload.get("ProfessionalDescription")
                or payload.get("Description")
                or payload.get("professional_description")
                or ""
            )
        )
        raw_ct = payload.get("ct_category")
        if raw_ct is None:
            raw_ct = payload.get("CtCategory")
        ct_category = str(raw_ct).strip() if raw_ct is not None and str(raw_ct).strip() else None
        lot = cls(
            sku=sku_normalized or None,
            auction_title=title,
            professional_description=prof,
            specification_lines=spec_lines,
            brands=brands,
            labels_and_tags=labels,
            search_tags=search_tags,
            condition=condition,
            starting_price=price,
            sku_needs_review=payload_suggests_sku_review(payload),
            lot_boundary_blank_page=False,
            ct_category=ct_category,
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

        detail_condition = str(
            detail_payload.get("Condition") or detail_payload.get("condition_notes") or ""
        ).strip()
        if detail_condition:
            from app.services.ctseller_conditions import canonicalize_ct_condition

            self.condition = canonicalize_ct_condition(detail_condition)

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

