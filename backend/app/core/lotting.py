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

    @classmethod
    def from_model(cls, payload: dict[str, Any]) -> "LotOutput":
        price_raw = payload.get("StartingPrice")
        price = None
        if price_raw is not None and str(price_raw).strip():
            try:
                price = float(str(price_raw).replace("$", "").strip())
            except ValueError:
                price = None
        return cls(
            sku=sku_digits(payload.get("SKU")) or None,
            auction_title=str(payload.get("AuctionTitle") or payload.get("Title") or "").strip(),
            professional_description=str(
                payload.get("ProfessionalDescription") or payload.get("Description") or ""
            ).strip(),
            specification_lines=as_str_list(payload.get("SpecificationLines")),
            brands=as_str_list(payload.get("Brands")),
            labels_and_tags=as_str_list(payload.get("LabelsAndTags")),
            condition=str(payload.get("Condition") or "").strip(),
            starting_price=price,
        )

    def merge_detail(self, detail_payload: dict[str, Any]) -> None:
        detail_brands = as_str_list(detail_payload.get("Brands"))
        detail_labels = as_str_list(detail_payload.get("LabelsAndTags"))
        detail_specs = as_str_list(detail_payload.get("SpecificationLines"))

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

