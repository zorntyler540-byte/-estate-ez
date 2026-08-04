"""Unofficial CT Seller (seller.ctbids.com) API client for posting lots from the site."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
from dataclasses import dataclass
from typing import Any

import requests
from PIL import Image, ImageOps

from app.config import settings
from app.services.ctseller_categories import (
    CT_SELLER_CATEGORY_DEFAULT,
    canonicalize_ct_category,
    infer_ct_area,
)
from app.services.ctseller_conditions import canonicalize_ct_condition

logger = logging.getLogger(__name__)

SELLER_API = "https://seller.ctbids.com/services/api/v1"
DEFAULT_TIMEOUT = 60.0
IMAGE_MAX_EDGE = 1600
IMAGE_JPEG_QUALITY = 85


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
        new_token = (body.get("newToken") or body.get("accessToken") or "").strip()
        if not new_token:
            raise CTSellerError("CT Seller refresh did not return a token")
        self.set_token(new_token)
        return new_token

    def list_open_sales(self) -> list[CTSellerSaleRow]:
        """
        Sales still open for cataloging — same idea as CT Seller Sales list \"active\":
        not deleted and endtime still in the future (includes Pending / not-yet-started).
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        base_filter = [
            {"field": "isDeleted", "value": 1, "op": "!=", "join": "AND"},
            # Match SaleList \"active\" tab: endtime > now (Pending + Started).
            {"field": "endtime", "value": now, "op": ">", "join": "AND"},
        ]
        fields = ["id", "title", "status", "starttime", "endtime", "tax", "buyerspremium"]
        out: list[CTSellerSaleRow] = []
        seen: set[int] = set()
        next_key: str | None = None
        for _ in range(15):
            page: dict[str, Any] = {"size": 100}
            if next_key:
                page["next"] = next_key
            payload: dict[str, Any] = {
                "filter": base_filter,
                "field": fields,
                "sort": [{"field": "starttime", "direction": "asc"}],
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
                # Skip fully closed/inactive if API still returns them.
                if status and status.lower() in ("closed", "inactive"):
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


def _image_to_base64(raw: bytes, *, mime: str = "image/jpeg") -> str:
    """Return raw base64 (no data: prefix), matching CT Seller processAddImage."""
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")
        w, h = img.size
        edge = max(w, h)
        if edge > IMAGE_MAX_EDGE:
            scale = IMAGE_MAX_EDGE / float(edge)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=IMAGE_JPEG_QUALITY, optimize=True)
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

    price = float(starting_price) if starting_price is not None and starting_price > 0 else 1.0
    cat = canonicalize_ct_category(category)
    # CT Seller stores receipt method as a JSON string on items (see /item/detail).
    if isinstance(item_receipt_method, str) and item_receipt_method.strip().startswith("{"):
        irm_out = item_receipt_method.strip()
    else:
        irm_out = json.dumps(irm, separators=(",", ":"))

    area_out = (area or "").strip() or infer_ct_area(cat, allowed_areas)

    def _pct(raw: float | str | None) -> str:
        if raw is None or raw == "":
            return ""
        try:
            return f"{float(raw):.3f}"
        except (TypeError, ValueError):
            return ""

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
        "tag": "",
        "quantity": "",
        "tax": tax_out,
        "buyerspremium": bp_out,
        "reserveprice": "",
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
