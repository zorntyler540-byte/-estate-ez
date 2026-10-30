from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, get_db
from app.models import Account, User
from app.services.plans import enforce_watcher_http, request_path
from app.services.roles import enforce_client_http

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
http_bearer_optional = HTTPBearer(auto_error=False)

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid authentication credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

SSE_TICKET_PURPOSE = "job_sse"
ACCESS_TOKEN_PURPOSES = frozenset({None, "access"})


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > 72:
        raise ValueError("password exceeds bcrypt limit of 72 bytes")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > 72:
        return False
    try:
        return bcrypt.checkpw(raw, hashed.encode("ascii"))
    except (ValueError, OSError):
        return False


def token_version_of(user: User) -> int:
    return int(getattr(user, "token_version", 0) or 0)


def bump_token_version(user: User) -> int:
    user.token_version = token_version_of(user) + 1
    return user.token_version


def create_access_token(
    user_id: str,
    account_id: str,
    role: str,
    minutes: int | None = None,
    token_version: int = 0,
) -> str:
    ttl = minutes if minutes is not None else settings.access_token_expire_minutes
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "account_id": account_id,
        "role": role,
        "ver": int(token_version or 0),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def create_sse_ticket(user: User, job_id: str, minutes: int | None = None) -> str:
    ttl = minutes if minutes is not None else settings.sse_ticket_expire_minutes
    now = datetime.now(timezone.utc)
    payload = {
        "purpose": SSE_TICKET_PURPOSE,
        "sub": user.id,
        "account_id": user.account_id,
        "role": user.role,
        "job_id": job_id,
        "ver": token_version_of(user),
        "jti": secrets.token_urlsafe(16),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def _decode_hs256(token: str, *, require_sub: bool = True) -> dict:
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            options={"require_sub": require_sub, "verify_aud": False},
        )
    except JWTError as exc:
        raise CREDENTIALS_EXCEPTION from exc


def _load_active_user(db: Session, user_id: str, account_id: str, token_version: int) -> User:
    user = db.query(User).filter(User.id == user_id, User.account_id == account_id).first()
    if not user:
        raise CREDENTIALS_EXCEPTION
    if token_version_of(user) != int(token_version or 0):
        raise CREDENTIALS_EXCEPTION
    acct_active = (
        db.query(Account.is_active)
        .filter(Account.id == user.account_id)
        .scalar()
    )
    if acct_active is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")
    return user


def user_from_access_token(token: str, db: Session) -> User:
    payload = _decode_hs256(token, require_sub=True)
    if payload.get("purpose") not in ACCESS_TOKEN_PURPOSES:
        raise CREDENTIALS_EXCEPTION
    user_id = payload.get("sub")
    account_id = payload.get("account_id")
    if not user_id or not account_id:
        raise CREDENTIALS_EXCEPTION
    # Tokens minted before versioning omit ``ver``; treat missing as 0.
    try:
        ver = int(payload.get("ver") or 0)
    except (TypeError, ValueError) as exc:
        raise CREDENTIALS_EXCEPTION from exc
    return _load_active_user(db, str(user_id), str(account_id), ver)


def user_from_sse_ticket(token: str, db: Session, job_id: str) -> User:
    payload = _decode_hs256(token, require_sub=True)
    if payload.get("purpose") != SSE_TICKET_PURPOSE:
        raise CREDENTIALS_EXCEPTION
    if str(payload.get("job_id") or "") != str(job_id or ""):
        raise CREDENTIALS_EXCEPTION
    user_id = payload.get("sub")
    account_id = payload.get("account_id")
    if not user_id or not account_id:
        raise CREDENTIALS_EXCEPTION
    try:
        ver = int(payload.get("ver") or 0)
    except (TypeError, ValueError) as exc:
        raise CREDENTIALS_EXCEPTION from exc
    return _load_active_user(db, str(user_id), str(account_id), ver)


def enforce_account_http(db: Session, user: User, method: str, path: str) -> None:
    """Client upload lock, then Auction Watcher allowlist.

    Master admin keeps ``/admin/*`` even if that login's plan is watcher, so
    the admin panel cannot be locked by a plan change.
    """
    enforce_client_http(user, method, path)
    raw = request_path(path)
    if is_master_admin(user) and (raw == "/admin" or raw.startswith("/admin/")):
        return
    account = db.query(Account).filter(Account.id == user.account_id).first()
    enforce_watcher_http(account, method, path)


def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    user = user_from_access_token(token, db)
    enforce_account_http(db, user, request.method, request.url.path)
    return user


def get_current_user_upload(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer_optional),
) -> User:
    """Short-lived DB session for multipart catalog ingest (do not pin ``Depends(get_db)``)."""
    token = credentials.credentials if credentials else ""
    if not token:
        raise CREDENTIALS_EXCEPTION
    with SessionLocal() as db:
        user = user_from_access_token(token, db)
        enforce_account_http(db, user, request.method, request.url.path)
        return user


def get_current_user_sse(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer_optional),
    sse_ticket: str | None = Query(
        None,
        description="Short-lived job SSE ticket from POST /jobs/{id}/events-ticket.",
    ),
) -> User:
    """
    Short-lived DB session only. Do not use ``Depends(get_db)`` here: SSE requests stay open for minutes and
    would otherwise hold a pooled connection for the entire stream (pool exhaustion).
    Accepts Authorization Bearer or a single-purpose ``sse_ticket`` query (EventSource cannot set headers).
    """
    job_id = str(request.path_params.get("job_id") or "")
    bearer = credentials.credentials if credentials else ""
    ticket = (sse_ticket or "").strip()
    if ticket:
        with SessionLocal() as db:
            user = user_from_sse_ticket(ticket, db, job_id)
            enforce_account_http(db, user, request.method, request.url.path)
            return user
    if not bearer:
        raise CREDENTIALS_EXCEPTION
    with SessionLocal() as db:
        user = user_from_access_token(bearer, db)
        enforce_account_http(db, user, request.method, request.url.path)
        return user


def is_master_admin_email(email: str) -> bool:
    return (email or "").strip().lower() in set(settings.master_admin_email_list())


def is_reserved_master_admin_email(email: str) -> bool:
    """True when this mailbox is listed for master admin and must not be registered or invited."""
    return is_master_admin_email(email)


def is_master_admin(user: User) -> bool:
    uid = (getattr(user, "id", None) or "").strip()
    if uid and uid in set(settings.master_admin_user_id_list()):
        return True
    return is_master_admin_email(getattr(user, "email", "") or "")
