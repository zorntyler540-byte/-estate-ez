"""Bridge isolated ``backend/pipeline`` module to production ``LotOutput`` / job processing."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

from app.config import settings
from app.core.gemini import analyze_path_with_retries, verify_tape_sku
from app.core.lotting import LotOutput, infer_brands_from_label_context, normalize_brand_list, tape_marking_canonical
from pipeline import triage_and_select_field_lot
from pipeline.pipeline import (
    _build_pro_image_payload,
    _hero_triage_block,
    _manifest_to_brand_audit,
    run_brand_audit_pro,
)
from pipeline.schemas import FullyAuditedManifest, InventoryLotDraft

logger = logging.getLogger(__name__)

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
    if (
        title_low in _PLACEHOLDER_TITLES
        or title_low.startswith("estate item (")
        or title_low.startswith("estate lot (")
    ):
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


def _brands_from_triage_and_draft(
    triage: list[dict[str, Any]] | None,
    flash_draft: dict[str, Any] | None,
) -> list[str]:
    """Recover maker names from triage OCR and Flash draft using label-context rules."""
    labels: list[str] = []
    if triage:
        for row in triage:
            if row.get("label_legibility") not in ("clear", "partial"):
                continue
            tag_text = str(row.get("brand_text") or "").strip()
            if tag_text:
                labels.append(tag_text)
    spec_lines: list[str] = []
    if flash_draft:
        for item in flash_draft.get("items") or []:
            ocr = str(item.get("raw_ocr_text_found") or "").strip()
            if ocr:
                labels.append(ocr)
        spec_lines = [
            f"Item {idx}: {str(item.get('suggested_title') or '').strip()}"
            for idx, item in enumerate(flash_draft.get("items") or [], start=1)
            if str(item.get("suggested_title") or "").strip()
        ]
    return infer_brands_from_label_context(labels, spec_lines)


def _brands_from_pro_manifest(manifest: FullyAuditedManifest) -> list[str]:
    """Infer makers from Pro proofread titles when confirmed_brand is empty."""
    spec_lines = [
        f"Item {row.line_item_id}: {(row.final_title or '').strip()}"
        for row in manifest.verified_inventory
        if (row.final_title or "").strip()
    ]
    return infer_brands_from_label_context(
        [],
        spec_lines,
        manifest.final_auction_title,
        manifest.final_professional_description,
    )


def _apply_brand_audit(
    lot: LotOutput,
    brand_audit: dict[str, Any],
    *,
    triage: list[dict[str, Any]] | None = None,
    flash_draft: dict[str, Any] | None = None,
    pro_manifest: FullyAuditedManifest | None = None,
) -> None:
    brands: list[str] = list(brand_audit.get("brands") or [])
    if pro_manifest:
        brands.extend(_brands_from_pro_manifest(pro_manifest))
    brands.extend(_brands_from_triage_and_draft(triage, flash_draft))
    if triage:
        for row in triage:
            if row.get("label_legibility") not in ("clear", "partial"):
                continue
            tag_text = str(row.get("brand_text") or "").strip()
            if tag_text and tag_text not in lot.labels_and_tags:
                lot.labels_and_tags.append(tag_text)

    brands = normalize_brand_list(brands)
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


def _apply_pro_manifest_to_lot(
    lot: LotOutput,
    manifest_json: str,
    *,
    triage: list[dict[str, Any]] | None = None,
    flash_draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply Pro proofread title, description, item lines, and brands onto the Flash listing."""
    try:
        manifest = FullyAuditedManifest.model_validate(json.loads(manifest_json))
    except Exception:
        _apply_brand_audit(lot, {"brands": [], "brand_needs_review": True}, triage=triage, flash_draft=flash_draft)
        return {"brands": [], "brand_needs_review": True, "was_corrected_by_pro": False}

    flash_model = InventoryLotDraft.model_validate(flash_draft or {})
    title = (manifest.final_auction_title or "").strip()
    desc = (manifest.final_professional_description or "").strip()
    if title:
        lot.auction_title = title
    if desc:
        lot.professional_description = desc

    if manifest.verified_inventory:
        item_lines: list[str] = []
        for row in manifest.verified_inventory:
            line_title = (row.final_title or "").strip()
            if not line_title:
                continue
            item_lines.append(f"Item {row.line_item_id}: {line_title}")
        if item_lines:
            kept = [ln for ln in lot.specification_lines if not ln.lower().startswith("item ")]
            lot.specification_lines = kept + item_lines

    brand_audit = _manifest_to_brand_audit(manifest_json, [], flash_model)
    _apply_brand_audit(
        lot,
        brand_audit.model_dump(),
        triage=triage,
        flash_draft=flash_draft,
        pro_manifest=manifest,
    )
    # Pro title/description are authoritative — finalize must not overwrite them.
    if title:
        lot.auction_title = title
    if desc:
        lot.professional_description = desc
    if manifest.listing_was_corrected_by_pro:
        brand_audit_dict = brand_audit.model_dump()
        brand_audit_dict["was_corrected_by_pro"] = True
        if manifest.listing_correction_details:
            brand_audit_dict["correction_details"] = manifest.listing_correction_details
        return brand_audit_dict
    return brand_audit.model_dump()


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
    Stage 1 triage + selection, capped Flash listing, then Pro proofreads title/brands/items.
    Returns ``LotOutput`` and raw pipeline diagnostics.

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

    logger.info(
        "Pipeline start: photos=%d pro_model=%s flash_model=%s",
        len(entries),
        settings.gemini_pro_model,
        settings.gemini_flash_model,
    )
    pipeline_result = triage_and_select_field_lot(images)
    selected = dict(pipeline_result.get("selected") or {})
    flash_draft = dict(pipeline_result.get("flash_draft") or {})
    triage_rows = list(pipeline_result.get("triage") or [])

    if emit:
        emit(
            {
                "type": "pipeline_selection",
                "hero_filename": selected.get("hero_filename"),
                "front_filenames": list(selected.get("front_filenames") or []),
                "pro_filenames": list(selected.get("pro_filenames") or []),
                "catalog_only_count": int(pipeline_result.get("catalog_only_count") or 0),
            }
        )

    listing_names: list[str] = []
    hero_name = str(selected.get("hero_filename") or "").strip()
    pro_filenames = list(selected.get("pro_filenames") or [])
    if hero_name:
        listing_names.append(hero_name)
    for fn in pro_filenames:
        if fn and fn not in listing_names:
            listing_names.append(fn)
    for fn in list(selected.get("front_filenames") or []):
        if fn and fn not in listing_names:
            listing_names.append(fn)
    listing_names = listing_names[: max(1, settings.listing_flash_max_frames)]

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

    brand_audit: dict[str, Any] = {"brands": [], "brand_needs_review": True}
    pro_skipped = True
    ran_pro = False

    if lot is None:
        lot = _lot_from_flash_draft(flash_draft, brand_audit)
    else:
        _merge_flash_item_specs(lot, flash_draft)

    hero_triage = _hero_triage_block(triage_rows, hero_name)
    should_run_pro = bool(hero_name or pro_filenames or lot)
    if should_run_pro:
        pro_images = _build_pro_image_payload(images, pro_filenames, hero_filename=hero_name)
        flash_listing_context = {
            "auction_title": lot.auction_title,
            "professional_description": lot.professional_description,
            "specification_lines": list(lot.specification_lines),
            "brands": list(lot.brands),
        }
        if emit:
            emit({"type": "batch_phase", "phase": "pipeline_pro_proofread", "total_photos": len(entries)})
        try:
            manifest_json = run_brand_audit_pro(
                pro_images,
                flash_draft,
                hero_triage_metadata=hero_triage,
                flash_listing_context=flash_listing_context,
            )
            brand_audit = _apply_pro_manifest_to_lot(
                lot,
                manifest_json,
                triage=triage_rows,
                flash_draft=flash_draft,
            )
            pipeline_result["brand_audit_json"] = manifest_json
            pro_skipped = False
            ran_pro = True
            pipeline_result["pro_model"] = settings.gemini_pro_model
            logger.info("Pipeline Pro proofread succeeded model=%s", settings.gemini_pro_model)
        except Exception as exc:
            logger.warning(
                "Pipeline Pro proofread failed (%s); falling back to Flash + triage brands.",
                exc,
                exc_info=True,
            )
            pipeline_result["pro_error"] = f"{type(exc).__name__}: {exc}"
            _apply_brand_audit(lot, brand_audit, triage=triage_rows, flash_draft=flash_draft)
    else:
        _apply_brand_audit(lot, brand_audit, triage=triage_rows, flash_draft=flash_draft)

    pipeline_result["brand_audit"] = brand_audit
    pipeline_result["pro_skipped_due_to_agreement"] = pro_skipped
    pipeline_result["ran_pro"] = ran_pro

    _apply_tape_sku_from_paths(
        lot,
        list(selected.get("tape_filenames") or []),
        path_by_key,
        manifest_tag=manifest_tag,
    )

    est_in, est_out = _estimate_pipeline_tokens(len(entries), ran_pro=ran_pro, listing_calls=listing_calls)
    pipeline_result["token_estimates"] = {"input": est_in, "output": est_out}
    pipeline_result["listing_filenames_used"] = listing_names
    return lot, pipeline_result
