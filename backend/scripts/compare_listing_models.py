#!/usr/bin/env python3
"""Compare listing-author models on local sample lots.

Sends each fixture through the production listing prompt concurrently
(``generateContent``, not the Gemini Batch API) and writes a side-by-side
Markdown report plus a CSV. Does not open the catalog database and does not
read production photos unless you pass a folder of your own exports.

Examples (from ``backend/``):

    ../backend/.venv/bin/python scripts/compare_listing_models.py --dry-run
    ../backend/.venv/bin/python scripts/compare_listing_models.py
    ../backend/.venv/bin/python scripts/compare_listing_models.py --include-3-6

Requires ``GOOGLE_API_KEY`` for a live run. Reports land in
``scripts/listing_compare_out/`` (gitignored).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.listing_compare import (  # noqa: E402
    DEFAULT_COMPARE_MODELS,
    OPTIONAL_COMPARE_MODEL,
    live_generate,
    load_sample_lots,
    run_comparison,
    write_report,
)

_DEFAULT_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "listing_samples"
_DEFAULT_OUTPUT = Path(__file__).resolve().parent / "listing_compare_out"


def _parse_models(raw: str, *, include_36: bool) -> list[str]:
    models = [part.strip() for part in raw.split(",") if part.strip()]
    if include_36 and OPTIONAL_COMPARE_MODEL not in models:
        models.append(OPTIONAL_COMPARE_MODEL)
    if not models:
        raise SystemExit("Pass at least one model via --models")
    return models


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=_DEFAULT_FIXTURES,
        help="Directory of sample lots (each with lot.json) or one lot folder",
    )
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_COMPARE_MODELS),
        help="Comma-separated model ids (default: gemini-2.5-pro,gemini-3.8-flash)",
    )
    parser.add_argument(
        "--include-3-6",
        action="store_true",
        help=f"Also run {OPTIONAL_COMPARE_MODEL}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Directory for report.md and report.csv",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Concurrent generateContent calls (default: one per lot/model pair, max 8)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the production prompt and write the report without calling Gemini",
    )
    args = parser.parse_args(argv)
    models = _parse_models(args.models, include_36=args.include_3_6)
    try:
        lots = load_sample_lots(args.fixtures)
    except (OSError, ValueError) as exc:
        print(f"Could not load fixtures: {exc}", file=sys.stderr)
        return 1

    if not args.dry_run:
        from app.config import google_api_key

        if not google_api_key():
            print(
                "GOOGLE_API_KEY is not set, so Gemini was not called. "
                "Export the key or pass --dry-run. Nothing was read from a database.",
                file=sys.stderr,
            )
            return 2

    workers = args.workers if args.workers > 0 else min(8, max(len(models), len(lots) * len(models)))
    results = run_comparison(
        lots,
        models,
        generate=None if args.dry_run else live_generate,
        max_workers=workers,
        dry_run=args.dry_run,
    )
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    md_path, csv_path = write_report(
        results,
        args.output,
        models=models,
        generated_at=generated_at,
    )
    failures = [row for row in results if row.error]
    print(f"Wrote {md_path}")
    print(f"Wrote {csv_path}")
    print(f"Lots: {len(lots)}  Models: {', '.join(models)}  Workers: {workers}")
    if args.dry_run:
        print("Dry run only — no Gemini calls.")
    if failures:
        print(f"{len(failures)} call(s) failed. See the report.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
