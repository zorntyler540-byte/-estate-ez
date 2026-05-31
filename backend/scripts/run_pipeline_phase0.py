#!/usr/bin/env python3
"""Phase 0: run estate pipeline on a local photo folder (read-only; no DB writes)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run estate pipeline triage on a local folder.")
    parser.add_argument("folder", type=Path, help="Folder containing lot photos")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional JSON report path (default: print summary only)",
    )
    args = parser.parse_args()
    folder = args.folder.resolve()
    if not folder.is_dir():
        print(f"Not a directory: {folder}", file=sys.stderr)
        return 1

    photos = sorted(
        [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
        key=lambda p: str(p).lower(),
    )
    if not photos:
        print("No images found.", file=sys.stderr)
        return 1

    # Run from backend/ so ``pipeline`` package resolves.
    backend = Path(__file__).resolve().parent.parent
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))

    from pipeline import process_incoming_field_lot

    images = {p.name: p.read_bytes() for p in photos}
    result = process_incoming_field_lot(images)

    summary = {
        "photo_count": len(photos),
        "hero": result["selected"].get("hero_filename"),
        "fronts": result["selected"].get("front_filenames"),
        "pro_pool": result["selected"].get("pro_filenames"),
        "catalog_only_count": result["catalog_only_count"],
        "brands": result["brand_audit"].get("brands"),
        "brand_needs_review": result["brand_audit"].get("brand_needs_review"),
    }
    print(json.dumps(summary, indent=2))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Wrote full report to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
