"""Unofficial CT Seller (seller.ctbids.com) API client for posting lots from the site."""

from __future__ import annotations

import base64
import contextvars
import hashlib
import io
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from datetime import datetime

import requests
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener  # type: ignore[import-untyped]

    register_heif_opener()
except ImportError:
    pass

from app.config import settings
from app.services.ctseller_categories import (
    CT_SELLER_CATEGORY_DEFAULT,
    canonicalize_ct_category,
    infer_ct_area,
)
from app.services.ctseller_conditions import canonicalize_ct_condition
from app.services.search_tags import format_ctseller_tag_field

logger = logging.getLogger(__name__)

SELLER_API = "https://seller.ctbids.com/services/api/v1"
# seller.ctbids.com sits behind an AWS load balancer that returns an HTML 403
# unless the call carries a browser User-Agent plus the seller site Origin.
_SELLER_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Origin": "https://seller.ctbids.com",
    "Referer": "https://seller.ctbids.com/",
}
DEFAULT_TIMEOUT = 60.0
IMAGE_MAX_EDGE = 1600
IMAGE_JPEG_QUALITY = 85
# Sale covers post 4 raw-base64 JPEGs in one JSON body — keep them smaller than lot photos.
COVER_MAX_EDGE = 1200
COVER_RETRY_EDGE = 880
COVER_JPEG_QUALITY = 78
# Seller media tab: 1 MB fragments, 50 MB per clip. The cover image stays a photo.
CTSELLER_VIDEO_PART_BYTES = 1024 * 1024
CTSELLER_MAX_VIDEO_BYTES = 50 * 1024 * 1024


def ctseller_video_locator(*, filename: str, partkey: str, size: int, part_count: int) -> str:
    """Same token the seller site stores in ``image`` for a video (``btoa`` of the BIG… string)."""
    raw = f"BIG{filename}${partkey}${int(size)}${int(part_count)}"
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def _ctseller_partkey(body: dict[str, Any]) -> str:
    key = body.get("partkey")
    data = body.get("data")
    if not key and isinstance(data, dict):
        key = data.get("partkey")
    text = str(key or "").strip()
    if not text:
        raise CTSellerError("CT Seller did not return a video upload key")
    return text


