"""Assign the next free SKU numbers to lots that have none."""

from __future__ import annotations

import re

_STEM = re.compile(r"^(.*?)(\d+)$")


class SkuStartError(ValueError):
    """The starting value is not a number or a prefix plus a number."""


def parse_sku_start(raw: str) -> tuple[str, int, int]:
    text = (raw or "").strip()
    match = _STEM.match(text)
    if not match or not match.group(2):
        raise SkuStartError("Start with a number, such as 1001 or DK1001.")
    prefix, digits = match.group(1), match.group(2)
    return prefix, int(digits), len(digits)


def format_sku(prefix: str, number: int, width: int) -> str:
    return f"{prefix}{number:0{width}d}"


def assign_blank_skus(
    blank_lot_ids: list[str],
    *,
    start: str,
    taken: set[str],
) -> list[tuple[str, str]]:
    """Return (lot_id, sku) in order. ``taken`` is existing SKUs, compared case-insensitively."""
    prefix, number, width = parse_sku_start(start)
    used = {item.strip().upper() for item in taken if item and item.strip()}
    out: list[tuple[str, str]] = []
    for lot_id in blank_lot_ids:
        while True:
            sku = format_sku(prefix, number, width)
            number += 1
            if sku.upper() not in used:
                break
        used.add(sku.upper())
        out.append((lot_id, sku))
    return out
