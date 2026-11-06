"""Side-by-side listing-author comparison. Fixtures only — no database.

Each sample lot is sent through the production listing prompt
(``pipeline.pipeline.build_listing_request``) once per model. Calls run
concurrently via ``generateContent`` (the same near-real-time path as catalog
jobs). This module never uses the Gemini Batch API and never opens a
production database.
"""

from __future__ import annotations

import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.core.gemini_rates import CURRENT, JAN_2027, estimate_usd

DEFAULT_COMPARE_MODELS: tuple[str, ...] = ("gemini-2.5-pro", "gemini-3.8-flash")
OPTIONAL_COMPARE_MODEL = "gemini-3.6-flash"

GenerateFn = Callable[[str, list[Any], Any], Any]


@dataclass
class SampleLot:
    lot_id: str
    images: dict[str, bytes]
    flash_draft: dict[str, Any]
    hero_triage: dict[str, Any] | None
    flash_listing: dict[str, Any] | None
    triage_ocr: list[dict[str, str]]
    note: str = ""


@dataclass
class ListingCallResult:
    lot_id: str
    model: str
    output_text: str = ""
    title: str = ""
    description: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_s: float = 0.0
    cost_current_usd: float | None = None
    cost_2027_usd: float | None = None
    error: str = ""
    dry_run: bool = False
    prompt_chars: int = 0
    extras: dict[str, str] = field(default_factory=dict)


def load_sample_lots(root: Path) -> list[SampleLot]:
    """Load ``lot.json`` folders. A single folder or a directory of them."""
    root = root.resolve()
    if (root / "lot.json").is_file():
        folders = [root]
    elif root.is_dir():
        folders = sorted(p for p in root.iterdir() if p.is_dir() and (p / "lot.json").is_file())
    else:
        raise FileNotFoundError(f"Fixture path is not a directory: {root}")
    if not folders:
        raise FileNotFoundError(f"No lot.json fixtures under {root}")
    return [load_sample_lot(folder) for folder in folders]


def load_sample_lot(folder: Path) -> SampleLot:
    from pipeline.pipeline import _hero_triage_block, aggregate_triage_ocr

    meta = json.loads((folder / "lot.json").read_text(encoding="utf-8"))
    names = [str(n) for n in meta.get("images") or []]
    if not names:
        raise ValueError(f"{folder / 'lot.json'} needs an images list")
    hero = str(meta.get("hero_filename") or names[0])
    blobs: dict[str, bytes] = {}
    for name in names:
        path = folder / name
        if not path.is_file():
            raise FileNotFoundError(f"Fixture image missing: {path}")
        blobs[name] = path.read_bytes()
    ordered: dict[str, bytes] = {}
    if hero in blobs:
        ordered[hero] = blobs.pop(hero)
    ordered.update(blobs)
    triage = list(meta.get("triage") or [])
    ocr = aggregate_triage_ocr(triage) if triage else []
    return SampleLot(
        lot_id=str(meta.get("lot_id") or folder.name),
        images=ordered,
        flash_draft=dict(meta.get("flash_draft") or {}),
        hero_triage=_hero_triage_block(triage, hero),
        flash_listing=dict(meta["flash_listing"]) if meta.get("flash_listing") else None,
        triage_ocr=ocr,
        note=str(meta.get("note") or ""),
    )


def _prompt_chars(parts: list[Any]) -> int:
    total = 0
    for part in parts:
        if isinstance(part, str):
            total += len(part)
    return total


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    parsed = getattr(response, "parsed", None)
    if parsed is None:
        return ""
    if hasattr(parsed, "model_dump_json"):
        return parsed.model_dump_json()
    if isinstance(parsed, dict):
        return json.dumps(parsed)
    return str(parsed)


def _usage(response: Any) -> tuple[int | None, int | None]:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return None, None
    if isinstance(meta, dict):
        inp = meta.get("prompt_token_count")
        out = meta.get("candidates_token_count")
    else:
        inp = getattr(meta, "prompt_token_count", None)
        out = getattr(meta, "candidates_token_count", None)
    return (
        int(inp) if inp is not None else None,
        int(out) if out is not None else None,
    )


