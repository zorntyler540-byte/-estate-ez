"""
Find lots where more than one distinct estate-style lot number appears:
- canonical ``lots.sku`` (digits only)
- plus explicit mentions in title / description / specs / labels (regex: "SKU 123", "(SKU 123)", "Lot #456", etc.)

Use this when the DB has a single ``sku`` column per lot but merged text may still reference another lot number.

Usage (from ``backend/``):

  python scripts/analyze_lot_sku_consistency.py --sqlite app.db
  python scripts/analyze_lot_sku_consistency.py --json exports.json --print-all

``--json`` expects a JSON array of objects shaped like ``GET /exports/json`` (LotOut-like keys).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path


def _sku_digits(value: str | None) -> str | None:
    if not value:
        return None
    d = re.sub(r"\D", "", str(value))
    return d or None


def _combined_text(lot: dict) -> str:
    parts = [
        str(lot.get("auction_title") or ""),
        str(lot.get("professional_description") or ""),
        str(lot.get("condition_text") or ""),
    ]
    for key in ("specification_lines", "labels_and_tags", "brands"):
        v = lot.get(key)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
    return "\n".join(parts)


def _extract_explicit_lot_numbers(text: str) -> set[str]:
    """Digit runs following common estate-SKU phrasing (reduces random years/prices)."""
    patterns = [
        r"(?:SKU|Sku)\s*#?\s*(\d{2,12})",
        r"\(\s*SKU\s+(\d{2,12})\s*\)",
        r"(?:lot\s*#|Lot\s*#|LOT\s*#)\s*(\d{2,12})",
        r"estate\s*lot\s*#?\s*(\d{2,12})",
        r"lot\s+(?:number|#|no\.?)\s*(\d{2,12})",
    ]
    out: set[str] = set()
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            digits = re.sub(r"\D", "", m.group(1))
            if digits:
                out.add(digits)
    return out


def _numeric_dedupe(digit_strings: set[str]) -> set[int]:
    return {int(s) for s in digit_strings if s.isdigit()}


def load_lots_from_sqlite(path: Path) -> list[dict]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM lots")]
    conn.close()
    for lot in rows:
        lot["specification_lines"] = json.loads(lot.get("specification_lines_json") or "[]")
        lot["brands"] = json.loads(lot.get("brands_json") or "[]")
        lot["labels_and_tags"] = json.loads(lot.get("labels_and_tags_json") or "[]")
        for k in ("specification_lines_json", "brands_json", "labels_and_tags_json"):
            lot.pop(k, None)
    return rows


def load_lots_from_json(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("JSON root must be an array of lot objects")
    return data


def analyze(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Returns:
      (grouped_inconsistent, single_photo_inconsistent)
    Each item: id, sku, photo_count, distinct_digit_groups, mentioned_in_text_only
    """
    grouped: list[dict] = []
    single: list[dict] = []
    for lot in rows:
        canon = _sku_digits(lot.get("sku"))
        mentioned = _extract_explicit_lot_numbers(_combined_text(lot))
        variant_digits: set[str] = set(mentioned)
        if canon:
            variant_digits.add(canon)
        nums = _numeric_dedupe(variant_digits)
        if len(nums) < 2:
            continue
        photo_count = int(lot.get("photo_count") or 0)
        rec = {
            "id": lot.get("id"),
            "sku": lot.get("sku"),
            "photo_count": photo_count,
            "distinct_values": sorted(variant_digits, key=lambda s: int(s) if s.isdigit() else 0),
            "mentioned_not_canon": sorted(mentioned - {canon} if canon else mentioned, key=int),
        }
        if photo_count >= 2:
            grouped.append(rec)
        else:
            single.append(rec)
    return grouped, single


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sqlite", type=Path, help="Path to SQLite file (e.g. backend/app.db)")
    src.add_argument("--json", type=Path, help="Path to JSON export array")
    ap.add_argument("--print-all", action="store_true", help="Print every flagged lot JSON line")
    args = ap.parse_args()

    if args.sqlite:
        rows = load_lots_from_sqlite(args.sqlite)
    else:
        rows = load_lots_from_json(args.json)

    grouped, single = analyze(rows)
    print(f"Total lots: {len(rows)}")
    print(
        "Grouped lots (photo_count >= 2) with >= 2 distinct lot numbers "
        f"(canonical sku + explicit text mentions): {len(grouped)}"
    )
    print(f"Single-photo lots with same issue (informational): {len(single)}")

    if args.print_all:
        for r in grouped:
            print(json.dumps(r, ensure_ascii=False))
    elif grouped:
        print("--- Sample (first 20) ---")
        for r in grouped[:20]:
            print(json.dumps(r, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
