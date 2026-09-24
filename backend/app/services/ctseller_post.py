"""Post one catalog lot to CT Seller: create the item first, then photos."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.catalog_storage import resolve_catalog_storage_path
from app.models import Lot, Photo, Sale
from app.schemas import LotPostToCTSellerOut
from app.services.audience_hunt import lot_is_held_from_ctseller
from app.services.ctseller import CTSellerClient, CTSellerError, build_item_payload, resolve_lot_reserve_price
from app.services.listing_channels import is_facebook_listing_channel
from app.services.photo_roles import photos_for_ctbids_listing
from app.services.search_tags import parse_search_tags_json, search_tags_for_lot_fields

_LOG = logging.getLogger(__name__)

PhaseFn = Callable[[str], None]


def search_tags_for_lot_row(lot: Lot) -> list[str]:
    try:
        specs = json.loads(lot.specification_lines_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        specs = []
    try:
        brands = json.loads(lot.brands_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        brands = []
    try:
        labels = json.loads(lot.labels_and_tags_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        labels = []
    if not isinstance(specs, list):
        specs = []
    if not isinstance(brands, list):
        brands = []
    if not isinstance(labels, list):
        labels = []
    return search_tags_for_lot_fields(
        stored=parse_search_tags_json(getattr(lot, "search_tags_json", None)),
        title=lot.auction_title or "",
        description=lot.professional_description or "",
        brands=brands,
        specification_lines=specs,
        labels_and_tags=labels,
        category=lot.ct_category,
    )


def post_one_lot_to_ctseller(
    *,
    db: Session,
    lot: Lot,
    client: CTSellerClient,
    remote_sale_id: int,
    location_id: object,
    item_receipt_method: object,
    force: bool = False,
    allowed_areas: list[str] | None = None,
    tax: object = None,
    buyers_premium: object = None,
    on_phase: PhaseFn | None = None,
) -> LotPostToCTSellerOut:
    """Create the CT Seller item immediately, then upload listing photos.

    Photo bytes are pulled from R2 one at a time *after* ``item/add``. Holding all
    downloads before create meant Railway's ~60s HTTP cutoff could kill the request
    with nothing on CT Bids.
    """
    def phase(msg: str) -> None:
        if on_phase:
            on_phase(msg)

    if lot.ctseller_item_id and not force:
        raise HTTPException(
            status_code=409,
            detail=f"Already posted to CT Seller as item {lot.ctseller_item_id}. Pass force=true to post again.",
        )
    sku = (lot.sku or "").strip()
    if not sku:
        raise HTTPException(status_code=400, detail="Lot needs a SKU before posting to CT Seller")
    if not (lot.auction_title or "").strip():
        raise HTTPException(status_code=400, detail="Lot needs an auction title before posting")
    if is_facebook_listing_channel(getattr(lot, "listing_channel", None)):
        raise HTTPException(
            status_code=400,
            detail="This lot is marked for Facebook Marketplace — it cannot be posted to CT Seller",
        )
    if lot_is_held_from_ctseller(lot) and not force:
        raise HTTPException(
            status_code=400,
            detail="This lot is held for collector groups — turn off Hold for groups, or pass force=true",
        )

    photos = (
        db.query(Photo)
        .filter(Photo.lot_id == lot.id, Photo.account_id == lot.account_id)
        .order_by(Photo.sort_order.asc(), Photo.created_at.asc())
        .all()
    )
    listing_photos = photos_for_ctbids_listing(photos)
    if photos and not listing_photos:
        raise HTTPException(
            status_code=400,
            detail="Only SKU tape photos on this lot — add a product photo before posting to CT Seller",
        )

    areas = allowed_areas
    if areas is None:
        try:
            areas = client.list_areas()
        except CTSellerError:
            areas = None

    material: str | None = None
    try:
        specs = json.loads(lot.specification_lines_json or "[]")
        if isinstance(specs, list):
            for line in specs:
                s = str(line or "").strip()
                if s.lower().startswith("material:"):
                    material = s.split(":", 1)[-1].strip() or None
                    break
    except (TypeError, ValueError, json.JSONDecodeError):
        material = None

    item = build_item_payload(
        sale_id=int(remote_sale_id),
        location_id=location_id,
        sku=sku,
        title=lot.auction_title or sku,
        description=lot.professional_description or "",
        category=(lot.ct_category or "").strip() or "Household Goods Other",
        condition=(lot.condition_text or "").strip() or "GOOD",
        starting_price=lot.starting_price,
        item_receipt_method=item_receipt_method,  # type: ignore[arg-type]
        allowed_areas=areas,
        tax=tax if tax is not None else None,
        buyers_premium=buyers_premium if buyers_premium is not None else None,
        material=material,
        tag=search_tags_for_lot_row(lot),
        reserve_price=resolve_lot_reserve_price(
            lot,
            db.query(Sale).filter(Sale.id == lot.sale_id, Sale.account_id == lot.account_id).first(),
        ),
    )
    resolved_tags = search_tags_for_lot_row(lot)
    if resolved_tags:
        lot.search_tags_json = json.dumps(resolved_tags, ensure_ascii=True)

    phase("creating item")
    item_id = client.create_item(item)

    now = datetime.now(timezone.utc)
    lot.ctseller_item_id = int(item_id)
    lot.ctseller_posted_at = now
    lot.ctseller_sale_id = int(remote_sale_id)
    lot.ctseller_item_status = "Pending"
    lot.ctseller_sold_price = None
    db.add(lot)
    db.commit()
    db.refresh(lot)

    uploaded = 0
    total_photos = len(listing_photos)
    for idx, ph in enumerate(listing_photos):
        phase(f"photo {idx + 1}/{total_photos}")
        try:
            path = resolve_catalog_storage_path(ph.storage_path)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("CT Seller photo resolve failed lot=%s photo=%s: %s", lot.id, ph.id, exc)
            continue
        if path is None or not path.is_file():
            _LOG.warning("CT Seller photo missing lot=%s photo=%s", lot.id, getattr(ph, "id", idx))
            continue
        try:
            raw = path.read_bytes()
            name = Path(ph.original_name or path.name).stem or f"image{idx + 1}"
            client.add_item_image(
                item_id=item_id,
                image_bytes=raw,
                name=name[:80],
                display_order=idx + 1,
                is_display_image=(idx == 0),
            )
            uploaded += 1
        except (OSError, CTSellerError) as exc:
            _LOG.warning("CT Seller photo upload failed lot=%s photo=%s: %s", lot.id, ph.id, exc)
            continue

    return LotPostToCTSellerOut(
        lot_id=lot.id,
        ctseller_item_id=int(item_id),
        ctseller_sale_id=int(remote_sale_id),
        photos_uploaded=uploaded,
        ctseller_posted_at=now,
    )
