"""Build a printable PDF inventory for Facebook Marketplace lots."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Sequence
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _safe(text: object | None, *, fallback: str = "—") -> str:
    s = (str(text) if text is not None else "").strip()
    return s or fallback


def build_facebook_inventory_pdf(
    lots: Sequence[object],
    *,
    sale_name: str,
    generated_at: datetime | None = None,
) -> bytes:
    """Return PDF bytes: numbered inventory of Facebook-channel lots for one sale."""
    when = generated_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"Facebook Marketplace Inventory — {sale_name}",
        author="Estate-EZ",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "FbInvTitle",
        parent=styles["Heading1"],
        fontSize=16,
        leading=20,
        spaceAfter=4,
        textColor=colors.HexColor("#111111"),
    )
    sub_style = ParagraphStyle(
        "FbInvSub",
        parent=styles["Normal"],
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#444444"),
        spaceAfter=2,
    )
    cell_style = ParagraphStyle(
        "FbInvCell",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#111111"),
    )
    cell_right = ParagraphStyle(
        "FbInvCellRight",
        parent=cell_style,
        alignment=TA_RIGHT,
    )
    header_style = ParagraphStyle(
        "FbInvHeader",
        parent=cell_style,
        textColor=colors.white,
        fontName="Helvetica-Bold",
    )
    header_right = ParagraphStyle(
        "FbInvHeaderRight",
        parent=header_style,
        alignment=TA_RIGHT,
    )
    empty_style = ParagraphStyle(
        "FbInvEmpty",
        parent=styles["Normal"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#666666"),
        spaceBefore=18,
    )

    story: list = [
        Paragraph("Estate-EZ — Facebook Marketplace Inventory", title_style),
        Paragraph(f"Sale: {escape(_safe(sale_name, fallback='Untitled sale'))}", sub_style),
        Paragraph(
            f"Generated: {when.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
            f"{len(lots)} item{'s' if len(lots) != 1 else ''}",
            sub_style,
        ),
        Spacer(1, 0.2 * inch),
    ]

    if not lots:
        story.append(
            Paragraph("No Facebook Marketplace items in this sale yet.", empty_style)
        )
        doc.build(story)
        return buffer.getvalue()

    data: list[list] = [
        [
            Paragraph("#", header_style),
            Paragraph("Title", header_style),
            Paragraph("Price", header_right),
            Paragraph("Condition", header_style),
            Paragraph("Photos", header_right),
            Paragraph("Description", header_style),
        ]
    ]

    for idx, lot in enumerate(lots, start=1):
        title = escape(_safe(getattr(lot, "auction_title", None), fallback="Untitled"))
        condition = escape(_safe(getattr(lot, "condition_text", None)))
        desc = _safe(getattr(lot, "professional_description", None), fallback="")
        if len(desc) > 280:
            desc = desc[:277].rstrip() + "…"
        desc = escape(desc) if desc else "—"
        photo_count = int(getattr(lot, "photo_count", 0) or 0)
        data.append(
            [
                Paragraph(str(idx), cell_style),
                Paragraph(title, cell_style),
                Paragraph(_money(getattr(lot, "starting_price", None)), cell_right),
                Paragraph(condition, cell_style),
                Paragraph(str(photo_count), cell_right),
                Paragraph(desc, cell_style),
            ]
        )

    table = Table(
        data,
        colWidths=[
            0.35 * inch,
            1.85 * inch,
            0.7 * inch,
            1.1 * inch,
            0.55 * inch,
            2.55 * inch,
        ],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a1a")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f4")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#333333")),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return buffer.getvalue()
