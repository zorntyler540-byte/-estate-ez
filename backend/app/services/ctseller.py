"""Unofficial CT Seller (seller.ctbids.com) API client for posting lots from the site."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import math
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

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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
            {"Content-Type": "application/json", "Accept": "application/json"}
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
        if resp.status_code >= 400:
            raise CTSellerError(
                _message_from_response(resp) or "CT Seller login failed",
                status_code=resp.status_code,
            )
        body = resp.json() if resp.content else {}
        access = (body.get("accessToken") or body.get("access_token") or "").strip()
        refresh = (body.get("refreshToken") or body.get("refresh_token") or "").strip()
        if not access:
            raise CTSellerError("CT Seller login did not return an access token")
        self.set_token(access)
        return CTSellerTokens(access_token=access, refresh_token=refresh)

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
        """Set ``reserveprice`` on an existing item without re-posting photos."""
        try:
            row = self.item_detail(int(item_id))
        except CTSellerError:
            row = {"id": int(item_id)}
        if not isinstance(row, dict):
            row = {}
        row["id"] = int(item_id)
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
        if not self.access_token:
            raise CTSellerError("Not signed in to CT Seller", status_code=401)
        resp = self.session.delete(
            f"{SELLER_API}{path}",
            data=json.dumps(payload),
            timeout=self.timeout,
        )
        return self._parse_seller_response(resp)

    def _put_raw(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.access_token:
            raise CTSellerError("Not signed in to CT Seller", status_code=401)
        resp = self.session.put(
            f"{SELLER_API}{path}",
            data=json.dumps(payload),
            timeout=self.timeout,
        )
        return self._parse_seller_response(resp)

    def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        body = self._post_raw(path, payload)
        return body.get("data")

    def _post_raw(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.access_token:
            raise CTSellerError("Not signed in to CT Seller", status_code=401)
        resp = self.session.post(
            f"{SELLER_API}{path}",
            data=json.dumps(payload),
            timeout=self.timeout,
        )
        return self._parse_seller_response(resp)

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
        if status and status not in ("success", "ok", True) and body.get("message"):
            raise CTSellerError(_coerce_message(body.get("message")) or "CT Seller error")
        return body


def _message_from_response(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return (resp.text or "")[:300]
    if isinstance(body, dict):
        return _coerce_message(body.get("message")) or (resp.text or "")[:300]
    return (resp.text or "")[:300]


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
    if message is None:
        return ""
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, list) and message:
        first = message[0]
        if isinstance(first, dict):
            return str(first.get("message") or first.get("msg") or first).strip()
        return str(first).strip()
    return str(message).strip()


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


# New CT Seller items are always pickup and shipping. Staff change that on the
# item in seller.ctbids.com. The sale's receipt method is not copied, and a
# missing sale setting must not become pickup only.
POSTED_ITEM_RECEIPT_METHOD: dict[str, bool] = {
    "pickup": True,
    "delivery": False,
    "shipping": True,
}


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
    """Build the item/add ``data`` object from catalog fields.

    ``item_receipt_method`` is accepted so callers stay stable, and ignored.
    Every new item is pickup and shipping.
    """
    irm = dict(POSTED_ITEM_RECEIPT_METHOD)

    price = float(starting_price) if starting_price is not None and starting_price > 0 else 1.0
    cat = canonicalize_ct_category(category)
    # CT Seller stores receipt method as a JSON string on items (see /item/detail).
    irm_out = json.dumps(irm, separators=(",", ":"))

    area_out = (area or "").strip() or infer_ct_area(cat, allowed_areas)

    def _pct(raw: float | str | None) -> str:
        if raw is None or raw == "":
            return ""
        try:
            return f"{float(raw):.3f}"
        except (TypeError, ValueError):
            return ""

    tag_evidence = f"{title or ''} {description or ''}"
    if isinstance(tag, (list, tuple)):
        tag_out = format_ctseller_tag_field(tag, evidence=tag_evidence)
    else:
        tag_out = format_ctseller_tag_field(
            [p.strip() for p in str(tag or "").split(",") if p.strip()],
            evidence=tag_evidence,
        )

    tax_out = _pct(tax)
    bp_out = _pct(buyers_premium)
    # SPA requires tax % when the item is taxable; copy sale-level tax when present.
    is_taxable = bool(tax_out)
    ships = bool(irm.get("shipping"))

    # Keep this lean: extra null fields are ok, but ``id: null`` and ``itemtypeid`` are required
    # or CT Seller returns "Not found : Update key or data missing".
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
        "isshippable": ships,
        "isfeatured": False,
        "ispublic": True,
        "itemreceiptmethod": irm_out,
        "closingorder": 999,
        "tag": tag_out,
        "quantity": "",
        "tax": tax_out,
        "buyerspremium": bp_out,
        "reserveprice": format_ctseller_reserve_price(reserve_price),
        "buynowprice": "",
        "width": "",
        "height": "",
        "length": "",
        "weight": "",
        "shippingfee": "",
        "handlingfee": "",
        "goal": "",
        "valuation": "",
        "fragilityscore": None,
        "material": (material or "").strip() or None,
        "specialtyshippeditems": "",
    }
    return out
