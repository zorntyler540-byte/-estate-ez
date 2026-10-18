"""Encrypt Facebook Page/user tokens at rest (separate key namespace from CT Seller)."""

from __future__ import annotations

import base64
import hashlib
import logging

from app.config import settings

_LOG = logging.getLogger(__name__)


def _fernet_key() -> bytes:
    raw = (settings.meta_token_encryption_key or "").strip() or settings.secret_key
    digest = hashlib.sha256(f"meta-page:{raw}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def seal_facebook_secret(plaintext: str) -> str:
    text = (plaintext or "").strip()
    if not text:
        return ""
    try:
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key()).encrypt(text.encode("utf-8")).decode("ascii")
    except ImportError:
        raw = text.encode("utf-8")
        key = hashlib.sha256(f"meta-page:{settings.secret_key}".encode("utf-8")).digest()
        xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
        return "xor:" + base64.urlsafe_b64encode(xored).decode("ascii")


def unseal_facebook_secret(sealed: str | None) -> str:
    if not sealed:
        return ""
    if sealed.startswith("xor:"):
        key = hashlib.sha256(f"meta-page:{settings.secret_key}".encode("utf-8")).digest()
        xored = base64.urlsafe_b64decode(sealed[4:].encode("ascii"))
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(xored)).decode("utf-8")
    try:
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key()).decrypt(sealed.encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Facebook token decrypt failed: %s", exc)
        return ""
