"""Verbatim note appended to CT Seller descriptions. Not part of the AI prompt."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Account, Lot, Sale


def normalize_standard_description(raw: str | None) -> str:
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _paragraphs(text: str) -> list[str]:
    return [block.strip() for block in text.split("\n\n") if block.strip()]


def suffix_already_present(description: str | None, suffix: str | None) -> bool:
    """True when ``suffix`` is already its own trailing block or paragraph."""
    extra = normalize_standard_description(suffix)
    if not extra:
        return True
    body = (description or "").replace("\r\n", "\n").replace("\r", "\n").rstrip()
    if body == extra or body.endswith("\n\n" + extra) or body.endswith("\n" + extra):
        return True
    extra_parts = _paragraphs(extra)
    parts = _paragraphs(body)
    if not extra_parts or len(parts) < len(extra_parts):
        return False
    width = len(extra_parts)
    return any(parts[index : index + width] == extra_parts for index in range(len(parts) - width + 1))


def append_standard_description(description: str | None, suffix: str | None) -> str:
    """Append ``suffix`` verbatim. Leave the description alone when it is already there."""
    extra = normalize_standard_description(suffix)
    body = (description or "").replace("\r\n", "\n").replace("\r", "\n").rstrip()
    if not extra or suffix_already_present(body, extra):
        return body
    if not body:
        return extra
    return f"{body}\n\n{extra}"


def replace_trailing_standard_description(
    description: str | None,
    previous: str | None,
    new: str | None,
) -> str:
    """Swap a trailing copy of ``previous`` for ``new``, then append ``new`` if it is missing."""
    body = (description or "").replace("\r\n", "\n").replace("\r", "\n").rstrip()
    prev = normalize_standard_description(previous)
    if prev:
        parts = _paragraphs(body)
        prev_parts = _paragraphs(prev)
        if prev_parts and len(parts) >= len(prev_parts) and parts[-len(prev_parts) :] == prev_parts:
            body = "\n\n".join(parts[: -len(prev_parts)]).rstrip()
        elif body == prev:
            body = ""
        elif body.endswith("\n\n" + prev):
            body = body[: -(len(prev) + 2)].rstrip()
        elif body.endswith("\n" + prev):
            body = body[: -(len(prev) + 1)].rstrip()
    return append_standard_description(body, new)


def resolve_standard_description(sale: Any | None, account: Any | None) -> str:
    """Sale text wins, including an empty override that turns the account note off.

    ``None`` on the sale means inherit the account default.
    """
    if sale is not None and getattr(sale, "standard_description", None) is not None:
        return normalize_standard_description(getattr(sale, "standard_description", None))
    if account is None:
        return ""
    return normalize_standard_description(getattr(account, "standard_description", None))


def description_for_ctseller(db: Session, lot: Any) -> str:
    """Listing copy plus the account/sale note, for the CT Seller item payload."""
    account_id = getattr(lot, "account_id", None)
    sale_id = getattr(lot, "sale_id", None)
    sale = None
    account = None
    if account_id and sale_id:
        sale = (
            db.query(Sale)
            .filter(Sale.id == sale_id, Sale.account_id == account_id)
            .first()
        )
    if account_id:
        account = db.query(Account).filter(Account.id == account_id).first()
    return append_standard_description(
        getattr(lot, "professional_description", None) or "",
        resolve_standard_description(sale, account),
    )


def suffixes_by_sale(db: Session, account_id: str, sale_ids: set[str] | list[str]) -> dict[str, str]:
    wanted = {str(sid) for sid in sale_ids if sid}
    account = db.query(Account).filter(Account.id == account_id).first()
    if not wanted:
        return {}
    sales = db.query(Sale).filter(Sale.account_id == account_id, Sale.id.in_(wanted)).all()
    found = {row.id: resolve_standard_description(row, account) for row in sales}
    fallback = resolve_standard_description(None, account)
    return {sid: found.get(sid, fallback) for sid in wanted}


def apply_standard_description_to_unposted(
    db: Session,
    *,
    account_id: str,
    new_text: str,
    previous_text: str,
    sale_id: str | None = None,
    inherit_only: bool = False,
) -> int:
    """Write the note onto unposted lots. Posted CT Seller items are left alone."""
    query = db.query(Lot).filter(
        Lot.account_id == account_id,
        Lot.ctseller_item_id.is_(None),
        Lot.ctseller_posted_at.is_(None),
    )
    if sale_id:
        query = query.filter(Lot.sale_id == sale_id)
    rows = query.all()
    if inherit_only:
        overridden = {
            row.id
            for row in db.query(Sale).filter(Sale.account_id == account_id).all()
            if getattr(row, "standard_description", None) is not None
        }
        rows = [row for row in rows if row.sale_id not in overridden]
    updated = 0
    new_norm = normalize_standard_description(new_text)
    prev_norm = normalize_standard_description(previous_text)
    for lot in rows:
        current = lot.professional_description or ""
        nxt = replace_trailing_standard_description(current, prev_norm, new_norm)
        if nxt != current.rstrip():
            lot.professional_description = nxt
            db.add(lot)
            updated += 1
    return updated
