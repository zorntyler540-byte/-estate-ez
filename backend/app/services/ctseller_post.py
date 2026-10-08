"""Post one catalog lot to CT Seller: create the item first, then photos."""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.catalog_storage import resolve_catalog_storage_path
from app.models import Lot, LotVideo, Photo, Sale
from app.schemas import LotPostToCTSellerOut
from app.services.audience_hunt import lot_is_held_from_ctseller
from app.services.ctseller import (
    CTSellerClient,
    CTSellerError,
    build_item_payload,
    jpeg_bytes_for_ctseller_post,
    resolve_lot_reserve_price,
)
from app.services.listing_channels import is_facebook_listing_channel
from app.services.photo_roles import photos_for_ctbids_listing
from app.services.search_tags import parse_search_tags_json, search_tags_for_lot_fields

_LOG = logging.getLogger(__name__)

PhaseFn = Callable[[str], None]


def ctseller_image_ids_to_replace(rows: list[dict]) -> list[int]:
    """Image ids to remove before a photo refresh. Videos on the item stay."""
    ids: list[int] = []
    for row in rows:
        kind = str(row.get("type") or "image").strip().lower()
        if kind not in ("", "image"):
            continue
        raw_id = row.get("id")
        if raw_id is None:
            continue
        try:
            ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue
    return ids


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


def ctseller_upload_name(original_name: object, fallback: str) -> str:
    stem = Path(str(original_name or "")).stem.strip()
    return (stem or fallback)[:80]


def listing_photos_missing_on_ctseller(listing_photos: list[Photo], rows: list[dict]) -> list[Photo]:
    """Gallery frames whose file name is not already an image on the CT Seller item."""
    present: set[str] = set()
    for row in rows:
        kind = str(row.get("type") or "image").strip().lower()
        if kind not in ("", "image"):
            continue
        key = ctseller_upload_name(row.get("name"), "").lower()
        if key:
            present.add(key)
    missing: list[Photo] = []
    for idx, photo in enumerate(listing_photos):
        key = ctseller_upload_name(getattr(photo, "original_name", None), f"image{idx + 1}").lower()
        if key in present:
            continue
        missing.append(photo)
    return missing


def listing_videos_missing_on_ctseller(videos: list[LotVideo], rows: list[dict]) -> list[LotVideo]:
    """Clips whose file name is not already a video on the CT Seller item."""
    present: set[str] = set()
    for row in rows:
        if str(row.get("type") or "").strip().lower() != "video":
            continue
        name = str(row.get("name") or "").strip().lower()
        if not name:
            continue
        present.add(name)
        present.add(Path(name).stem.lower())
    missing: list[LotVideo] = []
    for video in videos:
        name = str(getattr(video, "original_name", "") or "").strip()
        key = name.lower()
        stem = Path(name).stem.lower() if name else ""
        if key and (key in present or (stem and stem in present)):
            continue
        missing.append(video)
    return missing


def _listing_videos_for_lot(db: Session, lot: Lot) -> list[LotVideo]:
    rows = (
        db.query(LotVideo)
        .filter(LotVideo.lot_id == lot.id, LotVideo.account_id == lot.account_id)
        .order_by(LotVideo.sort_order.asc(), LotVideo.created_at.asc(), LotVideo.id.asc())
        .all()
    )
    return rows if isinstance(rows, list) else []


def _upload_listing_videos(
    *,
    client: CTSellerClient,
    lot: Lot,
    item_id: int,
    videos: list[LotVideo],
    phase: PhaseFn,
    order_start: int,
) -> tuple[int, str | None]:
    """Upload clips after photos. A failure here does not remove the item or its photos."""
    uploaded = 0
    notes: list[str] = []
    total = len(videos)
    for idx, video in enumerate(videos):
        phase(f"video {idx + 1}/{total}")
        name = Path(str(getattr(video, "original_name", "") or "video.mp4")).name or "video.mp4"
        try:
            path = resolve_catalog_storage_path(getattr(video, "storage_path", None))
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("CT Seller video resolve failed lot=%s video=%s: %s", lot.id, getattr(video, "id", idx), exc)
            notes.append(f"{name} is missing on the server")
            continue
        if path is None or not path.is_file():
            notes.append(f"{name} is missing on the server")
            continue
        try:
            client.add_item_video(
                item_id=item_id,
                video_bytes=path.read_bytes(),
                name=name,
                display_order=order_start + idx,
            )
            uploaded += 1
        except (OSError, CTSellerError) as exc:
            _LOG.warning("CT Seller video upload failed lot=%s video=%s: %s", lot.id, getattr(video, "id", idx), exc)
            notes.append(str(exc)[:240])
    note = " ".join(notes)[:500] if notes else None
    return uploaded, note


def next_ctseller_display_order(rows: list[dict]) -> int:
    top = 0
    for row in rows:
        raw = row.get("displayorder", row.get("display_order"))
        try:
            top = max(top, int(raw))
        except (TypeError, ValueError):
            continue
    return top + 1


