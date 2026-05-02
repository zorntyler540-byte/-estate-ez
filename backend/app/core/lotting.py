from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_MENS_IN_TITLE_RE = re.compile(r"\bmen(?:'|’)?s\b", re.I)
_WOMENS_IN_TITLE_RE = re.compile(r"\bwomen(?:'|’)?s\b", re.I)
_MENS_EVIDENCE_RE = re.compile(r"\b(men(?:'|’)?s|male|boy(?:'|’)?s|boys)\b", re.I)
_WOMENS_EVIDENCE_RE = re.compile(r"\b(women(?:'|’)?s|female|ladies|girl(?:'|’)?s|girls)\b", re.I)
_UNBRANDED_RE = re.compile(r"^(?:unmarked|unbranded|unknown|none|n/?a|null)$", re.I)


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


def normalize_listing_lines(value: Any) -> list[str]:
    """Drop placeholder rows such as 'Unknown' or 'Brand: Unknown'."""
    lines = as_str_list(value)
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        clean = clean_listing_text(line)
        if not clean or _line_has_placeholder_value(clean):
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


def dedupe_specification_lines_preserve_order(lines: list[str]) -> list[str]:
    """
    Drop repeated spec bullets across merged photo frames.

    Uses a stronger normalization than plain lower() so ``- Foo`` and ``• Foo`` collapse once.
    Lines that look like **measurements or ruler context** (digits, units, ``ruler``, etc.) only dedupe
    when the normalized key matches exactly, so different per-item sizes stay separate.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in lines:
        line = clean_listing_text(raw)
        if not line or _line_has_placeholder_value(line):
            continue
        key = _spec_line_comparison_key(line)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def sanitize_description_unknowns(text: str) -> str:
    """Remove placeholder brand notes and placeholder clauses from prose."""
    s = clean_listing_text(text)
    if not s:
        return s
    # Remove "Noted brand(s): …" tails — maker/region belongs in structured ``brands[]`` / specs, not prose.
    for _ in range(100):
        new_s = re.sub(r"(?i)\s*,?\s*noted brands?:\s*[^.!?]+(?:[.!?]|$)", "", s)
        new_s = clean_listing_text(new_s)
        if new_s == s:
            break
        s = new_s
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
    Normalize a handwritten purple-tape lot code for storage and comparison.

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


def payload_lot_boundary_blank_page(payload: dict[str, Any]) -> bool:
    """True when the model marked this frame as a blank divider sheet between lots (flat-folder workflow)."""
    v = payload.get("lot_boundary_blank_page")
    if v is None:
        v = payload.get("lotBoundaryBlankPage")
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return False


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
    Prefer digits after the last ``#`` (purple-tape convention). Take the first 2–6 digit run after ``#``,
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
    _ = brands  # merge_detail still passes merged brands; structured field remains authoritative.
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
        boundary_blank = payload_lot_boundary_blank_page(payload)
        return cls(
            sku=None if boundary_blank else (sku_normalized or None),
            auction_title="Blank lot separator (page)" if boundary_blank else title,
            professional_description=(
                "Separator frame between catalog lots; not a listed item."
                if boundary_blank
                else sanitize_description_unknowns(
                    str(
                        payload.get("ProfessionalDescription")
                        or payload.get("Description")
                        or payload.get("professional_description")
                        or ""
                    )
                )
            ),
            specification_lines=dedupe_specification_lines_preserve_order(
                normalize_listing_lines(
                    payload.get("SpecificationLines") or payload.get("specification_lines")
                )
            ),
            brands=normalize_brand_list(payload.get("Brands") or payload.get("brand") or payload.get("brands")),
            labels_and_tags=normalize_listing_lines(
                payload.get("LabelsAndTags") or payload.get("labels_and_tags")
            ),
            condition=str(
                payload.get("Condition") or payload.get("condition_notes") or ""
            ).strip(),
            starting_price=None if boundary_blank else price,
            sku_needs_review=False if boundary_blank else payload_suggests_sku_review(payload),
            lot_boundary_blank_page=boundary_blank,
        )

    def merge_detail(self, detail_payload: dict[str, Any]) -> None:
        if payload_lot_boundary_blank_page(detail_payload):
            return
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

        if added_labels:
            for lab in added_labels:
                extra = f"Size / tag (from another frame in this lot): {lab}"
                if extra.lower() not in {x.lower() for x in self.specification_lines}:
                    self.specification_lines.append(extra)

        self.professional_description = weave_brands_into_description(
            self.brands, self.professional_description
        )
        self.photo_count += 1
        if payload_suggests_sku_review(detail_payload):
            self.sku_needs_review = True

