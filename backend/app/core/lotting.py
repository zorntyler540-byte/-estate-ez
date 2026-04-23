from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if x is not None and str(x).strip()]
    s = str(value).strip()
    return [s] if s else []


def tape_sku_opens_new_lot(sku: Any) -> bool:
    if sku is None:
        return False
    raw = str(sku).strip()
    if not raw or raw.lower() in ("null", "none", "n/a", "unknown", "undefined"):
        return False
    digits = re.sub(r"\D", "", raw)
    return 1 <= len(digits) <= 12


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
    sku_clean = sku_digits(raw) if raw not in (None, "") else None
    if not sku_clean:
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
    missing = [b for b in brands if b and not brand_appears_in_prose(b, description)]
    if not missing:
        return description
    if len(missing) == 1:
        prefix = f"**{missing[0]}** - "
    else:
        bolded = ", ".join(f"**{m}**" for m in missing[:-1])
        prefix = f"{bolded}, and **{missing[-1]}** - "
    return (prefix + description).strip()


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
        sku_normalized = normalize_tape_sku_input(
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
        return cls(
            sku=sku_normalized or None,
            auction_title=title,
            professional_description=str(
                payload.get("ProfessionalDescription")
                or payload.get("Description")
                or payload.get("professional_description")
                or ""
            ).strip(),
            specification_lines=as_str_list(
                payload.get("SpecificationLines") or payload.get("specification_lines")
            ),
            brands=as_str_list(payload.get("Brands") or payload.get("brand") or payload.get("brands")),
            labels_and_tags=as_str_list(
                payload.get("LabelsAndTags") or payload.get("labels_and_tags")
            ),
            condition=str(
                payload.get("Condition") or payload.get("condition_notes") or ""
            ).strip(),
            starting_price=price,
            sku_needs_review=payload_suggests_sku_review(payload),
        )

    def merge_detail(self, detail_payload: dict[str, Any]) -> None:
        detail_brands = as_str_list(
            detail_payload.get("Brands") or detail_payload.get("brand") or detail_payload.get("brands")
        )
        detail_labels = as_str_list(
            detail_payload.get("LabelsAndTags") or detail_payload.get("labels_and_tags")
        )
        detail_specs = as_str_list(
            detail_payload.get("SpecificationLines") or detail_payload.get("specification_lines")
        )

        self.brands, added_brands = merge_unique_case_insensitive(self.brands, detail_brands)
        self.labels_and_tags, added_labels = merge_unique_case_insensitive(
            self.labels_and_tags, detail_labels
        )
        self.specification_lines, _ = merge_unique_case_insensitive(
            self.specification_lines, detail_specs
        )

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