def _title_and_description(text: str) -> tuple[str, str]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    title = data.get("final_auction_title") or ""
    description = data.get("final_professional_description") or ""
    return str(title), str(description)


def _listing_parts(lot: SampleLot) -> tuple[list[Any], Any]:
    from pipeline.pipeline import build_listing_request

    return build_listing_request(
        lot.images,
        lot.flash_draft,
        hero_triage_metadata=lot.hero_triage,
        flash_listing_context=lot.flash_listing,
        triage_ocr_aggregate=lot.triage_ocr,
        pro_author_mode=True,
    )


def _one_call(
    lot: SampleLot,
    model: str,
    generate: GenerateFn | None,
    *,
    dry_run: bool,
) -> ListingCallResult:
    parts, config = _listing_parts(lot)
    chars = _prompt_chars(parts)
    if dry_run:
        return ListingCallResult(
            lot_id=lot.lot_id,
            model=model,
            dry_run=True,
            prompt_chars=chars,
        )
    if generate is None:
        raise RuntimeError("generate function is required when dry_run is false")
    started = time.perf_counter()
    try:
        response = generate(model, parts, config)
    except Exception as exc:  # noqa: BLE001 — report the failure, keep the other calls
        return ListingCallResult(
            lot_id=lot.lot_id,
            model=model,
            latency_s=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
            prompt_chars=chars,
        )
    latency = time.perf_counter() - started
    text = _response_text(response)
    title, description = _title_and_description(text)
    inp, out = _usage(response)
    return ListingCallResult(
        lot_id=lot.lot_id,
        model=model,
        output_text=text,
        title=title,
        description=description,
        input_tokens=inp,
        output_tokens=out,
        latency_s=latency,
        cost_current_usd=estimate_usd(model, inp, out, schedule=CURRENT),
        cost_2027_usd=estimate_usd(model, inp, out, schedule=JAN_2027),
        prompt_chars=chars,
    )


def run_comparison(
    lots: list[SampleLot],
    models: list[str],
    *,
    generate: GenerateFn | None = None,
    max_workers: int = 4,
    dry_run: bool = False,
) -> list[ListingCallResult]:
    """Run every lot through every model at once (capped by ``max_workers``)."""
    if not lots:
        raise ValueError("No sample lots")
    if not models:
        raise ValueError("No models")
    pairs = [(lot, model) for lot in lots for model in models]
    workers = max(1, min(int(max_workers or len(pairs)), len(pairs)))
    results: list[ListingCallResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_one_call, lot, model, generate, dry_run=dry_run) for lot, model in pairs
        ]
        for future in as_completed(futures):
            results.append(future.result())
    order = {(lot.lot_id, model): i for i, (lot, model) in enumerate(pairs)}
    results.sort(key=lambda row: order.get((row.lot_id, row.model), 0))
    return results


def live_generate(model: str, parts: list[Any], config: Any) -> Any:
    """Production ``generateContent`` wrapper. Not the Batch API."""
    from app.core.gemini_slots import generate_content
    from pipeline.pipeline import _get_client

    return generate_content(_get_client(), model=model, contents=parts, config=config)


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.6f}"


def _cell(text: str) -> str:
    return (
        (text or "")
        .replace("\r", "")
        .replace("|", "\\|")
        .replace("\n", "<br>")
    )


def _fence(text: str) -> str:
    body = text or ""
    fence = "````" if "```" in body else "```"
    return f"{fence}text\n{body}\n{fence}"


