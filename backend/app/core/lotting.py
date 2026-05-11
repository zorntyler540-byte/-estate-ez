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
        return f"spec__item_{int(m_item.group(1))}"
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
    merged = consolidate_brand_and_marking_spec_lines(_collapse_semantic_spec_lines(first))
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
    nums = [int(x) for x in re.findall(r"\b\d+\b", components_text or "")]
    if sum(nums) >= 2:
        return True
    low = (components_text or "").lower()
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
        )
    )


MERGED_PRO_DESCRIPTION_MAX_CHARS = 4200
"""Cap merged lot prose so many photo passes do not stack into an unreadable wall."""


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


def _trim_block_for_merge_append(block: str, max_chars: int = 1100) -> str:
    """Each merged frame contributes at most a short addendum; details belong in specification_lines."""
    return _trim_prose_at_limit(block.strip(), max_chars)


def _prose_substantially_new(existing: str, new_block: str) -> bool:
    """True when ``new_block`` is worth appending (not a near-duplicate of ``existing``)."""
    e, n = (existing or "").strip().lower(), (new_block or "").strip().lower()
    if not n or len(n) < 25:
        return False
    if n in e:
        return False
    head = n[:160]
    if head and head in e:
        return False
    return True


def _merge_auction_title_for_lot(old: str, new: str, specification_lines: list[str]) -> str:
    """
    When later frames describe a broader inventory than the first, prefer a more inclusive title.
    First frame often latches onto the most ornate single object (e.g. dragon stand) and omits the rest.
    """
    o, n = (old or "").strip(), (new or "").strip()
    if not n:
        return o or ""
    if not o:
        return n
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
    return o


def _ensure_multi_piece_description(description: str, specification_lines: list[str]) -> str:
    comp = _components_spec_text(specification_lines)
    if not comp or not _components_indicate_multi_piece(comp):
        return description
    desc = clean_listing_text(description)
    low = desc.lower()
    if "this lot includes multiple pieces:" in low or comp.lower() in low:
        return desc
    extra = f"This lot includes multiple pieces: {comp}."
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
    lot_boundary_blank_page: bool = False

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
        move_unmentioned_brands_to_specs(brands, prof, spec_lines)
        spec_lines = dedupe_specification_lines_preserve_order(spec_lines)
        prof = _ensure_multi_piece_description(prof, spec_lines)
        return cls(
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
                self.auction_title, new_title_raw, self.specification_lines
            )
            self.auction_title = _sanitize_gendered_title(merged, detail_payload)

        if added_labels:
            for lab in added_labels:
                extra = f"Size / tag (from another frame in this lot): {lab}"
                if extra.lower() not in {x.lower() for x in self.specification_lines}:
                    self.specification_lines.append(extra)

        self.professional_description = sanitize_description_unknowns(self.professional_description)
        move_unmentioned_brands_to_specs(self.brands, self.professional_description, self.specification_lines)
        self.specification_lines = dedupe_specification_lines_preserve_order(self.specification_lines)
        self.professional_description = _ensure_multi_piece_description(
            self.professional_description, self.specification_lines
        )
        self.professional_description = _trim_prose_at_limit(
            self.professional_description, MERGED_PRO_DESCRIPTION_MAX_CHARS
        )
        self.photo_count += 1
        if payload_suggests_sku_review(detail_payload):
            self.sku_needs_review = True

