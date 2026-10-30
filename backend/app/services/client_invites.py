"""Hashed client-upload invites. The raw token is returned once and never stored."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth import bump_token_version, create_access_token, hash_password, token_version_of
from app.models import ClientInvite, ProcessingJob, Sale, User
from app.services.roles import ROLE_CLIENT, is_client_role

INVITE_LINK_DAYS = 14


def hash_invite_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def assert_client_access_open(user: User) -> None:
    """Expiry and revocation apply on every authenticated request."""
    if not is_client_role(getattr(user, "role", None)):
        return
    if getattr(user, "revoked_at", None) is not None:
        raise HTTPException(status_code=403, detail="This client login was revoked")
    expires = _aware(getattr(user, "access_expires_at", None))
    if expires is not None and datetime.now(timezone.utc) >= expires:
        raise HTTPException(status_code=403, detail="This client login has expired")


def create_client_invite(
    db: Session,
    *,
    owner: User,
    sale: Sale,
    email: str,
    access_expires_at: datetime | None,
) -> tuple[ClientInvite, str]:
    address = email.lower().strip()
    if not address:
        raise HTTPException(status_code=400, detail="Email is required")
    expires = _aware(access_expires_at)
    now = datetime.now(timezone.utc)
    if expires is not None and expires <= now:
        raise HTTPException(status_code=400, detail="Expiry must be in the future")

    existing = db.query(User).filter(User.email == address).first()
    if existing is not None and existing.account_id != owner.account_id:
        raise HTTPException(status_code=400, detail="Email already exists")
    if existing is not None and not is_client_role(existing.role):
        raise HTTPException(status_code=400, detail="That email is already a staff login")

    if existing is None:
        existing = User(
            account_id=owner.account_id,
            email=address,
            password_hash=hash_password(secrets.token_urlsafe(18)),
            role=ROLE_CLIENT,
            assigned_sale_id=sale.id,
            access_expires_at=expires,
        )
        db.add(existing)
        db.flush()
    else:
        existing.assigned_sale_id = sale.id
        existing.access_expires_at = expires
        existing.revoked_at = None
        bump_token_version(existing)

    raw = secrets.token_urlsafe(32)
    link_deadline = expires if expires is not None else now + timedelta(days=INVITE_LINK_DAYS)
    for prior in (
        db.query(ClientInvite)
        .filter(
            ClientInvite.account_id == owner.account_id,
            ClientInvite.user_id == existing.id,
            ClientInvite.revoked_at.is_(None),
        )
        .all()
    ):
        prior.revoked_at = now
    invite = ClientInvite(
        account_id=owner.account_id,
        sale_id=sale.id,
        user_id=existing.id,
        email=address,
        token_hash=hash_invite_token(raw),
        access_expires_at=expires,
        expires_at=link_deadline,
        created_by_user_id=owner.id,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite, raw


def redeem_client_invite(db: Session, token: str) -> str:
    digest = hash_invite_token(token.strip())
    invite = db.query(ClientInvite).filter(ClientInvite.token_hash == digest).first()
    if invite is None or invite.revoked_at is not None:
        raise HTTPException(status_code=403, detail="This invite link is not valid")
    now = datetime.now(timezone.utc)
    if _aware(invite.expires_at) is not None and now >= _aware(invite.expires_at):
        raise HTTPException(status_code=403, detail="This invite link has expired")
    user = db.query(User).filter(User.id == invite.user_id, User.account_id == invite.account_id).first()
    if user is None or not is_client_role(user.role):
        raise HTTPException(status_code=403, detail="This invite link is not valid")
    assert_client_access_open(user)
    if user.assigned_sale_id != invite.sale_id:
        raise HTTPException(status_code=403, detail="This invite link is not valid")
    if invite.accepted_at is None:
        invite.accepted_at = now
        db.commit()
    return create_access_token(
        user.id, user.account_id, user.role, token_version=token_version_of(user)
    )


def revoke_client_user(db: Session, *, account_id: str, user_id: str) -> User:
    user = db.query(User).filter(User.id == user_id, User.account_id == account_id).first()
    if user is None or not is_client_role(user.role):
        raise HTTPException(status_code=404, detail="Client login not found")
    now = datetime.now(timezone.utc)
    user.revoked_at = now
    bump_token_version(user)
    for invite in (
        db.query(ClientInvite)
        .filter(ClientInvite.account_id == account_id, ClientInvite.user_id == user.id, ClientInvite.revoked_at.is_(None))
        .all()
    ):
        invite.revoked_at = now
    db.commit()
    db.refresh(user)
    return user


def client_uploads_for_sale(db: Session, *, account_id: str, sale_id: str) -> list[tuple[ProcessingJob, str]]:
    rows = (
        db.query(ProcessingJob, User.email)
        .join(User, User.id == ProcessingJob.user_id)
        .filter(
            ProcessingJob.account_id == account_id,
            ProcessingJob.sale_id == sale_id,
            User.role == ROLE_CLIENT,
        )
        .order_by(ProcessingJob.created_at.desc())
        .all()
    )
    return [(job, email) for job, email in rows]