def render_markdown(
    results: list[ListingCallResult],
    *,
    models: list[str],
    generated_at: str,
) -> str:
    from app.core.gemini_rates import RATES

    lines = [
        "# Listing model comparison",
        "",
        f"Generated: {generated_at}",
        "",
        "Each cell is the production listing prompt (`build_listing_request` / "
        "`run_brand_audit_pro`) sent with concurrent `generateContent` calls. "
        "The Gemini Batch API is not used. Fixtures are local files; this report "
        "does not read the catalog database.",
        "",
        "Token rates are USD per 1M tokens from `app/core/gemini_rates.py`.",
        "",
    ]
    for schedule, table in RATES.items():
        lines.append(f"- **{schedule}**")
        for model, rates in table.items():
            lines.append(
                f"  - `{model}`: input ${rates.input_per_million:.2f} / "
                f"output ${rates.output_per_million:.2f}"
            )
    lines.append("")
    by_lot: dict[str, dict[str, ListingCallResult]] = {}
    for row in results:
        by_lot.setdefault(row.lot_id, {})[row.model] = row
    for lot_id, per_model in by_lot.items():
        lines.append(f"## {lot_id}")
        lines.append("")
        header = "| | " + " | ".join(f"`{m}`" for m in models) + " |"
        sep = "|---|" + "|".join("---" for _ in models) + "|"
        lines.extend([header, sep])

        def cells(fn) -> str:
            return " | ".join(fn(per_model.get(m)) for m in models)

        lines.append("| Latency (s) | " + cells(lambda r: "—" if r is None else f"{r.latency_s:.3f}") + " |")
        lines.append(
            "| Input tokens | "
            + cells(lambda r: "—" if r is None or r.input_tokens is None else str(r.input_tokens))
            + " |"
        )
        lines.append(
            "| Output tokens | "
            + cells(lambda r: "—" if r is None or r.output_tokens is None else str(r.output_tokens))
            + " |"
        )
        lines.append(
            "| Cost current | " + cells(lambda r: "—" if r is None else _money(r.cost_current_usd)) + " |"
        )
        lines.append(
            "| Cost 2027-01-01 | " + cells(lambda r: "—" if r is None else _money(r.cost_2027_usd)) + " |"
        )
        lines.append("| Title | " + cells(lambda r: "—" if r is None else _cell(r.title)) + " |")
        lines.append(
            "| Description | " + cells(lambda r: "—" if r is None else _cell(r.description)) + " |"
        )
        lines.append("| Error | " + cells(lambda r: "—" if r is None or not r.error else _cell(r.error)) + " |")
        dry = [m for m in models if per_model.get(m) and per_model[m].dry_run]
        if dry:
            chars = per_model[dry[0]].prompt_chars
            lines.append("")
            lines.append(
                f"Dry run: Gemini was not called. Prompt text is about {chars} characters "
                "(images are extra). Re-run without `--dry-run` when `GOOGLE_API_KEY` is set."
            )
        lines.append("")
        for model in models:
            row = per_model.get(model)
            lines.append(f"### {lot_id} — `{model}`")
            lines.append("")
            if row is None:
                lines.append("_no result_")
            elif row.dry_run:
                lines.append("_dry run — no model output_")
            elif row.error and not row.output_text:
                lines.append(f"Call failed: {row.error}")
            else:
                lines.append(_fence(row.output_text))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_csv(results: list[ListingCallResult]) -> str:
    import io

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "lot_id",
            "model",
            "latency_s",
            "input_tokens",
            "output_tokens",
            "cost_current_usd",
            "cost_2027_01_01_usd",
            "title",
            "description",
            "output_text",
            "error",
            "dry_run",
            "prompt_chars",
        ],
    )
    writer.writeheader()
    for row in results:
        writer.writerow(
            {
                "lot_id": row.lot_id,
                "model": row.model,
                "latency_s": f"{row.latency_s:.6f}",
                "input_tokens": "" if row.input_tokens is None else row.input_tokens,
                "output_tokens": "" if row.output_tokens is None else row.output_tokens,
                "cost_current_usd": "" if row.cost_current_usd is None else f"{row.cost_current_usd:.6f}",
                "cost_2027_01_01_usd": "" if row.cost_2027_usd is None else f"{row.cost_2027_usd:.6f}",
                "title": row.title,
                "description": row.description,
                "output_text": row.output_text,
                "error": row.error,
                "dry_run": "1" if row.dry_run else "0",
                "prompt_chars": row.prompt_chars,
            }
        )
    return buf.getvalue()


def write_report(
    results: list[ListingCallResult],
    output_dir: Path,
    *,
    models: list[str],
    generated_at: str,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "report.md"
    csv_path = output_dir / "report.csv"
    md_path.write_text(
        render_markdown(results, models=models, generated_at=generated_at),
        encoding="utf-8",
    )
    csv_path.write_text(render_csv(results), encoding="utf-8")
    return md_path, csv_path