class CTSellerError(Exception):
    """Raised when CT Seller returns an error or unexpected payload."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: Any = None,
        blank_keys: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.blank_keys = list(blank_keys or [])


def _fernet_key() -> bytes:
    """Derive a Fernet-compatible key from SECRET_KEY without a separate crypto secret."""
    digest = hashlib.sha256(f"ctseller:{settings.secret_key}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def seal_secret(plaintext: str) -> str:
    """Encrypt a short secret for DB storage (Fernet if cryptography is installed)."""
    text = (plaintext or "").strip()
    if not text:
        return ""
    try:
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key()).encrypt(text.encode("utf-8")).decode("ascii")
    except ImportError:
        # Dev fallback: obfuscation only — production should install cryptography.
        raw = text.encode("utf-8")
        key = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
        xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
        return "xor:" + base64.urlsafe_b64encode(xored).decode("ascii")


def unseal_secret(sealed: str | None) -> str:
    if not sealed:
        return ""
    if sealed.startswith("xor:"):
        key = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
        xored = base64.urlsafe_b64decode(sealed[4:].encode("ascii"))
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(xored)).decode("utf-8")
    try:
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key()).decrypt(sealed.encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 — treat any decrypt failure as missing secret
        logger.warning("CT Seller secret decrypt failed: %s", exc)
        return ""


def _btoa(s: str) -> str:
    """Match browser btoa() for Latin-1/ASCII passwords."""
    return base64.b64encode(s.encode("latin-1", errors="replace")).decode("ascii")


# Seller password login can return an mfatoken. The code itself is sent and
# checked on the CTBids MFA routes the seller sign-in already uses.
MFA_SEND_URL = "https://api.ctbids.com/services/api/v1/buyer/user/mfa/otp/send"
MFA_VERIFY_URL = "https://api.ctbids.com/services/api/v1/buyer/user/mfa/verify"
MFA_RESEND_URL = "https://api.ctbids.com/services/api/v1/buyer/user/mfa/otp/resend"
CT_SELLER_SITE = "https://seller.ctbids.com/"


class CTSellerMfaRequired(CTSellerError):
    """Password was accepted. CT Seller wants an email or text code."""

    def __init__(self, mfa_token: str, *, email: str = "", mobile: str = "") -> None:
        super().__init__("CT Seller needs a one-time code.")
        self.mfa_token = (mfa_token or "").strip()
        self.email = (email or "").strip()
        self.mobile = (mobile or "").strip()


def mfa_challenge_from_body(body: object) -> CTSellerMfaRequired | None:
    if not isinstance(body, dict):
        return None
    blobs: list[dict[str, Any]] = [body]
    data = body.get("data")
    if isinstance(data, dict):
        blobs.append(data)
    for blob in blobs:
        token = str(blob.get("mfatoken") or blob.get("mfaToken") or "").strip()
        kind = str(blob.get("type") or "").lower()
        email = str(blob.get("email") or "").strip()
        mobile = str(blob.get("mobile") or "").strip()
        if token and (kind in {"mfa", "otp"} or email or mobile):
            return CTSellerMfaRequired(token, email=email, mobile=mobile)
    return None


def _tokens_from_body(body: object) -> CTSellerTokens | None:
    if not isinstance(body, dict):
        return None
    blobs: list[dict[str, Any]] = [body]
    data = body.get("data")
    if isinstance(data, dict):
        blobs.append(data)
    access = ""
    refresh = ""
    for blob in blobs:
        if not access:
            for key in ("accessToken", "access_token", "newToken"):
                val = blob.get(key)
                if isinstance(val, str) and val.count(".") == 2:
                    access = val.strip()
                    break
        if not refresh:
            for key in ("refreshToken", "refresh_token", "newRefreshToken"):
                val = blob.get(key)
                if isinstance(val, str) and val.count(".") == 2:
                    refresh = val.strip()
                    break
    if not access:
        return None
    return CTSellerTokens(access_token=access, refresh_token=refresh)


def jwt_expiry_unix(token: str | None) -> float | None:
    """Return ``exp`` from a JWT access token, or None if the token is opaque / unreadable."""
    raw = (token or "").strip()
    parts = raw.split(".")
    if len(parts) != 3:
        return None
    try:
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except Exception:  # noqa: BLE001 — treat any decode failure as opaque
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    try:
        return float(exp) if exp is not None else None
    except (TypeError, ValueError):
        return None


def access_token_should_refresh(
    token: str | None,
    *,
    now: float | None = None,
    skew_seconds: int = 180,
) -> bool:
    """True when we should refresh before calling CT Seller.

    Missing token → refresh. Readable JWT past (or within ``skew_seconds`` of) ``exp``
    → refresh. Opaque tokens are left alone until a 401 forces a retry.
    """
    if not (token or "").strip():
        return True
    exp = jwt_expiry_unix(token)
    if exp is None:
        return False
    clock = time.time() if now is None else now
    return clock >= exp - max(0, int(skew_seconds))


def persist_ctseller_tokens(account: Any, tokens: CTSellerTokens) -> None:
    account.ctseller_access_token_enc = seal_secret(tokens.access_token)
    if tokens.refresh_token:
        account.ctseller_refresh_token_enc = seal_secret(tokens.refresh_token)


@dataclass
class CTSellerTokens:
    access_token: str
    refresh_token: str


@dataclass
class CTSellerSaleRow:
    id: int
    title: str
    status: str | None = None
    starttime: str | None = None
    endtime: str | None = None


def _parse_sale_endtime(raw: str | None) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    from datetime import timezone as tz

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[:19] if fmt != "%Y-%m-%d" else text[:10], fmt)
            return dt.replace(tzinfo=tz.utc)
        except ValueError:
            continue
    return None


class CTSellerClient:
    def __init__(self, access_token: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                **_SELLER_BROWSER_HEADERS,
            }
        )
        self.set_token(access_token)

    def set_token(self, access_token: str | None) -> None:
        self.access_token = (access_token or "").strip() or None
        if self.access_token:
            self.session.headers["Authorization"] = f"Bearer {self.access_token}"
        else:
            self.session.headers.pop("Authorization", None)

    def login(self, username: str, password: str, *, keep_logged_in: bool = True) -> CTSellerTokens:
        payload = {
            "data": {
                "username": username.strip(),
                "password": _btoa(password),
                "keep-user-logged-in": bool(keep_logged_in),
            }
        }
        resp = self.session.post(
            f"{SELLER_API}/auth/token",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        try:
            body = resp.json() if resp.content else {}
        except Exception:  # noqa: BLE001
            body = {}
        mfa = mfa_challenge_from_body(body)
        if mfa:
            raise mfa
        if resp.status_code >= 400:
            raise CTSellerError(
                _message_from_response(resp) or "CT Seller login failed",
                status_code=resp.status_code,
            )
        tokens = _tokens_from_body(body)
        if not tokens:
            raise CTSellerError("CT Seller login did not return an access token")
        self.set_token(tokens.access_token)
        return tokens

    def send_mfa(self, mfa_token: str, channel: str, *, resend: bool = False) -> None:
        token = (mfa_token or "").strip()
        if not token:
            raise CTSellerError("Sign in again — the one-time-code session expired.")
        url = MFA_RESEND_URL if resend else MFA_SEND_URL
        kind = "email" if (channel or "").strip().lower() in {"email", "mail"} else "sms"
        tried = [kind, "mobile"] if kind == "sms" else [kind]
        last = "Could not send the code."
        for type_val in tried:
            resp = self.session.post(
                url,
                data=json.dumps({"data": {"mfatoken": token, "type": type_val}}),
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            try:
                body = resp.json() if resp.content else {}
            except Exception:  # noqa: BLE001
                body = {}
            if not isinstance(body, dict):
                body = {}
            if resp.status_code < 400 and str(body.get("status") or "").lower() != "failed":
                return
            last = _coerce_message(body.get("message")) or last
            if "invalid" in last.lower() and "type" in last.lower():
                continue
            if resp.status_code < 400:
                continue
            if "CBMFA" in json.dumps(body):
                break
        raise CTSellerError(last)

    def verify_mfa(self, mfa_token: str, otp: str) -> CTSellerTokens:
        token = (mfa_token or "").strip()
        code = "".join(ch for ch in (otp or "") if ch.isalnum())
        if not token:
            raise CTSellerError("Sign in again — the one-time-code session expired.")
        if len(code) < 4:
            raise CTSellerError("Enter the code CT Seller sent.")
        payloads = [
            {"data": {"mfatoken": token, "otp": code, "isTrustedMode": True}},
            {"data": {"mfatoken": token, "otp": code}},
            {"data": {"mfatoken": token, "code": code, "isTrustedMode": True}},
        ]
        last = "That code was not accepted."
        for payload in payloads:
            resp = self.session.post(
                MFA_VERIFY_URL,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            try:
                body = resp.json() if resp.content else {}
            except Exception:  # noqa: BLE001
                body = {}
            if resp.status_code >= 400:
                last = _coerce_message(body.get("message") if isinstance(body, dict) else None) or last
                continue
            tokens = _tokens_from_body(body)
            if tokens:
                self.set_token(tokens.access_token)
                return tokens
            if mfa_challenge_from_body(body):
                last = "CT Seller still wants a code. Request a new one."
                continue
            last = _coerce_message(body.get("message") if isinstance(body, dict) else None) or last
        raise CTSellerError(last)

    def refresh(self, refresh_token: str) -> str:
        """Refresh access token; returns new access token."""
        return self.refresh_tokens(refresh_token).access_token

    def refresh_tokens(self, refresh_token: str) -> CTSellerTokens:
        """Refresh the session. Persist a rotated refresh token when CT Seller sends one."""
        headers = {
            "Authorization": f"Bearer {(refresh_token or '').strip()}",
            "Content-Type": "application/json",
        }
        resp = self.session.get(f"{SELLER_API}/token/refresh", headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            raise CTSellerError(
                _message_from_response(resp) or "CT Seller token refresh failed",
                status_code=resp.status_code,
            )
        body = resp.json() if resp.content else {}
        new_token = (
            body.get("newToken") or body.get("accessToken") or body.get("access_token") or ""
        ).strip()
        if not new_token:
            raise CTSellerError("CT Seller refresh did not return a token")
        new_refresh = (
            body.get("refreshToken")
            or body.get("refresh_token")
            or body.get("newRefreshToken")
            or ""
        ).strip()
        self.set_token(new_token)
        return CTSellerTokens(access_token=new_token, refresh_token=new_refresh)

    def _list_sales(
        self,
        *,
        extra_filters: list[dict[str, Any]],
        sort_field: str,
        sort_direction: str,
        skip_statuses: set[str] | None = None,
        max_pages: int = 15,
    ) -> list[CTSellerSaleRow]:
        """Paginate ``POST /sale/list`` with the same filter language as the CT Seller SPA."""
        base_filter = [
            {"field": "isDeleted", "value": 1, "op": "!=", "join": "AND"},
            *extra_filters,
        ]
        fields = ["id", "title", "status", "starttime", "endtime", "tax", "buyerspremium"]
        skip = {s.lower() for s in (skip_statuses or set())}
        out: list[CTSellerSaleRow] = []
        seen: set[int] = set()
        next_key: str | None = None
        for _ in range(max_pages):
            page: dict[str, Any] = {"size": 100}
            if next_key:
                page["next"] = next_key
            payload: dict[str, Any] = {
                "filter": base_filter,
                "field": fields,
                "sort": [{"field": sort_field, "direction": sort_direction}],
                "page": page,
            }
            body = self._post_raw("/sale/list", payload)
            data = body.get("data")
            rows = data if isinstance(data, list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    sid = int(row.get("id"))
                except (TypeError, ValueError):
                    continue
                if sid in seen:
                    continue
                seen.add(sid)
                status = str(row.get("status") or "").strip() or None
                if status and status.lower() in skip:
                    continue
                out.append(
                    CTSellerSaleRow(
                        id=sid,
                        title=str(row.get("title") or f"Sale {sid}"),
                        status=status,
                        starttime=str(row.get("starttime") or "") or None,
                        endtime=str(row.get("endtime") or "") or None,
                    )
                )
            page_meta = body.get("page") if isinstance(body.get("page"), dict) else {}
            nxt = None
            if isinstance(page_meta, dict):
                keyset = page_meta.get("keyset")
                if isinstance(keyset, dict):
                    nxt = keyset.get("next") or None
            if not nxt or not rows:
                break
            next_key = str(nxt)
        return out

    def list_open_sales(self) -> list[CTSellerSaleRow]:
        """
        Sales still open for cataloging — same idea as CT Seller Sales list \"active\":
        not deleted and endtime still in the future (includes Pending / not-yet-started).
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return self._list_sales(
            extra_filters=[
                # Match SaleList \"active\" tab: endtime > now (Pending + Started).
                {"field": "endtime", "value": now, "op": ">", "join": "AND"},
            ],
            sort_field="starttime",
            sort_direction="asc",
            skip_statuses={"closed", "inactive"},
        )

    def list_ended_sales(
        self,
        *,
        ended_after: datetime | None = None,
        ended_before: datetime | None = None,
        limit: int = 30,
    ) -> list[CTSellerSaleRow]:
        """Closed / ended auctions from the CT Seller Sales history list (endtime in range)."""
        from datetime import datetime, timezone

        end = ended_before or datetime.now(timezone.utc)
        extra: list[dict[str, Any]] = [
            {
                "field": "endtime",
                "value": end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                if end.tzinfo
                else end.strftime("%Y-%m-%d %H:%M:%S"),
                "op": "<=",
                "join": "AND",
            }
        ]
        rows = self._list_sales(
            extra_filters=extra,
            sort_field="endtime",
            sort_direction="desc",
            max_pages=20,
        )
        parsed: list[CTSellerSaleRow] = []
        after = None
        if ended_after is not None:
            after = (
                ended_after.astimezone(timezone.utc)
                if ended_after.tzinfo
                else ended_after.replace(tzinfo=timezone.utc)
            )
        for row in rows:
            if after is None:
                parsed.append(row)
                continue
            ended_at = _parse_sale_endtime(row.endtime)
            if ended_at is None or ended_at >= after:
                parsed.append(row)
        cap = max(1, min(int(limit), 400))
        return parsed[:cap]

    def sale_detail(self, sale_id: int) -> dict[str, Any]:
        payload = {
            "field": [
                "id",
                "locationid",
                "status",
                "saleitemreceiptmethod",
                "endtime",
                "timezonename",
                # SPA copies these onto each new item's Settings tab (setBuyerTax).
                "tax",
                "buyerspremium",
            ]
        }
        data = self._post_json(f"/sale/detail/{int(sale_id)}", payload)
        if isinstance(data, list) and data:
            row = data[0]
            if isinstance(row, dict):
                return row
        if isinstance(data, dict):
            return data
        raise CTSellerError(f"CT Seller sale {sale_id} not found")

    def list_areas(self) -> list[str]:
        """Location Area dropdown values (GET /category/area/list)."""
        resp = self.session.get(f"{SELLER_API}/category/area/list", timeout=self.timeout)
        if resp.status_code >= 400:
            raise CTSellerError(
                _message_from_response(resp) or "Could not load CT Seller areas",
                status_code=resp.status_code,
            )
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise CTSellerError("CT Seller areas response was not JSON") from exc
        data = body.get("data") if isinstance(body, dict) else None
        rows: Any = data
        if isinstance(data, dict):
            rows = data.get("data")
        if not isinstance(rows, list):
            return []
        out: list[str] = []
        for row in rows:
            if isinstance(row, str) and row.strip():
                out.append(row.strip())
            elif isinstance(row, dict):
                name = str(row.get("area") or row.get("name") or row.get("title") or "").strip()
                if name:
                    out.append(name)
        return out

    def create_item(self, item: dict[str, Any]) -> int:
        body = self._post_raw("/item/add", {"data": item})
        data = body.get("data")
        row: dict[str, Any] | None = None
        if isinstance(data, list) and data and isinstance(data[0], dict):
            row = data[0]
        elif isinstance(data, dict):
            row = data
        if not row or row.get("id") is None:
            raise CTSellerError("CT Seller item/add did not return an item id")
        return int(row["id"])

    def add_item_image(
        self,
        *,
        item_id: int,
        image_bytes: bytes,
        name: str,
        display_order: int,
        is_display_image: bool,
        mime: str = "image/jpeg",
    ) -> None:
        b64 = _image_to_base64(image_bytes, mime=mime)
        payload = {
            "data": {
                "itemid": int(item_id),
                "name": (name or "image")[:80],
                "image": b64,
                "type": "image",
                "isdisplayimage": 1 if is_display_image else 0,
                "displayorder": int(display_order),
            }
        }
        self._post_raw("/item/image/add", payload)

    def add_item_video(
        self,
        *,
        item_id: int,
        video_bytes: bytes,
        name: str,
        display_order: int,
    ) -> None:
        """Attach a clip the way the seller media tab does.

        The site starts a multipart upload, sends 1 MB base64 fragments, then
        posts ``/item/image/add`` with ``type`` ``video`` and ``isdisplayimage`` 0
        so the cover stays a photo.
        """
        filename = Path(name or "video.mp4").name.strip() or "video.mp4"
        size = len(video_bytes)
        if size <= 0:
            raise CTSellerError(f"{filename} is an empty video")
        if size > CTSELLER_MAX_VIDEO_BYTES:
            mb = CTSELLER_MAX_VIDEO_BYTES // (1024 * 1024)
            raise CTSellerError(
                f"{filename} is {size / (1024 * 1024):.1f} MB. CT Seller accepts videos up to {mb} MB."
            )
        part_size = CTSELLER_VIDEO_PART_BYTES
        part_count = max(1, math.ceil(size / part_size))
        started = self._post_raw(
            "/file/upload",
            {"data": {"filename": filename, "size": size, "partCount": part_count}},
        )
        partkey = _ctseller_partkey(started)
        for index in range(part_count):
            chunk = video_bytes[index * part_size : (index + 1) * part_size]
            self._post_raw(
                "/file/upload/fragment",
                {
                    "data": {
                        "partkey": partkey,
                        "partid": index + 1,
                        "content": base64.b64encode(chunk).decode("ascii"),
                    }
                },
            )
        locator = ctseller_video_locator(
            filename=filename,
            partkey=partkey,
            size=size,
            part_count=part_count,
        )
        self._post_raw(
            "/item/image/add",
            {
                "data": {
                    "itemid": int(item_id),
                    "name": filename,
                    "image": locator,
                    "type": "video",
                    "isdisplayimage": 0,
                    "displayorder": int(display_order),
                }
            },
        )

    def list_item_images(self, item_id: int) -> list[dict[str, Any]]:
        """Photos and clips already on a CT Seller item, in display order."""
        payload = {
            "filter": [{"field": "itemid", "value": int(item_id), "op": "=", "join": "AND"}],
            "sort": [{"field": "displayorder", "direction": "asc"}],
        }
        body = self._post_raw("/item/image/list", payload)
        data = body.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            inner = data.get("data")
            if isinstance(inner, list):
                return [row for row in inner if isinstance(row, dict)]
        return []

    def delete_item_images(self, item_id: int, image_ids: list[int]) -> None:
        """Remove existing item photos. Matches the seller site DELETE /item/image/delete."""
        ids = [int(x) for x in image_ids]
        if not ids:
            return
        payload = {"data": {"imageids": ids, "itemid": int(item_id)}}
        self._delete_raw("/item/image/delete", payload)

    def item_detail(self, item_id: int) -> dict[str, Any]:
        payload = {
            "field": [
                "id",
                "saleid",
                "SKU",
                "title",
                "description",
                "startingprice",
                "reserveprice",
                "buynowprice",
                "itemstatus",
                "currentbid",
                "highbid",
                "winningbid",
                "soldprice",
                "category",
                "area",
                "condition",
                "locationid",
            ]
        }
        data = self._post_json(f"/item/detail/{int(item_id)}", payload)
        if isinstance(data, list) and data:
            row = data[0]
            if isinstance(row, dict):
                return row
        if isinstance(data, dict):
            return data
        raise CTSellerError(f"CT Seller item {item_id} not found")

    def list_sale_items(self, sale_id: int) -> list[dict[str, Any]]:
        """Items on one CT Seller auction (sold / unsold / pending), paginated."""
        fields = [
            "id",
            "saleid",
            "SKU",
            "title",
            "itemstatus",
            "currentbid",
            "highbid",
            "winningbid",
            "soldprice",
            "reserveprice",
            "category",
        ]
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        next_key: str | None = None
        for _ in range(20):
            page: dict[str, Any] = {"size": 100}
            if next_key:
                page["next"] = next_key
            payload: dict[str, Any] = {
                "filter": [{"field": "saleid", "value": int(sale_id), "op": "=", "join": "AND"}],
                "field": fields,
                "sort": [{"field": "id", "direction": "asc"}],
                "page": page,
            }
            body = self._post_raw("/item/list", payload)
            data = body.get("data")
            rows = data if isinstance(data, list) else []
            if isinstance(data, dict):
                inner = data.get("data")
                rows = inner if isinstance(inner, list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    iid = int(row.get("id"))
                except (TypeError, ValueError):
                    continue
                if iid in seen:
                    continue
                seen.add(iid)
                out.append(row)
            page_meta = body.get("page") if isinstance(body.get("page"), dict) else {}
            nxt = None
            if isinstance(page_meta, dict):
                keyset = page_meta.get("keyset")
                if isinstance(keyset, dict):
                    nxt = keyset.get("next") or None
            if not nxt or not rows:
                break
            next_key = str(nxt)
        return out

    def update_item_reserve(self, *, item_id: int, reserve_price: float | None) -> dict[str, Any]:
        """Set ``reserveprice`` on an existing item without re-posting photos.

        Item detail omits ``itemtypeid``. CT Seller rejects an update that lacks it
        ("Not found : Update key or data missing"), which is why a reserve saved here
        never appeared on an already-posted item. Bid result fields are read-only and
        are not sent back.
        """
        detail: dict[str, Any] = {}
        try:
            fetched = self.item_detail(int(item_id))
            if isinstance(fetched, dict):
                detail = fetched
        except CTSellerError:
            detail = {}
        skip = {
            "currentbid",
            "highbid",
            "winningbid",
            "soldprice",
            "currentbidamount",
            "bidcount",
        }
        row: dict[str, Any] = {}
        for key, value in detail.items():
            if key in skip or isinstance(value, (dict, list)):
                continue
            row[key] = value
        row["id"] = int(item_id)
        raw_type = row.get("itemtypeid")
        try:
            row["itemtypeid"] = int(raw_type) if raw_type not in (None, "") else 2
        except (TypeError, ValueError):
            row["itemtypeid"] = 2
        row["reserveprice"] = format_ctseller_reserve_price(reserve_price)
        return self._put_raw("/item/update", {"data": [row]})

    def list_sale_images(self, *, sale_id: int) -> list[dict[str, Any]]:
        """Sale-level images (cover / profile) for one CT Seller auction."""
        payload = {"filter": [{"field": "saleid", "value": int(sale_id), "op": "=", "join": "AND"}]}
        body = self._post_raw("/sale/image/list", payload)
        data = body.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            inner = data.get("data")
            if isinstance(inner, list):
                return [row for row in inner if isinstance(row, dict)]
        return []

    def upsert_sale_cover_photos(
        self,
        *,
        sale_id: int,
        primary_jpeg: bytes,
        secondary_jpegs: list[bytes],
        existing_id: int | None = None,
    ) -> dict[str, Any]:
        """Write the 4 sale cover photos the SPA requires before items can move to Ready.

        Matches seller.ctbids.com ``submitImage`` / ``setImage``: primary on ``image``,
        three secondaries on ``itemimageurl``, each raw JPEG base64 with no ``data:``
        prefix (the crop editor FileReader strips ``data:...;base64,``). New covers
        POST ``/sale/image/add``; existing profile rows PUT update.
        """
        if len(secondary_jpegs) != 3:
            raise CTSellerError("CT Seller sale covers need exactly 3 secondary photos")

        def _row(edge: int, eid: int | None) -> dict[str, Any]:
            row: dict[str, Any] = {
                "saleid": int(sale_id),
                "name": "profile",
                "type": "image",
                "image": sale_cover_base64(primary_jpeg, max_edge=edge),
                "itemimageurl": [sale_cover_base64(raw, max_edge=edge) for raw in secondary_jpegs],
            }
            if eid is not None:
                row["id"] = int(eid)
            return row

        def _send(edge: int, eid: int | None) -> dict[str, Any]:
            row = _row(edge, eid)
            if eid is not None:
                return self._put_raw("/sale/image/update", {"data": [row]})
            return self._post_raw("/sale/image/add", {"data": [row]})

        eid = int(existing_id) if existing_id is not None else None
        last_exc: CTSellerError | None = None
        for edge in (COVER_MAX_EDGE, COVER_RETRY_EDGE):
            try:
                return _send(edge, eid)
            except CTSellerError as exc:
                last_exc = exc
                if not _is_retryable_seller_error(exc):
                    break
                logger.warning(
                    "CT Seller cover upload failed (sale=%s edge=%s id=%s): %s",
                    sale_id,
                    edge,
                    eid,
                    exc,
                )
                try:
                    profile = pick_profile_sale_image(self.list_sale_images(sale_id=int(sale_id)))
                    if profile and profile.get("id") is not None:
                        eid = int(profile["id"])
                    else:
                        eid = None
                except CTSellerError:
                    eid = None if eid is not None else eid
                time.sleep(1.0)
        raise CTSellerError(
            _friendly_cover_upload_error(last_exc or CTSellerError("CT Seller cover upload failed")),
            status_code=getattr(last_exc, "status_code", None),
        ) from last_exc

    def _delete_raw(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._write_raw("delete", path, payload)

    def _put_raw(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._write_raw("put", path, payload)

    def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        body = self._post_raw(path, payload)
        return body.get("data")

    def _post_raw(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._write_raw("post", path, payload)

    def _write_raw(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.access_token:
            raise CTSellerError("Not signed in to CT Seller", status_code=401)
        send = getattr(self.session, method)
        resp = send(
            f"{SELLER_API}{path}",
            data=json.dumps(payload),
            timeout=self.timeout,
        )
        try:
            return self._parse_seller_response(resp)
        except CTSellerError as exc:
            _log_ctseller_write_failure(method=method, path=path, payload=payload, resp=resp, exc=exc)
            raise

    def _parse_seller_response(self, resp: requests.Response) -> dict[str, Any]:
        if resp.status_code in (401, 403):
            raise CTSellerError("CT Seller session expired — reconnect on Account", status_code=resp.status_code)
        if resp.status_code >= 400:
            raise CTSellerError(
                _message_from_response(resp) or f"CT Seller request failed ({resp.status_code})",
                status_code=resp.status_code,
            )
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise CTSellerError("CT Seller returned non-JSON") from exc
        if not isinstance(body, dict):
            raise CTSellerError("CT Seller returned unexpected payload")
        # Some endpoints put errors in message with HTTP 200
        status = body.get("status")
        if status and status not in ("success", "ok", True):
            text = _failure_message(body)
            if text or body.get("message"):
                raise CTSellerError(text or "CT Seller error")
        return body


def _message_from_response(resp: requests.Response) -> str:
    text = resp.text or ""
    if "<html" in text[:300].lower() or "<title>403 forbidden" in text.lower():
        return "CT Seller refused the sign-in. Try again in a moment."
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return _redact_text(text)[:2000]
    if isinstance(body, dict):
        return _failure_message(body) or _redact_text(text)[:2000]
    return _coerce_message(body) or _redact_text(text)[:2000]


def _is_retryable_seller_error(exc: CTSellerError) -> bool:
    code = int(exc.status_code or 0)
    if code >= 500 or code == 429:
        return True
    msg = str(exc).lower()
    return "server problem" in msg or "uh oh" in msg or "timeout" in msg or "timed out" in msg


def _friendly_cover_upload_error(exc: CTSellerError) -> str:
    raw = str(exc).strip()
    if _is_retryable_seller_error(exc) or "server problem" in raw.lower() or "uh oh" in raw.lower():
        return (
            "CT Seller hit a server error while saving the 4 sale covers. "
            "Your picks are still saved here — click Push covers to CT Seller again."
        )
    return raw or "CT Seller cover upload failed"


def _coerce_message(message: Any) -> str:
    """Join every CT message, keeping field names when CT sends a list of them."""
    seen: list[str] = []
    for part in _message_parts(message):
        if part not in seen:
            seen.append(part)
    return "; ".join(seen)


def _message_parts(message: Any) -> list[str]:
    if message is None:
        return []
    if isinstance(message, str):
        text = message.strip()
        return [text] if text else []
    if isinstance(message, list):
        out: list[str] = []
        for item in message:
            out.extend(_message_parts(item))
        return out
    if isinstance(message, dict):
        field_name = str(message.get("field") or message.get("name") or message.get("key") or "").strip()
        texts: list[str] = []
        for key in ("message", "msg", "error"):
            if key in message:
                texts.extend(_message_parts(message.get(key)))
        if not texts:
            return [field_name] if field_name else []
        if field_name:
            return [f"{field_name}: {text}" for text in texts]
        return texts
    text = str(message).strip()
    return [text] if text else []


def _looks_like_field_errors(data: Any) -> bool:
    if isinstance(data, dict):
        return any(key in data for key in ("field", "message", "msg", "error"))
    if isinstance(data, list) and data:
        return all(
            isinstance(item, str) or (isinstance(item, dict) and any(key in item for key in ("field", "message", "msg", "error")))
            for item in data
        )
    return False


def _failure_message(body: Any) -> str:
    """User-facing text: every message and field name CT returned."""
    if not isinstance(body, dict):
        return _coerce_message(body)
    chunks: list[Any] = [body.get("message")]
    for key in ("errors", "error", "fieldErrors", "fields"):
        if key in body:
            chunks.append(body.get(key))
    data = body.get("data")
    if _looks_like_field_errors(data):
        chunks.append(data)
    return _coerce_message(chunks)


class _WriteLogCtx:
    def __init__(
        self,
        *,
        sale_id: str | None = None,
        ct_auction_id: int | None = None,
        sku: str | None = None,
    ) -> None:
        self.sale_id = sale_id
        self.ct_auction_id = ct_auction_id
        self.sku = sku


_write_log_ctx: contextvars.ContextVar[_WriteLogCtx | None] = contextvars.ContextVar(
    "ctseller_write_log",
    default=None,
)

_SECRET_KEY = re.compile(r"(password|token|authorization|secret|otp|mfa|cookie|bearer|refresh)", re.IGNORECASE)
_BULK_KEY = re.compile(r"(image|content|filedata|photo)", re.IGNORECASE)
_BEARER_TEXT = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{8,}")
_JWT_TEXT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_LOG_BODY_MAX = 4000


def enter_ctseller_write_log(
    *,
    sale_id: str | None = None,
    ct_auction_id: int | None = None,
    sku: str | None = None,
) -> contextvars.Token:
    """Remember Estate-EZ sale id / CT auction id for the next CT write log line."""
    return _write_log_ctx.set(_WriteLogCtx(sale_id=sale_id, ct_auction_id=ct_auction_id, sku=sku))


def reset_ctseller_write_log(token: contextvars.Token) -> None:
    _write_log_ctx.reset(token)


def set_ctseller_write_sku(sku: str | None) -> None:
    ctx = _write_log_ctx.get()
    if ctx is not None:
        ctx.sku = (sku or "").strip() or None


def _is_blank_payload_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if value == [] or value == {}:
        return True
    return False


def _payload_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def blank_payload_keys(payload: Any) -> list[str]:
    """Names of item fields we sent empty. Secret-looking keys are omitted."""
    found: list[str] = []
    for row in _payload_rows(payload):
        for key, value in row.items():
            name = str(key)
            if _SECRET_KEY.search(name):
                continue
            if _is_blank_payload_value(value) and name not in found:
                found.append(name)
    return found


def _field_types(payload: Any) -> str:
    """Compact key:type list for a failed write. No values, secrets, or image bytes."""
    parts: list[str] = []
    for row in _payload_rows(payload):
        for key, value in row.items():
            name = str(key)
            if _SECRET_KEY.search(name) or _BULK_KEY.search(name):
                continue
            if value is None:
                kind = "null"
            elif isinstance(value, bool):
                kind = "bool"
            elif isinstance(value, (int, float)):
                kind = "num"
            elif isinstance(value, str):
                kind = "blank" if not value.strip() else "str"
            elif isinstance(value, dict):
                kind = "obj"
            elif isinstance(value, list):
                kind = "list"
            else:
                kind = type(value).__name__
            parts.append(f"{name}:{kind}")
    return ",".join(parts) or "-"


_SNAPSHOT_KEYS = (
    "category",
    "area",
    "condition",
    "tax",
    "buyerspremium",
    "istaxable",
    "isshippable",
    "itemtypeid",
    "startingprice",
    "quantity",
    "locationid",
    "itemstatus",
    "itemreceiptmethod",
    "SKU",
    "title",
)


def _item_add_snapshot(payload: Any) -> str:
    """Sale-level values from a failed item/add. Description text is omitted."""
    snap: dict[str, Any] = {}
    for row in _payload_rows(payload):
        for key in _SNAPSHOT_KEYS:
            if key not in row:
                continue
            value = row.get(key)
            if isinstance(value, str) and len(value) > 80:
                value = value[:80] + "…"
            snap[key] = value
        desc = row.get("description")
        if isinstance(desc, str):
            snap["description_len"] = len(desc)
    try:
        return json.dumps(snap, default=str, ensure_ascii=False)
    except TypeError:
        return str(snap)


def _first_payload_value(payload: Any, *keys: str) -> str:
    for row in _payload_rows(payload):
        for key in keys:
            raw = row.get(key)
            if raw is None:
                continue
            text = str(raw).strip()
            if text:
                return text
    return ""


def _redact_text(text: str) -> str:
    text = _BEARER_TEXT.sub(r"\1[redacted]", text)
    return _JWT_TEXT.sub("[redacted]", text)


def _scrub_for_log(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _SECRET_KEY.search(name):
                out[name] = "[redacted]"
            elif _BULK_KEY.search(name):
                out[name] = "[omitted]"
            else:
                out[name] = _scrub_for_log(item, depth + 1)
        return out
    if isinstance(value, list):
        return [_scrub_for_log(item, depth + 1) for item in value[:40]]
    if isinstance(value, str):
        text = _redact_text(value)
        if len(text) > 500:
            return text[:500] + "…"
        return text
    return value


def _response_body_for_log(resp: requests.Response | None) -> Any:
    if resp is None:
        return None
    try:
        parsed = resp.json()
    except Exception:  # noqa: BLE001
        parsed = None
    if parsed is not None:
        return _scrub_for_log(parsed)
    return _redact_text(resp.text or "")[:_LOG_BODY_MAX]


def _log_ctseller_write_failure(
    *,
    method: str,
    path: str,
    payload: dict[str, Any],
    resp: requests.Response | None,
    exc: CTSellerError,
) -> None:
    """Log a CT write failure. Never includes credentials, tokens, or image bytes."""
    ctx = _write_log_ctx.get()
    sku = _first_payload_value(payload, "SKU", "sku") or (ctx.sku if ctx else "") or "-"
    estate_sale_id = (ctx.sale_id if ctx and ctx.sale_id else "") or "-"
    auction = _first_payload_value(payload, "saleid", "sale_id")
    if not auction and ctx and ctx.ct_auction_id is not None:
        auction = str(ctx.ct_auction_id)
    blank = blank_payload_keys(payload)
    body = _response_body_for_log(resp)
    exc.blank_keys = blank
    exc.response_body = body
    try:
        body_text = body if isinstance(body, str) else json.dumps(body, default=str, ensure_ascii=False)
    except TypeError:
        body_text = str(body)
    body_text = _redact_text(body_text)[:_LOG_BODY_MAX]
    logger.warning(
        "CT Seller write failed method=%s path=%s sku=%s sale_id=%s ct_auction_id=%s blank_keys=%s fields=%s snapshot=%s status=%s body=%s",
        method.upper(),
        path,
        sku,
        estate_sale_id,
        auction or "-",
        ",".join(blank) or "-",
        _field_types(payload),
        _item_add_snapshot(payload),
        getattr(resp, "status_code", None) or exc.status_code or "-",
        body_text,
    )


def format_ctseller_reserve_price(raw: float | None) -> float | str:
    """CT Seller ``reserveprice``: a positive number, or blank when there is no reserve."""
    if raw is None or raw == "":
        return ""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return value


def resolve_lot_reserve_price(lot: Any, sale: Any | None = None) -> float | None:
    """Per-lot reserve wins; otherwise the sale-wide default."""
    lot_val = getattr(lot, "reserve_price", None)
    if lot_val is not None:
        try:
            parsed = float(lot_val)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            return parsed
    if sale is None:
        return None
    sale_val = getattr(sale, "ctseller_reserve_price", None)
    if sale_val is None:
        return None
    try:
        return float(sale_val)
    except (TypeError, ValueError):
        return None


def encode_sale_cover_jpeg(
    raw: bytes,
    *,
    max_edge: int = COVER_MAX_EDGE,
    quality: int = COVER_JPEG_QUALITY,
    square: bool = True,
) -> bytes:
    """Decode any catalog still (including HEIC) to a small JPEG. Never send raw originals.

    The seller.ctbids.com crop editor is 1:1 for the profile cover and the three
    secondary slots, so we center-crop to square unless ``square`` is false.
    """
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    if square:
        width, height = img.size
        side = min(width, height)
        if side < 1:
            raise ValueError("cover photo has no pixels")
        left = (width - side) // 2
        top = (height - side) // 2
        img = img.crop((left, top, left + side, top + side))
    width, height = img.size
    edge = max(width, height)
    cap = max(320, int(max_edge))
    if edge > cap:
        scale = cap / float(edge)
        img = img.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=max(40, min(int(quality), 92)), optimize=True)
    return buf.getvalue()


def sale_cover_base64(raw: bytes, *, max_edge: int = COVER_MAX_EDGE) -> str:
    """Raw JPEG base64 — same encoding seller.ctbids.com ``setImage`` submits."""
    try:
        jpeg = encode_sale_cover_jpeg(raw, max_edge=max_edge)
    except Exception as exc:  # noqa: BLE001
        raise CTSellerError("One cover photo could not be read as an image") from exc
    return base64.b64encode(jpeg).decode("ascii")


def pick_profile_sale_image(rows: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Prefer the ``profile`` sale-image row (primary + 3 secondaries)."""
    items = [row for row in (rows or []) if isinstance(row, dict)]
    for row in items:
        if str(row.get("name") or "").strip().lower() == "profile":
            return row
    return items[0] if items else None


def parse_sale_image_secondary_urls(raw: Any) -> list[str]:
    """``itemimageurl`` arrives as a list, JSON string, or Python-repr list."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if parsed is None:
        try:
            import ast

            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return []
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def jpeg_bytes_for_ctseller_post(path: Path) -> bytes:
    """1600px JPEG for item/image/add. Cache beside the catalog file so a sale is not re-encoded."""
    src = Path(path)
    cache = src.with_name(f"{src.stem}.ctseller.{IMAGE_MAX_EDGE}.jpg")
    try:
        if cache.is_file() and src.is_file() and cache.stat().st_mtime >= src.stat().st_mtime:
            return cache.read_bytes()
    except OSError:
        pass
    from app.services.thumbnail_image import thumbnail_jpeg_bytes

    try:
        body = thumbnail_jpeg_bytes(src, IMAGE_MAX_EDGE, fast=True)
    except Exception:
        return src.read_bytes()
    try:
        cache.write_bytes(body)
    except OSError:
        pass
    return body


def _image_to_base64(raw: bytes, *, mime: str = "image/jpeg") -> str:
    """Return raw base64 (no data: prefix), matching CT Seller processAddImage."""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            fmt = (img.format or "").upper()
            w, h = img.size
            edge = max(w, h)
            # Already a listing-sized JPEG from jpeg_bytes_for_ctseller_post — do not decode pixels again.
            if fmt == "JPEG" and edge <= IMAGE_MAX_EDGE:
                return base64.b64encode(raw).decode("ascii")
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")
            w, h = img.size
            edge = max(w, h)
            if edge > IMAGE_MAX_EDGE:
                img.thumbnail((IMAGE_MAX_EDGE, IMAGE_MAX_EDGE), Image.Resampling.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=IMAGE_JPEG_QUALITY, optimize=False)
            return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001 — fall back to original bytes
        return base64.b64encode(raw).decode("ascii")


def build_item_payload(
    *,
    sale_id: int,
    location_id: Any,
    sku: str,
    title: str,
    description: str,
    category: str,
    condition: str,
    starting_price: float | None,
    item_receipt_method: dict[str, bool] | str | None,
    area: str | None = None,
    allowed_areas: list[str] | None = None,
    tax: float | str | None = None,
    buyers_premium: float | str | None = None,
    material: str | None = None,
    tag: str | list[str] | None = None,
    reserve_price: float | None = None,
) -> dict[str, Any]:
    """Build the item/add ``data`` object from catalog fields."""
    irm: dict[str, bool]
    if isinstance(item_receipt_method, str) and item_receipt_method.strip():
        try:
            parsed = json.loads(item_receipt_method)
            irm = parsed if isinstance(parsed, dict) else {"pickup": True, "delivery": False, "shipping": False}
        except json.JSONDecodeError:
            irm = {"pickup": True, "delivery": False, "shipping": False}
    elif isinstance(item_receipt_method, dict):
        irm = {
            "pickup": bool(item_receipt_method.get("pickup")),
            "delivery": bool(item_receipt_method.get("delivery")),
            "shipping": bool(item_receipt_method.get("shipping")),
        }
    else:
        irm = {"pickup": True, "delivery": False, "shipping": False}
    # A receipt method with every flag false fails item/add. Pickup is the sale default.
    if not any(irm.values()):
        irm["pickup"] = True

    price = float(starting_price) if starting_price is not None and starting_price > 0 else 1.0
    cat = canonicalize_ct_category(category)
    # Item detail returns receipt method as a JSON string. item/add expects the object
    # the seller form posts: {pickup, delivery, shipping}.

    area_out = (area or "").strip() or infer_ct_area(cat, allowed_areas)

    def _number_or_none(raw: float | str | None) -> float | None:
        """Seller form parseFloat: a number, or null when the field is blank."""
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    tag_evidence = f"{title or ''} {description or ''}"
    if isinstance(tag, (list, tuple)):
        tag_out = format_ctseller_tag_field(tag, evidence=tag_evidence)
    else:
        tag_out = format_ctseller_tag_field(
            [p.strip() for p in str(tag or "").split(",") if p.strip()],
            evidence=tag_evidence,
        )

    tax_out = _number_or_none(tax)
    bp_out = _number_or_none(buyers_premium)
    # SPA requires tax % when the item is taxable; copy sale-level tax when present.
    is_taxable = tax_out is not None
    reserve = format_ctseller_reserve_price(reserve_price)
    reserve_out = float(reserve) if isinstance(reserve, (int, float)) and reserve > 0 else None

    # ``id: null`` and ``itemtypeid`` 2 are required on create, or CT Seller returns
    # "Not found : Update key or data missing".
    # SKU 1000 item/add still 400'd after quantity was 1 and empty price strings were
    # omitted (3:33 PM PT Nov 21). The seller form posts tax and buyers premium as
    # numbers, receipt method as an object, and blank prices/dimensions as null.
    # Empty strings fail with "The required field data for the requested operation
    # is not met" and no field name. Quantity is one lot. Specialty shipping is a checkbox.
    # The seller form forces isshippable false on save. Shipping is only the receipt-method
    # flag. isshippable true with null weight and fees is rejected as missing required data.
    # The March 2026 seller app deleted blank goal and valuation and did not send material,
    # fragility, or specialty shipping. The Nov 20 app sends those as null, and item/add
    # rejects that null data (SKU 3003, 3:45 PM PT). Leave them off unless they have a value.
    # Auction 50050 allows shipping. The 3:51 PM PT retry still sent shippingfee and
    # handlingfee as null. A shippable receipt method needs those as numbers.
    out: dict[str, Any] = {
        "id": None,
        "saleid": int(sale_id),
        "locationid": location_id,
        "SKU": (sku or "").strip()[:20],
        "title": (title or "").strip()[:500] or (sku or "Lot"),
        "description": (description or "").strip(),
        "category": cat or CT_SELLER_CATEGORY_DEFAULT,
        "area": area_out,
        "condition": canonicalize_ct_condition(condition),
        "itemstatus": "Pending",
        "itemtypeid": 2,  # Auction (same as SPA-created lots)
        "startingprice": price,
        "istaxable": is_taxable,
        "isshippable": False,
        "isfeatured": False,
        "ispublic": True,
        "itemreceiptmethod": irm,
        "closingorder": 999,
        "tag": tag_out,
        "quantity": 1,
        "tax": tax_out,
        "buyerspremium": bp_out,
        "reserveprice": reserve_out,
        "buynowprice": None,
        "width": None,
        "height": None,
        "length": None,
        "weight": None,
        "shippingfee": 0.0 if irm.get("shipping") or irm.get("delivery") else None,
        "handlingfee": 0.0 if irm.get("shipping") or irm.get("delivery") else None,
    }
    material_out = (material or "").strip()
    if material_out:
        out["material"] = material_out
    # id must stay null. Every other null is blank data CT Seller rejects.
    return {key: value for key, value in out.items() if value is not None or key == "id"}
