from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, get_db
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
http_bearer_optional = HTTPBearer(auto_error=False)

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid authentication credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


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


def create_access_token(user_id: str, account_id: str, role: str, minutes: int | None = None) -> str:
    ttl = minutes if minutes is not None else settings.access_token_expire_minutes
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "account_id": account_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def user_from_access_token(token: str, db: Session) -> User:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            options={"require_sub": True, "verify_aud": False},
        )
        user_id = payload.get("sub")
        account_id = payload.get("account_id")
        if not user_id or not account_id:
            raise CREDENTIALS_EXCEPTION
    except JWTError as exc:
        raise CREDENTIALS_EXCEPTION from exc
    user = db.query(User).filter(User.id == user_id, User.account_id == account_id).first()
    if not user:
        raise CREDENTIALS_EXCEPTION
    return user


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    return user_from_access_token(token, db)


def get_current_user_sse(
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer_optional),
    access_token: str | None = Query(
        None,
        description="Same JWT as Authorization Bearer; required for browser EventSource (no header support).",
    ),
) -> User:
    """
    Short-lived DB session only. Do not use ``Depends(get_db)`` here: SSE requests stay open for minutes and
    would otherwise hold a pooled connection for the entire stream (pool exhaustion).
    """
    token = (credentials.credentials if credentials else None) or (access_token or "").strip()
    if not token:
        raise CREDENTIALS_EXCEPTION
    with SessionLocal() as db:
        return user_from_access_token(token, db)

