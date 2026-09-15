"""Printable inventory sheet: SKU, description, and a thumbnail per lot."""

from __future__ import annotations

import io
import re
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy.orm import Session

from app.catalog_storage import resolve_catalog_storage_path
from app.models import Lot, Photo, Sale
from app.services.sale_zip_export import pick_lot_hero_photo
from app.services.thumbnail_image import thumbnail_jpeg_bytes

PAGE_W, PAGE_H = 1275, 1650  # ~8.5x11 at 150 dpi
MARGIN = 48
THUMB = 168
ROW_H = 196
GAP = 12
HEADER_H = 92


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    names = (
        ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf")
        if bold
        else ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
    )
    roots = (
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype/liberation"),
        Path("/usr/share/fonts/truetype/freefont"),
    )
    for root in roots:
        for name in names:
            path = root / name
            if path.is_file():
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:
                    continue
    return ImageFont.load_default()


def _plain(text: str | None) -> str:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    return raw.strip()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int, max_lines: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    cur = ""
    leftover = False

    def width(s: str) -> int:
        box = draw.textbbox((0, 0), s, font=font)
        return box[2] - box[0]

    def ellipsize(s: str) -> str:
        t = s.rstrip()
        while t and width(t + "…") > max_w:
            t = t[:-1]
        return (t + "…") if t else "…"

    for i, word in enumerate(words):
        trial = word if not cur else f"{cur} {word}"
        if width(trial) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = word
        else:
            lines.append(ellipsize(word))
            cur = ""
        if len(lines) >= max_lines:
            leftover = i < len(words) - 1 or bool(cur)
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
        cur = ""
    elif cur:
        leftover = True
    if leftover and lines:
        lines[-1] = ellipsize(lines[-1].rstrip("…"))
    return lines[:max_lines]


def _load_thumb(photo: Photo | None) -> Image.Image:
    blank = Image.new("RGB", (THUMB, THUMB), (38, 38, 42))
    if photo is None:
        return blank
    try:
        src = resolve_catalog_storage_path(photo.storage_path)
        raw = thumbnail_jpeg_bytes(src, THUMB)
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail((THUMB, THUMB), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (THUMB, THUMB), (24, 24, 27))
        x = (THUMB - im.size[0]) // 2
        y = (THUMB - im.size[1]) // 2
        canvas.paste(im, (x, y))
        return canvas
    except Exception:
        return blank


def _sku_sort_key(lot: Lot) -> tuple:
    sku = (lot.sku or "").strip()
    return (sku.upper() == "", sku.upper(), lot.created_at or "")


def build_sale_inventory_pdf(
    db: Session,
    *,
    sale: Sale,
    account_id: str,
) -> bytes:
    lots = (
        db.query(Lot)
        .filter(Lot.account_id == account_id, Lot.sale_id == sale.id)
        .all()
    )
    lots = sorted(lots, key=_sku_sort_key)
    photos_by_lot: dict[str, list[Photo]] = defaultdict(list)
    if lots:
        rows = (
            db.query(Photo)
            .filter(Photo.account_id == account_id, Photo.lot_id.in_([lot.id for lot in lots]))
            .all()
        )
        for photo in rows:
            if photo.lot_id:
                photos_by_lot[photo.lot_id].append(photo)

    font_title = _font(28, bold=True)
    font_meta = _font(16)
    font_sku = _font(22, bold=True)
    font_head = _font(18, bold=True)
    font_body = _font(15)

    pages: list[Image.Image] = []
    page = Image.new("RGB", (PAGE_W, PAGE_H), (255, 255, 255))
    draw = ImageDraw.Draw(page)
    sale_name = (sale.name or "Sale").strip() or "Sale"
    draw.text((MARGIN, 28), sale_name, font=font_title, fill=(20, 20, 22))
    draw.text(
        (MARGIN, 64),
        f"Inventory sheet · {len(lots)} lot{'s' if len(lots) != 1 else ''} · SKU, photo, description",
        font=font_meta,
        fill=(80, 80, 86),
    )
    y = HEADER_H
    text_x = MARGIN + THUMB + 18
    text_w = PAGE_W - MARGIN - text_x

    def flush() -> None:
        nonlocal page, draw, y
        pages.append(page)
        page = Image.new("RGB", (PAGE_W, PAGE_H), (255, 255, 255))
        draw = ImageDraw.Draw(page)
        y = MARGIN

    if not lots:
        draw.text((MARGIN, y + 12), "No lots in this sale.", font=font_body, fill=(80, 80, 86))
        pages.append(page)
    else:
        for lot in lots:
            if y + ROW_H > PAGE_H - MARGIN:
                flush()
            hero = pick_lot_hero_photo(photos_by_lot.get(lot.id, []))
            thumb = _load_thumb(hero)
            page.paste(thumb, (MARGIN, y))
            sku = (lot.sku or "").strip() or "—"
            title = _plain(lot.auction_title) or "—"
            desc = _plain(lot.professional_description)
            draw.text((text_x, y), sku, font=font_sku, fill=(16, 16, 18))
            title_lines = _wrap(draw, title, font_head, text_w, 2)
            ty = y + 28
            for line in title_lines:
                draw.text((text_x, ty), line, font=font_head, fill=(32, 32, 36))
                ty += 22
            body_lines = _wrap(draw, desc, font_body, text_w, 5) if desc else ["No description."]
            for line in body_lines:
                draw.text((text_x, ty), line, font=font_body, fill=(55, 55, 60))
                ty += 18
            draw.line((MARGIN, y + ROW_H - 8, PAGE_W - MARGIN, y + ROW_H - 8), fill=(220, 220, 224), width=1)
            y += ROW_H + GAP
        pages.append(page)

    out = io.BytesIO()
    first, rest = pages[0], pages[1:]
    first.save(out, format="PDF", save_all=bool(rest), append_images=rest, resolution=150.0)
    return out.getvalue()