def _upload_listing_photos(
    *,
    client: CTSellerClient,
    lot: Lot,
    item_id: int,
    listing_photos: list[Photo],
    phase: PhaseFn,
    order_start: int = 1,
    make_cover: bool = True,
) -> int:
    """Upload listing photos onto a CT Seller item. Returns how many landed."""
    uploaded = 0
    total_photos = len(listing_photos)
    workers = max(1, min(int(settings.ctseller_post_photo_workers), total_photos or 1))
    post_lock = threading.Lock()

    def _upload_one(idx: int, ph: Photo) -> bool:
        with post_lock:
            phase(f"photo {idx + 1}/{total_photos}")
        try:
            path = resolve_catalog_storage_path(ph.storage_path)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("CT Seller photo resolve failed lot=%s photo=%s: %s", lot.id, ph.id, exc)
            return False
        if path is None or not path.is_file():
            _LOG.warning("CT Seller photo missing lot=%s photo=%s", lot.id, getattr(ph, "id", idx))
            return False
        try:
            raw = jpeg_bytes_for_ctseller_post(path)
            name = ctseller_upload_name(ph.original_name or path.name, f"image{idx + 1}")
            with post_lock:
                client.add_item_image(
                    item_id=item_id,
                    image_bytes=raw,
                    name=name,
                    display_order=order_start + idx,
                    is_display_image=make_cover and idx == 0,
                )
            return True
        except (OSError, CTSellerError) as exc:
            _LOG.warning("CT Seller photo upload failed lot=%s photo=%s: %s", lot.id, ph.id, exc)
            return False

    if total_photos == 1 or workers == 1:
        return sum(1 for idx, ph in enumerate(listing_photos) if _upload_one(idx, ph))
    if total_photos > 1:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ct-photo") as pool:
            futs = [pool.submit(_upload_one, idx, ph) for idx, ph in enumerate(listing_photos)]
            uploaded = sum(1 for fut in as_completed(futs) if fut.result())
    return uploaded


def _listing_photos_for_lot(db: Session, lot: Lot) -> list[Photo]:
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
    return listing_photos


def add_lot_photos_on_ctseller(
    *,
    db: Session,
    lot: Lot,
    client: CTSellerClient,
    on_phase: PhaseFn | None = None,
) -> LotPostToCTSellerOut:
    """Add gallery photos that are not already on the CT Seller item. Does not remove photos or create an item."""

    def phase(msg: str) -> None:
        if on_phase:
            on_phase(msg)

    item_id = getattr(lot, "ctseller_item_id", None)
    if not item_id:
        raise HTTPException(
            status_code=400,
            detail="This lot is not on CT Seller yet. Post the lot first, then update photos.",
        )
    if is_facebook_listing_channel(getattr(lot, "listing_channel", None)):
        raise HTTPException(
            status_code=400,
            detail="This lot is marked for Facebook Marketplace — its photos stay off CT Seller",
        )

    listing_photos = _listing_photos_for_lot(db, lot)
    local_videos = _listing_videos_for_lot(db, lot)
    if not listing_photos and not local_videos:
        raise HTTPException(status_code=400, detail="This lot has no photos to send to CT Seller.")

    phase("reading CT Seller photos")
    existing = client.list_item_images(int(item_id))
    to_add = listing_photos_missing_on_ctseller(listing_photos, existing) if listing_photos else []
    videos = listing_videos_missing_on_ctseller(local_videos, existing)
    remote_sale_id = int(getattr(lot, "ctseller_sale_id", None) or 0)
    if not to_add and not videos:
        return LotPostToCTSellerOut(
            lot_id=lot.id,
            ctseller_item_id=int(item_id),
            ctseller_sale_id=remote_sale_id,
            photos_uploaded=0,
            videos_uploaded=0,
            ctseller_posted_at=getattr(lot, "ctseller_posted_at", None),
        )

    order_start = next_ctseller_display_order(existing)
    uploaded = 0
    if to_add:
        uploaded = _upload_listing_photos(
            client=client,
            lot=lot,
            item_id=int(item_id),
            listing_photos=to_add,
            phase=phase,
            order_start=order_start,
            make_cover=not ctseller_image_ids_to_replace(existing),
        )
        total = len(to_add)
        if uploaded < total:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Sent {uploaded} of {total} new photos to CT Seller item {item_id}. "
                    "Photos already on that item are still there. Click Add photos again."
                ),
            )

    videos_uploaded, video_note = (0, None)
    if videos:
        videos_uploaded, video_note = _upload_listing_videos(
            client=client,
            lot=lot,
            item_id=int(item_id),
            videos=videos,
            phase=phase,
            order_start=order_start + len(to_add),
        )

    if uploaded or videos_uploaded:
        now = datetime.now(timezone.utc)
        lot.ctseller_posted_at = now
        db.add(lot)
        db.commit()
        db.refresh(lot)
        posted_at = now
    else:
        posted_at = getattr(lot, "ctseller_posted_at", None)
    return LotPostToCTSellerOut(
        lot_id=lot.id,
        ctseller_item_id=int(item_id),
        ctseller_sale_id=remote_sale_id,
        photos_uploaded=uploaded,
        videos_uploaded=videos_uploaded,
        video_note=video_note,
        ctseller_posted_at=posted_at,
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

    listing_photos = _listing_photos_for_lot(db, lot)

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

    uploaded = _upload_listing_photos(
        client=client,
        lot=lot,
        item_id=int(item_id),
        listing_photos=listing_photos,
        phase=phase,
    )
    videos_uploaded, video_note = _upload_listing_videos(
        client=client,
        lot=lot,
        item_id=int(item_id),
        videos=_listing_videos_for_lot(db, lot),
        phase=phase,
        order_start=1 + len(listing_photos),
    )

    return LotPostToCTSellerOut(
        lot_id=lot.id,
        ctseller_item_id=int(item_id),
        ctseller_sale_id=int(remote_sale_id),
        photos_uploaded=uploaded,
        videos_uploaded=videos_uploaded,
        video_note=video_note,
        ctseller_posted_at=now,
    )
