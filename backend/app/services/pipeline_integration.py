"""Bridge isolated ``backend/pipeline`` module to production ``LotOutput`` / job processing."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from app.config import settings
from app.core.gemini import analyze_path_with_retries, verify_tape_sku
from app.core.lotting import LotOutput, normalize_brand_list, tape_marking_canonical
from pipeline import process_incoming_field_lot

EmitFn = Callable[[dict[str, Any]], None]
PhotoEntry = tuple[Path, str]

# Per-photo triage is small; one listing + optional Pro dominate estimates.
EST_PIPELINE_INPUT_TOKENS_PER_PHOTO = 450
EST_PIPELINE_OUTPUT_TOKENS_PER_PHOTO = 120
EST_PIPELINE_LISTING_INPUT = 1800
EST_PIPELINE_LISTING_OUTPUT = 280
EST_PIPELINE_PRO_INPUT = 3200
EST_PIPELINE_PRO_OUTPUT = 600

_PLACEHOLDER_TITLES = frozenset({"estate lot", "estate item (item_1)", ""})


def _sync_pipeline_env_from_settings() -> None:
    """Ensure the isolated pipeline reads the same API key and model knobs as the app."""
    key = (settings.google_api_key or "").strip()
    if key:
        os.environ.setdefault("GOOGLE_API_KEY", key)
        os.environ.setdefault("GEMINI_API_KEY", key)
    os.environ["GEMINI_FLASH_MODEL"] = settings.gemini_flash_model
    os.environ["GEMINI_PRO_MODEL"] = settings.gemini_pro_model
    os.environ["LISTING_FLASH_MAX_FRAMES"] = str(settings.listing_flash_max_frames)
    os.environ["BRAND_PRO_MAX_LABEL_FRAMES"] = str(settings.brand_pro_max_label_frames)
    os.environ["BRAND_SKIP_PRO_IF_AGREEMENT"] = "1" if settings.brand_skip_pro_if_agreement else "0"
    os.environ["ENABLE_GROUNDED_VALUATION"] = "1" if settings.enable_grounded_valuation else "0"
    os.environ["TRIAGE_IMAGE_MAX_EDGE"] = str(settings.triage_image_max_edge)


def _coerce_photo_entries(photos: list[Path] | list[PhotoEntry]) -> tuple[list[PhotoEntry], dict[str, Path]]:
    """Normalize upload/regenerate inputs to stable triage filename keys mapped to disk paths."""
    entries: list[PhotoEntry] = []
    for item in photos:
        if isinstance(item, Path):
            path = item
            key = path.name
        else:
            path, key = item
            key = (key or path.name).strip() or path.name
        entries.append((path, key))

    path_by_key: dict[str, Path] = {}
    for path, key in entries:
        use_key = key
        suffix = 2
        while use_key in path_by_key and path_by_key[use_key] != path:
            use_key = f"{key}#{suffix}"
            suffix += 1
        path_by_key[use_key] = path

    normalized = [(path_by_key[k], k) for k in path_by_key]
    return normalized, path_by_key


def is_weak_pipeline_lot(lot: LotOutput) -> bool:
    """True when pipeline produced no usable listing text (generic placeholder)."""
    title = (lot.auction_title or "").strip()
    desc = (lot.professional_description or "").strip()
    title_low = title.lower()
    if title_low in _PLACEHOLDER_TITLES or title_low.startswith("estate item ("):
        if not lot.brands and not lot.specification_lines:
            return True
    if not title and not desc and not lot.specification_lines and not lot.brands:
        return True
    return False


def _lot_from_flash_draft(flash_draft: dict[str, Any], brand_audit: dict[str, Any]) -> LotOutput:
    items = list(flash_draft.get("items") or [])
    brands = normalize_brand_list(list(brand_audit.get("brands") or []))
    specs: list[str] = []
    labels: list[str] = []

    for idx, item in enumerate(items, start=1):
        title = str(item.get("suggested_title") or "").strip()
        ocr = str(item.get("raw_ocr_text_found") or "").strip()
        if ocr:
            labels.append(ocr)
            if not any(s.lower().startswith("brand:") for s in specs):
                specs.append(f"Brand: {ocr}")
        if title:
            specs.append(f"Item {idx}: {title}")

    if len(items) == 1:
        auction_title = str(items[0].get("suggested_title") or "").strip() or "Estate lot"
    elif len(items) > 1:
        auction_title = f"Estate lot ({len(items)} items)"
    else:
        auction_title = "Estate lot"

    lot = LotOutput(
        sku=None,
        auction_title=auction_title,
        professional_description=auction_title,
        specification_lines=specs,
        brands=brands,
        labels_and_tags=labels,
        condition="",
        starting_price=None,
        photo_count=0,
        sku_needs_review=False,
        brand_needs_review=bool(brand_audit.get("brand_needs_review")),
    )
    if lot.brand_needs_review:
        lot.sku_needs_review = True
    lot._finalize_listing_quality()
    return lot


def _apply_brand_audit(lot: LotOutput, brand_audit: dict[str, Any]) -> None:
    brands = normalize_brand_list(list(brand_audit.get("brands") or []))
    if brands:
        lot.brands = brands
    if brand_audit.get("brand_needs_review"):
        lot.brand_needs_review = True
        lot.sku_needs_review = True
    lot._finalize_listing_quality()


def _merge_flash_item_specs(lot: LotOutput, flash_draft: dict[str, Any]) -> None:
    """Add Item lines from triage draft when listing Flash did not produce them."""
    existing_items = sum(1 for s in lot.specification_lines if s.lower().startswith("item "))
    if existing_items >= len(flash_draft.get("items") or []):
        return
    for idx, item in enumerate(flash_draft.get("items") or [], start=1):
        title = str(item.get("suggested_title") or "").strip()
        if not title:
            continue
        line = f"Item {idx}: {title}"
        if line not in lot.specification_lines:
            lot.specification_lines.append(line)
    lot._finalize_listing_quality()


def _apply_tape_sku_from_paths(
    lot: LotOutput,
    tape_filenames: list[str],
    path_by_key: dict[str, Path],
    *,
    manifest_tag: str,
) -> None:
    if lot.sku or not tape_filenames:
        return
    for filename in tape_filenames:
        tape_path = path_by_key.get(filename)
        if tape_path is None:
            continue
        payload = analyze_path_with_retries(tape_path, manifest_tag=manifest_tag)
        if not payload:
            continue
        candidate = LotOutput.from_model(payload)
        if not candidate.sku:
            continue
        if settings.skip_tape_verify:
            lot.sku = tape_marking_canonical(candidate.sku) or candidate.sku
            lot.sku_needs_review = False
            return
        claimed = tape_marking_canonical(candidate.sku) or candidate.sku or ""
        if verify_tape_sku(tape_path, claimed) is True:
            lot.sku = claimed
            lot.sku_needs_review = False
            return
        lot.sku_needs_review = True


def _estimate_pipeline_tokens(photo_count: int, *, ran_pro: bool, listing_calls: int) -> tuple[int, int]:
    est_in = photo_count * EST_PIPELINE_INPUT_TOKENS_PER_PHOTO
    est_out = photo_count * EST_PIPELINE_OUTPUT_TOKENS_PER_PHOTO
    est_in += listing_calls * EST_PIPELINE_LISTING_INPUT
    est_out += listing_calls * EST_PIPELINE_LISTING_OUTPUT
    if ran_pro:
        est_in += EST_PIPELINE_PRO_INPUT
        est_out += EST_PIPELINE_PRO_OUTPUT
    return est_in, est_out


def run_single_lot_pipeline(
    photos: list[Path] | list[PhotoEntry],
    *,
    manifest_tag: str,
    emit: EmitFn | None = None,
) -> tuple[LotOutput, dict[str, Any]]:
    """
    Stage 1 triage + selection + Stage 2 brand audit, then a capped Flash listing pass
    on hero/front frames only. Returns ``LotOutput`` and raw pipeline diagnostics.

    Pass ``(path, filename_key)`` tuples when catalog files on disk are not named like
    the original camera filenames (regenerate on stored lots).
    """
    _sync_pipeline_env_from_settings()

    entries, path_by_key = _coerce_photo_entries(photos)
    images: dict[str, bytes] = {}
    for path, key in entries:
        images[key] = path.read_bytes()

    if emit:
        emit({"type": "batch_phase", "phase": "pipeline_triage", "total_photos": len(entries)})

    pipeline_result = process_incoming_field_lot(images)
    selected = dict(pipeline_result.get("selected") or {})
    flash_draft = dict(pipeline_result.get("flash_draft") or {})
    brand_audit = dict(pipeline_result.get("brand_audit") or {})

    if emit:
        emit(
            {
                "type": "pipeline_selection",
                "hero_filename": selected.get("hero_filename"),
                "front_filenames": list(selected.get("front_filenames") or []),
                "pro_filenames": list(selected.get("pro_filenames") or []),
                "catalog_only_count": int(pipeline_result.get("catalog_only_count") or 0),
                "pro_skipped_due_to_agreement": bool(pipeline_result.get("pro_skipped_due_to_agreement")),
            }
        )

    listing_names: list[str] = []
    hero_name = str(selected.get("hero_filename") or "").strip()
    if hero_name:
        listing_names.append(hero_name)
    for fn in list(selected.get("front_filenames") or []):
        if fn and fn not in listing_names:
            listing_names.append(fn)
    listing_names = listing_names[: max(1, settings.listing_flash_max_frames)]

    # If triage selection missed everything, still run listing on the first few files.
    if not listing_names:
        listing_names = list(path_by_key.keys())[: max(1, settings.listing_flash_max_frames)]

    lot: LotOutput | None = None
    listing_calls = 0
    for idx, filename in enumerate(listing_names):
        path = path_by_key.get(filename)
        if path is None:
            continue
        if emit:
            emit(
                {
                    "type": "batch_phase",
                    "phase": "pipeline_listing",
                    "index": idx,
                    "total": len(listing_names),
                    "file_name": filename,
                }
            )
        payload = analyze_path_with_retries(path, manifest_tag=manifest_tag)
        listing_calls += 1
        if not payload:
            continue
        if lot is None:
            lot = LotOutput.from_model(payload)
        else:
            lot.merge_detail(payload)

    if lot is None:
        lot = _lot_from_flash_draft(flash_draft, brand_audit)
    else:
        _merge_flash_item_specs(lot, flash_draft)
        _apply_brand_audit(lot, brand_audit)

    _apply_tape_sku_from_paths(
        lot,
        list(selected.get("tape_filenames") or []),
        path_by_key,
        manifest_tag=manifest_tag,
    )

    ran_pro = (
        not bool(pipeline_result.get("pro_skipped_due_to_agreement"))
        and bool(selected.get("pro_filenames") or hero_name)
    )
    est_in, est_out = _estimate_pipeline_tokens(len(entries), ran_pro=ran_pro, listing_calls=listing_calls)
    pipeline_result["token_estimates"] = {"input": est_in, "output": est_out}
    pipeline_result["listing_filenames_used"] = listing_names
    return lot, pipeline_result
